"""부족 원인별 보완 경로(KAN-186 A05, 기획서 4.4). DB 없이 테스트한다.

모든 경로를 기계적으로 돌지 않는다. 부족한 원인(정격·인증·형식·과업)과 고객 허용 조건에 맞는 경로를
순서대로 낸다. 외부 조달·과업 위탁·공동 제안이 금지되면 그 경로는 allowed=false와 사유로 남겨 추천하지
않고, 허용 여부가 적혀 있지 않으면 확인 조건으로 남긴다. 고를 경로가 하나도 없으면 NONE(보류 의견)이다.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.api.tft.rfp.comparison import Cause, Outcome

Path = Literal[
    "OTHER_OWN_PRODUCT",
    "ALTERNATIVE_SPEC",
    "EXTERNAL_PRODUCT",
    "TASK_SUBCONTRACT",
    "JOINT_PROPOSAL",
    "NONE",
]
Permission = Literal["ALLOWED", "FORBIDDEN", "UNSTATED"]

# 기획서 4.4 "부족한 원인 먼저 보는 경로" 표의 순서.
FIRST_PATHS: dict[Cause, tuple[Path, ...]] = {
    "RATING": ("OTHER_OWN_PRODUCT", "EXTERNAL_PRODUCT"),
    "CERTIFICATION": ("OTHER_OWN_PRODUCT", "EXTERNAL_PRODUCT", "ALTERNATIVE_SPEC"),
    "FORM": ("EXTERNAL_PRODUCT", "ALTERNATIVE_SPEC"),
    "TASK": ("TASK_SUBCONTRACT",),
}
# 타사 지정 모델: 지정품 조달 → 대체 제안(고객 승인 전제).
DESIGNATED_PATHS: tuple[Path, ...] = ("EXTERNAL_PRODUCT", "ALTERNATIVE_SPEC")
# 고객 허용이 필요한 외부 경로. 대체 사양은 늘 고객 승인이 필요해 따로 조건을 단다.
EXTERNAL: frozenset[Path] = frozenset({"EXTERNAL_PRODUCT", "TASK_SUBCONTRACT", "JOINT_PROPOSAL"})
CONDITIONS: dict[Path, str] = {
    "OTHER_OWN_PRODUCT": "모델·구성·환경별 적용성과 추가 자료 확인",
    "ALTERNATIVE_SPEC": "요구 변경 허용 여부와 기술 영향 확인(Deviation, 발주처 승인)",
    "EXTERNAL_PRODUCT": "외부 제품 인터페이스·보증 책임 확인",
    "TASK_SUBCONTRACT": "계약 범위와 과업 수행 조건 확인",
    "JOINT_PROPOSAL": "공동 참여 역할·책임 경계 확인",
}
EXPERTISE: dict[Path, str] = {
    "OTHER_OWN_PRODUCT": "자사 기술",
    "ALTERNATIVE_SPEC": "기술영업·기술 검토",
    "EXTERNAL_PRODUCT": "구매",
    "TASK_SUBCONTRACT": "구매·시공",
    "JOINT_PROPOSAL": "사업 책임",
}


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    allowed: bool
    conditions_to_confirm: list[str]
    required_expertise: list[str]
    blocked_reason: str | None


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deficiency_cause: Cause | None
    customer_permission: Permission
    options: list[Option]


def options_for(outcome: Outcome, permission: Permission, designated: bool = False) -> Options:
    """비교 결과에 맞는 보완 경로를 낸다. 사양 차이와 타사 지정 모델만 경로를 계산한다.

    Args:
        outcome: `comparison.compare` 결과.
        permission: RFP의 외부 보완 허용 조항(허용·금지·미기재).
        designated: 타사 지정 모델 요구인지.

    Returns:
        Options: 원인·허용과 경로 목록. 근거 있음·조건부·근거 부족은 경로가 아니라 다음 행동(조건 확인·
        자료 요청)이라 빈 목록이다.
    """
    if designated:
        paths = DESIGNATED_PATHS
    elif outcome.status == "SPEC_GAP" and outcome.deficiency_cause is not None:
        paths = FIRST_PATHS[outcome.deficiency_cause]
    else:
        return Options(
            deficiency_cause=outcome.deficiency_cause, customer_permission=permission, options=[]
        )
    options = []
    for path in paths:
        conditions = [CONDITIONS[path]]
        allowed, blocked = True, None
        if path in EXTERNAL and permission == "FORBIDDEN":
            allowed, blocked = False, "RFP가 외부 보완을 금지함"
        elif path in EXTERNAL and permission == "UNSTATED":
            # 적혀 있지 않으면 금지로도 허용으로도 보지 않고 확인 조건으로 남긴다.
            conditions.append("RFP에 외부 보완 허용 여부가 없음: 발주처 확인")
        options.append(
            Option(
                path=path,
                allowed=allowed,
                conditions_to_confirm=conditions if allowed else [],
                required_expertise=[EXPERTISE[path]],
                blocked_reason=blocked,
            )
        )
    if not any(o.allowed for o in options):
        options.append(
            Option(
                path="NONE",
                allowed=True,
                conditions_to_confirm=["필수 조건 유지 여부와 해결해야 할 조건 정리(보류 의견)"],
                required_expertise=["사업 책임"],
                blocked_reason=None,
            )
        )
    return Options(
        deficiency_cause=outcome.deficiency_cause, customer_permission=permission, options=options
    )
