"""부족 원인별 보완 경로(KAN-186 A05, 기획서 최종본 V_1 4.4, RFP API 명세 v1.3 §5). DB 없이 테스트한다.

보는 순서는 path_rank다: 1 자사 계열(다른 자사 제품, 자사 조건 변경 검토) → 2 자사 개발·수행 확인(R&D) →
3 협력사 계열(외부 제품·과업 위탁·공동 제안·대체 사양) → 4 고객 지정품 조달 → 9 경로 없음. 협력사는 자사 대응
가능성 확인보다 앞서지 않는다. 같은 순위 안에서는 부족 원인별 "먼저 보는 경로" 표의 순서를 따른다.

- 협력사 계열 경로는 자사 개발 확인을 거친다(gate.requires=OWN_DEVELOPMENT_CHECK). R&D가 개발 가능하다고
  확인했으면(CONFIRMED_FEASIBLE) 협력사 경로는 allowed=false(OWN_DEVELOPMENT_FEASIBLE)다.
- 지정품 요구는 협력사 보완과 섞지 않는다: 지정품 조달과 대체 사양(고객 승인)만 고를 수 있고 나머지는
  allowed=false(DESIGNATED_ITEM)로 이유를 보여 준다.
- 외부 조달·과업 위탁·공동 제안이 RFP에서 금지되면 allowed=false와 사유로 남겨 추천하지 않고, 허용 여부가
  적혀 있지 않으면 확인 조건으로 남긴다. 고를 경로가 하나도 없으면 NONE(보류 의견)이다.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.api.tft.rfp.comparison import Cause, Outcome

Path = Literal[
    "OTHER_OWN_PRODUCT",
    "OWN_DEVELOPMENT",
    # 명세 v1.3에 추가 요청 중(KAN-186 댓글). 상업 조건을 협력사로 풀지 않는 자사 계열 경로다.
    "OWN_TERMS_REVIEW",
    "ALTERNATIVE_SPEC",
    "EXTERNAL_PRODUCT",
    "TASK_SUBCONTRACT",
    "JOINT_PROPOSAL",
    "CUSTOMER_SPECIFIED_PROCUREMENT",
    "NONE",
]
Permission = Literal["ALLOWED", "FORBIDDEN", "UNSTATED"]
RndCheck = Literal[
    "NOT_REQUIRED",
    "PENDING",
    "CONFIRMED_FEASIBLE",
    "CONFIRMED_NOT_FEASIBLE",
    "CONFIRMED_SCHEDULE_MISMATCH",
    "WAIVED",
]

# 명세 v1.3 §2 path_rank. OWN_TERMS_REVIEW는 자사 계열(1)로 제안했다.
RANK: dict[Path, int] = {
    "OTHER_OWN_PRODUCT": 1,
    "OWN_TERMS_REVIEW": 1,
    "OWN_DEVELOPMENT": 2,
    "EXTERNAL_PRODUCT": 3,
    "TASK_SUBCONTRACT": 3,
    "JOINT_PROPOSAL": 3,
    "ALTERNATIVE_SPEC": 3,
    "CUSTOMER_SPECIFIED_PROCUREMENT": 4,
    "NONE": 9,
}
# 협력사 계열: 자사 개발 확인을 거치는 경로(명세 v1.3 §5.2).
PARTNER: frozenset[Path] = frozenset(
    {"EXTERNAL_PRODUCT", "TASK_SUBCONTRACT", "JOINT_PROPOSAL", "ALTERNATIVE_SPEC"}
)
# 자사 개발로 풀 수 없는 원인: 계약 조건(보증 등)과 고객 지정품. 이 원인의 대체 제안에는 R&D 게이트를 걸지 않는다
# (#299 리뷰 P3, 2026-10-30).
NOT_RND: frozenset[Cause] = frozenset({"COMMERCIAL", "DESIGNATED_MODEL"})
# 부족 원인별 "먼저 보는 경로"(기획서 최종본 V_1 4.4 표). 응답은 이 순서를 RANK로 다시 정렬한다.
FIRST_PATHS: dict[Cause, tuple[Path, ...]] = {
    "RATING": ("OTHER_OWN_PRODUCT", "OWN_DEVELOPMENT", "EXTERNAL_PRODUCT"),
    "CERTIFICATION": ("OTHER_OWN_PRODUCT", "EXTERNAL_PRODUCT", "ALTERNATIVE_SPEC"),
    "FORM": ("OWN_DEVELOPMENT", "EXTERNAL_PRODUCT", "ALTERNATIVE_SPEC"),
    # 계약 범위 확인은 각 경로의 확인 조건이다(자사 수행 → 과업 위탁).
    "TASK": ("OWN_DEVELOPMENT", "TASK_SUBCONTRACT"),
    # 협력사로 풀지 않는다: 자사 조건 변경 검토 → 대체 조건 제안(Deviation).
    "COMMERCIAL": ("OWN_TERMS_REVIEW", "ALTERNATIVE_SPEC"),
    # 자사 자료 보완 요청은 경로가 아니라 다음 행동(미확인 항목)이다. 공동 제안 → 보류 의견.
    "CORE_EQUIPMENT_MISSING": ("JOINT_PROPOSAL", "NONE"),
    # 지정품 조달과 대체 제안(고객 승인)만 고른다. 차단 경로는 아래 DESIGNATED_BLOCKED로 함께 보여 준다.
    "DESIGNATED_MODEL": ("CUSTOMER_SPECIFIED_PROCUREMENT", "ALTERNATIVE_SPEC"),
}
DESIGNATED_BLOCKED: tuple[Path, ...] = (
    "OTHER_OWN_PRODUCT",
    "OWN_DEVELOPMENT",
    "EXTERNAL_PRODUCT",
    "TASK_SUBCONTRACT",
    "JOINT_PROPOSAL",
)
# 고객 허용이 필요한 외부 경로. 대체 사양은 늘 고객 승인이 필요해 따로 조건을 단다.
EXTERNAL: frozenset[Path] = frozenset({"EXTERNAL_PRODUCT", "TASK_SUBCONTRACT", "JOINT_PROPOSAL"})
CONDITIONS: dict[Path, str] = {
    "OTHER_OWN_PRODUCT": "모델·구성·환경별 적용성과 추가 자료 확인",
    "OWN_DEVELOPMENT": "R&D 개발(과업이면 자사 수행) 가능 여부와 시점, 요구 납기와의 정합 확인",
    "OWN_TERMS_REVIEW": "영업·보증 담당의 자사 조건 변경 가능 여부 확인",
    "ALTERNATIVE_SPEC": "요구 변경 허용 여부와 기술 영향 확인(Deviation, 발주처 승인)",
    "EXTERNAL_PRODUCT": "외부 제품 인터페이스·보증 책임 확인",
    "TASK_SUBCONTRACT": "계약 범위와 과업 수행 조건 확인",
    "JOINT_PROPOSAL": "공동 참여 역할·책임 경계 확인",
    "CUSTOMER_SPECIFIED_PROCUREMENT": "지정품 조달 방식과 공급 범위 확인",
    "NONE": "필수 조건 유지 여부와 해결해야 할 조건 정리(보류 의견)",
}
EXPERTISE: dict[Path, str] = {
    "OTHER_OWN_PRODUCT": "자사 기술",
    "OWN_DEVELOPMENT": "R&D",
    "OWN_TERMS_REVIEW": "영업·보증",
    "ALTERNATIVE_SPEC": "기술영업·기술 검토",
    "EXTERNAL_PRODUCT": "구매",
    "TASK_SUBCONTRACT": "구매·시공",
    "JOINT_PROPOSAL": "사업 책임",
    "CUSTOMER_SPECIFIED_PROCUREMENT": "구매",
    "NONE": "사업 책임",
}
DESIGNATED_ITEM = "DESIGNATED_ITEM"
OWN_DEVELOPMENT_FEASIBLE = "OWN_DEVELOPMENT_FEASIBLE"


class Gate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requires: Literal["OWN_DEVELOPMENT_CHECK", "NONE"]
    status: RndCheck


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    rank: int
    gate: Gate
    allowed: bool
    conditions_to_confirm: list[str]
    required_expertise: list[str]
    blocked_reason: str | None


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deficiency_cause: Cause | None
    customer_permission: Permission
    options: list[Option]


def _option(
    path: Path,
    rnd_check: RndCheck,
    *,
    gated: bool = True,
    blocked: str | None = None,
    extra: list[str] | None = None,
) -> Option:
    gate = Gate(
        requires="OWN_DEVELOPMENT_CHECK" if gated and path in PARTNER else "NONE",
        status=rnd_check,
    )
    return Option(
        path=path,
        rank=RANK[path],
        gate=gate,
        allowed=blocked is None,
        conditions_to_confirm=[] if blocked else [CONDITIONS[path], *(extra or [])],
        required_expertise=[EXPERTISE[path]],
        blocked_reason=blocked,
    )


def options_for(
    outcome: Outcome,
    permission: Permission,
    designated: bool = False,
    rnd_check: RndCheck = "NOT_REQUIRED",
    approval_question: bool = False,
) -> Options:
    """비교 결과에 맞는 보완 경로를 path_rank 순으로 낸다.

    사양 차이, 타사 지정 모델(지정품), 핵심 기기 부재, 수행 과업만 경로를 계산한다. 그 밖의 근거 있음·조건부·근거
    부족은 경로가 아니라 다음 행동(조건 확인·자료 요청)이라 빈 목록이다.

    Args:
        outcome: `comparison.compare` 결과.
        permission: RFP의 외부 보완 허용 조항(허용·금지·미기재).
        designated: 타사 지정 모델 요구인지. outcome의 원인이 DESIGNATED_MODEL이어도 지정품으로 본다.
        rnd_check: 요구의 R&D 확인 상태(서버가 질문·답변에서 계산). 게이트 상태로 보이고,
            CONFIRMED_FEASIBLE이면 협력사 계열 경로를 막는다.
        approval_question: 지정품 대체 제안의 고객 승인 질문이 이미 있는지.

    Returns:
        Options: 원인·허용과 경로 목록.
    """
    cause = "DESIGNATED_MODEL" if designated else outcome.deficiency_cause
    if cause in ("DESIGNATED_MODEL", "CORE_EQUIPMENT_MISSING", "TASK"):
        # 지정품·핵심 기기 부재·수행 과업은 사양 차이가 아니어도 경로를 낸다(참여 판단에 필요한 다음 단계).
        paths = FIRST_PATHS[cause]
    elif outcome.status == "SPEC_GAP" and cause is not None:
        paths = FIRST_PATHS[cause]
    else:
        return Options(deficiency_cause=cause, customer_permission=permission, options=[])
    gated = cause not in NOT_RND
    options = []
    for path in paths:
        if path == "NONE":
            options.append(_option(path, rnd_check))
            continue
        extra = []
        blocked = None
        if gated and path in PARTNER and rnd_check == "CONFIRMED_FEASIBLE":
            # 자사 개발이 되는데 협력사로 가지 않는다. 바꾸려면 R&D 질문을 다시 올린다.
            blocked = OWN_DEVELOPMENT_FEASIBLE
        elif path in EXTERNAL and permission == "FORBIDDEN":
            blocked = "RFP가 외부 보완을 금지함"
        elif path in EXTERNAL and permission == "UNSTATED":
            # 적혀 있지 않으면 금지로도 허용으로도 보지 않고 확인 조건으로 남긴다.
            extra.append("RFP에 외부 보완 허용 여부가 없음: 발주처 확인")
        if cause == "DESIGNATED_MODEL" and path == "ALTERNATIVE_SPEC":
            extra.append("고객 승인")
            if not approval_question:
                extra.append("고객 질문 작성 필요")
        options.append(_option(path, rnd_check, gated=gated, blocked=blocked, extra=extra))
    if cause == "DESIGNATED_MODEL":
        # 지정품 요구의 다른 경로는 고를 수 없음을 이유와 함께 보여 준다.
        options += [
            _option(p, rnd_check, gated=False, blocked=DESIGNATED_ITEM) for p in DESIGNATED_BLOCKED
        ]
    # 고를 경로가 없으면 보류 의견을 덧붙인다(핵심 기기는 이미 NONE을 마지막에 둔다).
    if not any(o.allowed for o in options):
        options.append(_option("NONE", rnd_check))
    # path_rank 순. 같은 순위 안에서는 원인별 표의 순서(안정 정렬)를 지킨다.
    options.sort(key=lambda o: o.rank)
    return Options(deficiency_cause=cause, customer_permission=permission, options=options)
