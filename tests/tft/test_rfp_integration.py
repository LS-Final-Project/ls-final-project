"""KAN-186 조립품 통합 책임(명세 v1.3 §7). 14번 유닛 변전소 2.3 A·2.5 F·2.9 B 예. DB 없음."""

from uuid import UUID

import pytest

from backend.api.tft.rfp.integration import (
    Requirement,
    StoredRequirement,
    integration_responsibility,
    project_integration,
)

UNIT = [
    Requirement(clause_no="2.3.A", assembly_clause=True),  # 하나의 조립품으로 공급
    Requirement(clause_no="2.5.F", assembly_clause=True),  # 장비 간 신호선 사전 결선
    Requirement(clause_no="2.9.B", task="FACTORY_TEST"),  # 유닛 단위 공장 시험
    Requirement(clause_no="2.5.K"),  # BIL: 조립품 공급 요구가 아님
]


def test_an_assembly_unit_needs_an_integration_holder_before_review_save():
    result = integration_responsibility(UNIT)
    assert (result.required, result.holder, result.check_item_listed) == (True, "UNRESOLVED", True)
    assert result.basis_clause_nos == ["2.3.A", "2.5.F", "2.9.B"]
    # Missing holder: the confirmation order is offered, and the review version is blocked.
    assert [p.holder for p in result.fallback_paths] == ["OWN", "SINGLE_PARTNER", "JOINT", "HOLD"]
    assert result.unresolved and result.review_save_blocked
    assert result.assumption_sentence.endswith("통합 책임자: 미확보")


@pytest.mark.parametrize(
    ("holder", "text"),
    [("OWN", "자사"), ("SINGLE_PARTNER", "단일 협력사"), ("JOINT", "공동 제안")],
)
def test_a_decided_holder_unblocks_the_review_version(holder, text):
    result = integration_responsibility(UNIT, holder)
    assert (result.review_save_blocked, result.fallback_paths, result.unresolved) == (False, [], [])
    assert text in result.assumption_sentence


def test_no_assembly_requirement_needs_no_integration_check():
    result = integration_responsibility(
        [Requirement(clause_no="2.5.K"), Requirement(clause_no="3.2", task="INSTALL")]
    )
    assert (result.required, result.check_item_listed, result.assumption_sentence) == (
        False,
        False,
        None,
    )
    assert not result.review_save_blocked
    # Warranty and interconnection tasks count as assembly supply too.
    for task in ("WARRANTY", "INTERCONNECTION", "ASSEMBLY"):
        assert integration_responsibility([Requirement(clause_no="9", task=task)]).required


def test_project_integration_gives_the_ids_of_the_basis_clauses_only():
    """같은 조항 번호가 다른 문서에 있어도 근거인 조항의 ID만 낸다. 값은 읽을 때 계산한다(KAN-199)."""
    basis, other = UUID(int=1), UUID(int=2)
    stored = [
        StoredRequirement(clause_id=basis, clause_no="2.3.A", assembly_clause=True),
        StoredRequirement(clause_id=other, clause_no="2.3.A"),
        StoredRequirement(clause_id=UUID(int=3), clause_no="2.9.B", task="FACTORY_TEST"),
    ]
    result = project_integration(stored, "UNRESOLVED")
    assert result["required"] is True
    assert result["basis_clause_ids"] == [basis, UUID(int=3)]
    assert result["basis_clause_nos"] == ["2.3.A", "2.9.B"]
    assert result["unresolved"] and result["fallback_paths"]
    decided = project_integration(stored, "OWN")
    assert decided["unresolved"] == [] and decided["fallback_paths"] == []
    assert project_integration([], "UNRESOLVED")["required"] is False
    # 다른 문서의 같은 번호 조항이 둘 다 근거면 둘 다 낸다. 두 목록은 같은 길이·순서로 대응한다.
    both = project_integration(
        [
            StoredRequirement(clause_id=basis, clause_no="2.3.A", assembly_clause=True),
            StoredRequirement(clause_id=other, clause_no="2.3.A", assembly_clause=True),
        ],
        "UNRESOLVED",
    )
    assert both["basis_clause_ids"] == [basis, other]
    assert both["basis_clause_nos"] == ["2.3.A", "2.3.A"]


def _by_text(text: str) -> bool:
    return integration_responsibility([Requirement(clause_no="9", clause_text=text)]).required


@pytest.mark.parametrize(
    "text",
    [
        "Description: Factory-assembled and tested, complying with the cited standard.",
        "Indoor Unit Arrangement: Single assembly.",
        "Supplier shall have unit responsibility for the complete substation.",
        "Wiring shall be checked by the manufacturer of the completed line-up prior to shipment.",
        "The reactor shall be provided with terminations to match the bus of the adjacent breaker section.",
        "Matching harnesses shall be provided in the MV and LV equipment.",
        "A transition section shall enclose the bus connections from the breaker to the transformer.",
        "Perform factory tests on each unit substation.",
    ],
)
def test_assembly_wording_in_the_clause_text_is_a_basis_without_a_model_mark(text):
    """모델이 조립품 표시를 빠뜨려도 조항 원문의 조립품·접속면 문형으로 판별한다(실행마다 같다)."""
    assert _by_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "Control Wiring: Factory installed, complete with bundling, lacing, and protection.",
        "Touchup Paint: Three containers of paint matching enclosure's exterior finish.",
        "Repair damaged finish to match original finish. Vacuum interiors of the sections.",
        "Verify that fuse ratings match Drawings.",
        "The finish shall match the adjacent sections.",
        "Obtain switchboards and components from single source from single manufacturer.",
        "Tie Breaker rating: 4000 A, coordinate with plans as applicable.",
        "Coil Assembly: the primary and secondary coil assemblies shall be epoxy-cast.",
        "Field tests shall verify functioning as a complete unit.",
        "Access to the transition compartment shall be by bolted steel plates.",
        None,
    ],
)
def test_single_equipment_or_finish_wording_is_not_an_assembly_basis(text):
    """한 장비 안의 공장 결선, 마감 색 맞춤, 단일 공급원 구매 제한, 현장 시험은 조립품 근거가 아니다."""
    assert not _by_text(text)
