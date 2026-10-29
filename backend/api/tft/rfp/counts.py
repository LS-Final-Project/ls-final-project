"""RFP 미해결 필수 요구의 공용 집계(KAN-196).

목록·상세·검토본 저장은 동일 프로젝트의 최신 요구 판본 전체를 전달한다.
페이지 일부나 과거 판본으로 합산하지 않는다. 상태 변경이나 DB 쓰기는 하지 않는다.
"""

from collections.abc import Iterable

from backend.api.tft.rfp.schemas import RequirementDetail


def is_unresolved_mandatory(requirement: RequirementDetail) -> bool:
    """필수·조건부 필수 중 근거 미확정·미확인 사항·재검토가 남았는지 판정한다."""
    return requirement.spec.necessity in {"MANDATORY", "CONDITION_BOUND"} and (
        requirement.judgement.status != "EVIDENCE_FOUND"
        or bool(requirement.judgement.unresolved)
        or requirement.needs_rereview
    )


def count_unresolved_mandatory(requirements: Iterable[RequirementDetail]) -> int:
    """최신 요구를 한 번씩 센다. 같은 요구의 중복·과거 판본 혼입은 조용히 합산하지 않는다."""
    seen = set()
    count = 0
    for requirement in requirements:
        if requirement.requirement_id in seen:
            raise ValueError("Supply exactly one latest version per requirement")
        seen.add(requirement.requirement_id)
        count += is_unresolved_mandatory(requirement)
    return count
