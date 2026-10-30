"""R&D 확인 흐름(KAN-186, RFP API 명세 v1.3 §5.2·§5.3). DB·HTTP 없이 테스트한다.

보완 경로는 자사 제품 → 자사 개발(R&D 확인) → 협력사 순이다. 이 모듈은 그 사이의 규칙만 둔다. 저장과 HTTP
연결은 A07(KAN-188)·A08(KAN-189)이 이 함수를 부른다.

- `rnd_check_of`: 요구의 R&D 확인 상태를 R&D 질문·답변·면제에서 계산한다.
- `rnd_question_for`: `OWN_DEVELOPMENT`를 고르면 서버가 만들 내부 자문 질문(검토 부서 중 분류 RND인 첫 부서).
- `check_selection`: 막힌 경로(409)와 열린 R&D 질문이 있는 협력사 경로(면제 사유 없으면 422)를 거른다.
- `check_reply_fields`: R&D 전용 답변 필드는 R&D 부서 대상 내부 질문에만 받는다.

영업 의견과 R&D 답변이 다르면 둘 다 기록으로 남고 담당 sales·책임자의 변경 제안 승인이 결정이다. 별도 충돌
상태나 차단은 두지 않는다(명세 결정 13).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from backend.api.tft.rfp.supplement import PARTNER, Option, RndCheck
from backend.shared.errors import ApplicationError

DeptKind = Literal["SALES", "RND", "TECH", "PURCHASE", "OTHER"]
Feasibility = Literal["FEASIBLE", "FEASIBLE_WITH_SCHEDULE", "NOT_FEASIBLE", "UNDETERMINED"]
WAIVER_MIN = 20


@dataclass(frozen=True)
class RndQuestion:
    """요구에 달린 R&D 자문 질문 하나(issue_type=RND_FEASIBILITY)와 그 답변."""

    question_id: UUID
    created_at: datetime
    replied: bool
    feasibility: Feasibility | None = None
    schedule_fits: bool | None = None


def dept_kind(dept_code: str, kinds: Mapping[str, DeptKind]) -> DeptKind:
    """부서 분류(tft.department_kinds). 매핑이 없는 부서는 TECH로 본다(명세 v1.3 §3)."""
    return kinds.get(dept_code, "TECH")


def rnd_check_of(
    questions: Sequence[RndQuestion], waived_at: datetime | None = None
) -> tuple[RndCheck, UUID | None]:
    """요구의 R&D 확인 상태와 기준이 된 질문(열린 질문, 없으면 마지막 질문)을 계산한다.

    Args:
        questions: 그 요구의 R&D 자문 질문 전부.
        waived_at: 면제 사유와 함께 협력사 경로를 고른 시각. 그 뒤에 새 R&D 질문이 없으면 WAIVED다.

    Returns:
        tuple: (rnd_check, rnd_question_id). 질문이 없으면 (NOT_REQUIRED 또는 WAIVED, None).
    """
    if not questions:
        return ("WAIVED" if waived_at else "NOT_REQUIRED"), None
    latest = max(questions, key=lambda q: q.created_at)
    if waived_at is not None and waived_at >= latest.created_at:
        # 면제 뒤 새 질문을 올리지 않았으면 면제가 유효하다.
        return "WAIVED", latest.question_id
    open_questions = [q for q in questions if not q.replied]
    if open_questions:
        return "PENDING", max(open_questions, key=lambda q: q.created_at).question_id
    status: RndCheck
    if latest.feasibility == "FEASIBLE":
        status = "CONFIRMED_FEASIBLE"
    elif latest.feasibility == "NOT_FEASIBLE":
        status = "CONFIRMED_NOT_FEASIBLE"
    elif latest.feasibility == "FEASIBLE_WITH_SCHEDULE" and latest.schedule_fits is not None:
        status = "CONFIRMED_FEASIBLE" if latest.schedule_fits else "CONFIRMED_SCHEDULE_MISMATCH"
    else:
        # 답은 왔지만 판단 불가(UNDETERMINED)거나 일정 여부가 없으면 아직 확인 중이다.
        status = "PENDING"
    return status, latest.question_id


def rnd_question_for(
    requirement_summary: str,
    clause_no: str | None,
    review_departments: Sequence[str],
    kinds: Mapping[str, DeptKind],
) -> dict[str, str]:
    """`OWN_DEVELOPMENT` 선택 때 서버가 만들 내부 자문 질문.

    Args:
        requirement_summary: 요구 요약(장비·속성·값). 질문 본문에 그대로 쓴다.
        clause_no: 원문 조항 번호.
        review_departments: 프로젝트 검토 부서 목록(순서대로 본다).
        kinds: 부서 분류.

    Returns:
        dict: target·target_dept·issue_type·question_text. 요구 상태는 바꾸지 않고, 호출자가 next_owner_field=RND,
            rnd_check=PENDING으로 둔다. 검토 부서에 R&D가 없으면 422 VALIDATION_ERROR.
    """
    target = next((d for d in review_departments if dept_kind(d, kinds) == "RND"), None)
    if target is None:
        raise ApplicationError(
            422,
            "VALIDATION_ERROR",
            "검토 부서에 R&D 부서를 추가하세요.",
            details=[{"field": "review_departments", "reason": "no RND department"}],
        )
    where = f"원문 {clause_no} " if clause_no else ""
    return {
        "target": "INTERNAL",
        "target_dept": target,
        "issue_type": "RND_FEASIBILITY",
        "question_text": (
            f"{where}요구 '{requirement_summary}'를 자사 개발로 대응할 수 있는지, 가능하면 시점과 요구 납기에 "
            "맞는지 알려 주십시오."
        ),
    }


def check_selection(
    option: Option | None,
    rnd_check: RndCheck,
    waiver_reason: str | None,
    rnd_question_id: UUID | None,
) -> RndCheck:
    """보완 경로 선택을 검사하고 선택 판본에 남길 R&D 확인 상태를 돌려준다.

    Args:
        option: 고른 경로의 선택지(supplement.options_for 결과). 없으면 선택지에 없는 경로다.
        rnd_check: 요구의 현재 R&D 확인 상태.
        waiver_reason: 면제 사유(20자 이상).
        rnd_question_id: 열린 R&D 질문.

    Returns:
        RndCheck: 면제로 넘겼으면 WAIVED, 아니면 그대로. 막힌 경로나 선택지에 없는 경로는 409
            TFT_INVALID_TRANSITION, 열린 R&D 질문이 있는 협력사 경로를 사유 없이 고르면 422 TFT_RND_CHECK_REQUIRED.
    """
    if option is None or not option.allowed:
        raise ApplicationError(
            409,
            "TFT_INVALID_TRANSITION",
            "This supplement path cannot be chosen for the requirement.",
        )
    gated = option.path in PARTNER and option.gate.requires == "OWN_DEVELOPMENT_CHECK"
    if gated and rnd_check == "PENDING":
        if waiver_reason is None or len(waiver_reason.strip()) < WAIVER_MIN:
            raise ApplicationError(
                422,
                "TFT_RND_CHECK_REQUIRED",
                "Confirm own development with R&D first, or give a waiver reason.",
                details=[{"field": "rnd_check_waiver_reason", "reason": str(rnd_question_id)}],
            )
        # 초안 ASSUMPTIONS에 "자사 개발 가능성 미확인 상태에서 협력사 경로 선택"이 생긴다(KAN-188).
        return "WAIVED"
    return rnd_check


def check_reply_fields(
    target: Literal["CUSTOMER", "INTERNAL"],
    target_dept: str | None,
    kinds: Mapping[str, DeptKind],
    feasibility: Feasibility | None,
    earliest_available_on: date | None,
    schedule_fits: bool | None,
) -> None:
    """R&D 전용 답변 필드는 분류 RND 부서 대상 내부 질문에만 받는다.

    Raises:
        ApplicationError: 422 TFT_QUESTION_FIELD_NOT_ALLOWED. 일정 칸만 있고 개발 가능성이 없으면 422
            VALIDATION_ERROR.
    """
    given = any(v is not None for v in (feasibility, earliest_available_on, schedule_fits))
    if not given:
        return
    if target != "INTERNAL" or target_dept is None or dept_kind(target_dept, kinds) != "RND":
        raise ApplicationError(
            422,
            "TFT_QUESTION_FIELD_NOT_ALLOWED",
            "R&D answer fields are only for internal questions to an R&D department.",
        )
    if feasibility is None:
        raise ApplicationError(
            422, "VALIDATION_ERROR", "Give rnd_feasibility with the schedule fields."
        )
