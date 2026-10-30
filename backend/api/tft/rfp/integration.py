"""조립품 통합 책임(KAN-186, RFP API 명세 v1.3 §7, 기획서 최종본 V_1 4.8). DB 없이 테스트한다.

유닛 변전소처럼 여러 장비를 한 조립품으로 공급하는 요구에서는 장비별 후보가 있어도, 조립·공장 시험·연동·보증을
맡을 통합 책임자가 확인되기 전에는 유닛 공급 가능으로 표시하지 않는다. 시스템의 조립·시험·보증은 제안 주체
(제조사)의 책임이다.

- 판별(`required`): 요구 과업이 조립·공장 시험·연동·보증이거나, 사람이 조립품 공급 조항으로 표시한 요구(14번 2.3 A,
  2.5 신호선 사전 결선, 2.9 B 공장 시험)가 하나라도 있으면 참이다. 서버 계산값이다.
- 확인 순서(미확보 시 처리 경로): ① 자사 통합(OWN) ② 유닛 전체를 공급·통합할 단일 협력사(SINGLE_PARTNER, 자사는
  기기 납품) ③ 공동 제안(JOINT) 또는 참여 보류. 책임자(`holder`)는 사람이 정한다(human_input).
- 검토본 저장: 필요한데 `UNRESOLVED`면 422 `TFT_DRAFT_INCOMPLETE`(details.integration_responsibility=UNRESOLVED).
"""

from collections.abc import Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

Holder = Literal["OWN", "SINGLE_PARTNER", "JOINT", "UNRESOLVED"]
# 조립품 공급을 뜻하는 과업(명세 v1.3 §7).
ASSEMBLY_TASKS = frozenset({"ASSEMBLY", "FACTORY_TEST", "INTERCONNECTION", "WARRANTY"})
CHECK_ITEM = "통합 책임자 확인(조립·공장 시험·연동·보증)"
HOLDER_TEXT: dict[Holder, str] = {
    "OWN": "자사",
    "SINGLE_PARTNER": "단일 협력사(유닛 일괄 공급·통합)",
    "JOINT": "공동 제안",
    "UNRESOLVED": "미확보",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Requirement(Strict):
    """통합 책임 판별에 쓰는 요구 칸. assembly_clause는 사람이 조립품 공급 조항으로 표시한 요구다."""

    clause_no: str
    task: str | None = None
    assembly_clause: bool = False


class StoredRequirement(Requirement):
    """저장된 최신 요구 판본. 근거 조항을 화면에서 열 수 있게 조항 ID를 함께 둔다."""

    clause_id: UUID


class FallbackPath(Strict):
    holder: Holder | Literal["HOLD"]
    condition: str


class IntegrationResponsibility(Strict):
    required: bool
    holder: Holder
    basis_clause_nos: list[str]
    # 필수 확인 항목 목록에 "통합 책임자 확인"이 올라갔는지. 필요하면 항상 참이다.
    check_item_listed: bool
    # 책임자가 미확보일 때 보여 줄 처리 경로(확인 순서). 확보됐거나 필요 없으면 빈 목록이다.
    fallback_paths: list[FallbackPath]
    unresolved: list[str]
    # 초안 ASSUMPTIONS_EXCLUSIONS에 서버가 넣는 문장. 필요 없으면 None.
    assumption_sentence: str | None
    review_save_blocked: bool


FALLBACKS = (
    FallbackPath(holder="OWN", condition="자사가 조립·시험·연동·보증을 맡을 수 있는지 확인"),
    FallbackPath(
        holder="SINGLE_PARTNER",
        condition="유닛 전체를 공급·통합할 단일 협력사 확인(자사는 기기 납품 검토)",
    ),
    FallbackPath(holder="JOINT", condition="공동 제안 역할·책임 경계 확인"),
    FallbackPath(holder="HOLD", condition="통합 책임자를 확보하지 못하면 참여 보류 검토"),
)


def _is_basis(requirement: Requirement) -> bool:
    """요구가 조립품 공급의 근거인지(사람·추출이 표시한 조항이거나 조립품 과업)."""
    return requirement.assembly_clause or requirement.task in ASSEMBLY_TASKS


def integration_responsibility(
    requirements: Sequence[Requirement], holder: Holder = "UNRESOLVED"
) -> IntegrationResponsibility:
    """프로젝트 요구에서 통합 책임 확인이 필요한지와 미확보 시 처리 경로를 정한다.

    Args:
        requirements: 같은 프로젝트의 최신 요구 전부.
        holder: 사람이 정한 통합 책임자. 기본은 미확보다.

    Returns:
        IntegrationResponsibility: 필요 여부와 근거 조항, 필수 확인 항목, 처리 경로, 초안 전제 문장, 저장 차단 여부.
    """
    basis = sorted({r.clause_no for r in requirements if _is_basis(r)})
    required = bool(basis)
    if not required:
        return IntegrationResponsibility(
            required=False,
            holder=holder,
            basis_clause_nos=[],
            check_item_listed=False,
            fallback_paths=[],
            unresolved=[],
            assumption_sentence=None,
            review_save_blocked=False,
        )
    unresolved = holder == "UNRESOLVED"
    return IntegrationResponsibility(
        required=True,
        holder=holder,
        basis_clause_nos=basis,
        check_item_listed=True,
        fallback_paths=list(FALLBACKS) if unresolved else [],
        unresolved=[CHECK_ITEM] if unresolved else [],
        assumption_sentence=(
            f"유닛 전체의 조립·공장 시험·연동·보증을 맡을 통합 책임자: {HOLDER_TEXT[holder]}"
        ),
        review_save_blocked=unresolved,
    )


def project_integration(
    requirements: Sequence[StoredRequirement], holder: Holder
) -> dict[str, Any]:
    """저장된 요구로 프로젝트 응답의 서버 계산 칸을 만든다. 값은 저장하지 않고 읽을 때 계산한다(명세 v1.3 §7).

    Args:
        requirements: 프로젝트의 최신 요구 판본 전부.
        holder: 사람이 정한 통합 책임자.

    Returns:
        dict: required·basis_clause_ids·basis_clause_nos·fallback_paths·unresolved. 두 근거 목록은 같은 길이·순서로
            조항 하나씩 대응한다(조항 번호 순). 문서가 달라 번호가 같은 조항은 근거인 조항만 내고, 둘 다 근거면 둘 다 낸다.
    """
    result = integration_responsibility(requirements, holder)
    basis = sorted(
        {(r.clause_no, r.clause_id) for r in requirements if _is_basis(r)},
        key=lambda item: (item[0], str(item[1])),
    )
    return {
        "required": result.required,
        "basis_clause_ids": [clause_id for _, clause_id in basis],
        "basis_clause_nos": [clause_no for clause_no, _ in basis],
        "fallback_paths": result.fallback_paths,
        "unresolved": result.unresolved,
    }
