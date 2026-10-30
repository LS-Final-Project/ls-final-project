"""KAN-189 일반 합성 조항으로 질문 초안과 오탐 방지 경계를 검증한다."""

from copy import deepcopy

import pytest

from backend.engine.documents.model import CrossReference, DeletedValue
from backend.engine.rfp.clauses import Clause, ClauseTree, TableDeleted
from backend.engine.rfp.questions import generate_question_drafts
from backend.engine.rfp.requirements import Condition, ExtractedRequirement


def clause(no: str, text: str, **updates: object) -> Clause:
    """검사에 필요한 현행 조항을 만든다.

    Args:
        no: 조항 번호.
        text: 합성 본문.
        updates: 추가 필드.

    Returns:
        Clause: 현행 조항.
    """
    return Clause.model_validate(
        dict(
            clause_no=no,
            label=no,
            depth=3,
            parent_no=None,
            ordinal=0,
            title=None,
            text=text,
            deleted_values=[],
            block_ids=[no],
        )
        | updates
    )


def tree(*clauses: Clause) -> ClauseTree:
    """합성 조항 트리를 만든다.

    Args:
        clauses: 조항 목록.

    Returns:
        ClauseTree: 합성 트리.
    """
    return ClauseTree(
        sha256="synthetic",
        edit_trace_support=True,
        section_title=None,
        clauses=list(clauses),
        preamble_block_ids=[],
        boilerplate_block_ids=[],
        excluded=[],
        numbering_issues=[],
    )


def req(no: str, quote: str, value: str | None, **updates: object) -> ExtractedRequirement:
    """합성 전류 요구를 만든다.

    Args:
        no: 조항 번호.
        quote: 현행 원문 인용.
        value: 추출한 값.
        updates: 요구 필드 덮어쓰기.

    Returns:
        ExtractedRequirement: 합성 요구.
    """
    return ExtractedRequirement.model_validate(
        dict(
            clause_no=no,
            quote=quote,
            kind="SPEC",
            equipment="switchgear",
            task=None,
            attribute="rated current",
            value=value,
            unit="A",
            operator="EQ",
            standard_system="IEC",
            necessity="MANDATORY",
            designated_model=None,
            equipment_family="MV_EQUIP",
            attribute_key="rated_current",
        )
        | updates
    )


@pytest.mark.parametrize("marker", ["<Insert value>", "TBD", "[ ]", "___", "<INSERT rating>"])
def test_placeholders_remain_undetermined(marker: str) -> None:
    text = f"The rated current shall be {marker} A."
    output = generate_question_drafts(tree(clause("3.1.A", text)), [])
    assert len(output) == 1
    draft = output[0]
    assert draft.kind == "PLACEHOLDER"
    assert draft.sources[0].clause_no == "3.1.A"
    assert draft.sources[0].quote == text
    assert "미정" in draft.pre_reply_note and "판정에서 제외" in draft.pre_reply_note
    assert draft.question.endswith("?")


def test_title_and_table_current_values_are_used_without_deleted_history() -> None:
    old = DeletedValue(text="TBD or approved equal", position=0, origin="STRIKE")
    c = clause(
        "3.1.A",
        "Rated current: 800 A.",
        deleted_values=[old],
        title="Rating ___",
        table_rows=[["Voltage", "[ ]"]],
        title_deleted_values=[old],
        table_deleted_values=[TableDeleted(row=0, col=0, value=old)],
    )
    output = generate_question_drafts(tree(c), [])
    assert [d.kind for d in output] == ["PLACEHOLDER"]
    assert [s.quote for s in output[0].sources] == ["Rating ___", "[ ]"]
    clean = c.model_copy(update={"title": None, "table_rows": [["Voltage", "400 V"]]})
    assert generate_question_drafts(tree(clean), []) == []


def test_same_family_form_difference_is_a_question_despite_equipment_wording() -> None:
    a = "Provide metal-clad switchgear."
    b = "Provide metal-enclosed switchgear."
    r1 = req(
        "3.1.A",
        a,
        "metal-clad",
        equipment="metal-clad switchgear",
        attribute_key="form",
        unit=None,
        operator="TEXT",
    )
    r2 = req(
        "3.2.A",
        b,
        "metal-enclosed",
        equipment="metal-enclosed switchgear",
        attribute_key="form",
        unit=None,
        operator="TEXT",
    )
    t = tree(clause(r1.clause_no, a), clause(r2.clause_no, b))
    original = deepcopy((t.model_dump(), r1.model_dump(), r2.model_dump()))
    output = generate_question_drafts(t, [r1, r2, r1])
    assert len(output) == 1 and output[0].kind == "INCONSISTENCY"
    assert [(s.clause_no, s.quote) for s in output[0].sources] == [("3.1.A", a), ("3.2.A", b)]
    assert original == (t.model_dump(), r1.model_dump(), r2.model_dump())
    assert output == generate_question_drafts(t, [r1, r2, r1])


@pytest.mark.parametrize(
    ("v1", "u1", "v2", "u2"),
    [
        ("1200", "A", "1.2", "kA"),
        ("1e3", "A", "1000", "A"),
        ("1e-7", "A", "0.0000001", "A"),
        ("1E3", "A", "1", "kA"),
        ("1,200", "A", "1200.0", "a"),
        ("1.2 kA", None, "1200", "A"),
        ("0.4", "kV", "400", "V"),
        ("25", "percent", "25.0", "%"),
        ("1", "s", "1000", "ms"),
        ("Metal–Clad", None, "metal-clad", None),
    ],
)
def test_equivalent_units_and_notation_are_not_conflicts(
    v1: str,
    u1: str | None,
    v2: str,
    u2: str | None,
) -> None:
    a, b = f"Required: {v1} {u1 or ''}.", f"Required: {v2} {u2 or ''}."
    assert (
        generate_question_drafts(
            tree(clause("A", a), clause("B", b)),
            [req("A", a, v1, unit=u1), req("B", b, v2, unit=u2)],
        )
        == []
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"equipment_family": "TRANSFORMER"},
        {"equipment_family": "OTHER"},
        {"equipment_family": None},
        {"attribute_key": "OTHER"},
        {"attribute_key": "busbar_rated_current"},
        {"value": None},
        {"kind": "DOCUMENT"},
        {"clause_no": "missing"},
        {"conditions": [Condition(name="ambient", value="40", unit="°C")]},
    ],
)
def test_unrelated_or_unresolved_requirements_are_not_compared(updates: dict[str, object]) -> None:
    a, b = "Required current: 800 A.", "Required current: 1600 A."
    assert (
        generate_question_drafts(
            tree(clause("A", a), clause("B", b)),
            [req("A", a, "800"), req("B", b, "1600").model_copy(update=updates)],
        )
        == []
    )


def test_explicit_different_equipment_ids_are_excluded() -> None:
    a, b = "Required: 800 A.", "Required: 1600 A."
    assert (
        generate_question_drafts(
            tree(clause("A", a), clause("B", b)),
            [
                req("A", a, "800", equipment="switchgear SWG-1"),
                req("B", b, "1600", equipment="switchgear SWG-2"),
            ],
        )
        == []
    )


def test_equivalent_conditions_allow_comparison_but_different_conditions_do_not() -> None:
    a, b = "Required: 800 A.", "Required: 1600 A."
    c1 = Condition(name="supply voltage", value="400", unit="V")
    c2 = Condition(name=" Supply Voltage ", value="0.4", unit="kV")
    t = tree(clause("A", a), clause("B", b))
    left, right = req("A", a, "800", conditions=[c1]), req("B", b, "1600", conditions=[c2])
    assert len(generate_question_drafts(t, [left, right])) == 1
    right = right.model_copy(update={"conditions": [c2.model_copy(update={"value": "0.6"})]})
    assert generate_question_drafts(t, [left, right]) == []


def test_stale_deleted_value_and_quote_are_excluded() -> None:
    a, b = "Current: 125 A.", "Current: 300 A."
    t = tree(
        clause("A", a, deleted_values=[DeletedValue(text="25", position=9, origin="STRIKE")]),
        clause("B", b),
    )
    assert generate_question_drafts(t, [req("A", a, "25"), req("B", b, "300")]) == []
    assert generate_question_drafts(t, [req("A", "Current: 25 A.", "25"), req("B", b, "300")]) == []
    assert len(generate_question_drafts(t, [req("A", a, "125"), req("B", b, "300")])) == 1


@pytest.mark.parametrize(
    "phrase", ["or approved equal", "with Owner approval", "subject to Owner approval"]
)
def test_alternative_without_process_or_materials_needs_question(phrase: str) -> None:
    text = f"Provide the specified relay {phrase}."
    output = generate_question_drafts(tree(clause("A", text)), [])
    assert len(output) == 1 and output[0].kind == "APPROVAL_PROCESS"
    assert output[0].sources[0].quote == text
    assert "승인 요청 방법" in output[0].question and "제출 자료 목록" in output[0].question
    assert "승인 완료가 아닙니다" in output[0].pre_reply_note


@pytest.mark.parametrize("where", ["local", "ancestor", "child", "reference"])
def test_relevant_approval_instructions_suppress_question(where: str) -> None:
    instructions = (
        "Submit datasheets and test reports to the Owner for approval before procurement."
    )
    base = clause("A", "Provide the specified relay or approved equal.")
    extra: list[Clause] = []
    if where == "local":
        base = base.model_copy(update={"text": base.text + " " + instructions})
    elif where == "ancestor":
        base = base.model_copy(update={"parent_no": "P"})
        extra = [clause("P", instructions)]
    elif where == "child":
        extra = [clause("A.1", instructions, parent_no="A")]
    else:
        base = base.model_copy(
            update={
                "cross_references": [
                    CrossReference(
                        text="see clause B",
                        target_kind="CLAUSE",
                        target_hint="B",
                        resolved=True,
                        target_block_id="B",
                    )
                ]
            }
        )
        extra = [clause("B", instructions)]
    assert generate_question_drafts(tree(base, *extra), []) == []


def test_unrelated_sibling_procedure_does_not_hide_question() -> None:
    t = tree(
        clause("P", "Equipment requirements."),
        clause("A", "Provide a relay or approved equal.", parent_no="P"),
        clause("B", "Submit drawings for approval before installation.", parent_no="P"),
    )
    output = generate_question_drafts(t, [])
    assert len(output) == 1 and output[0].sources[0].clause_no == "A"


@pytest.mark.parametrize(
    ("instructions", "missing", "present"),
    [
        ("Obtain Owner approval before procurement.", "제출 자료 목록", "승인 요청 방법"),
        ("Provide datasheets and test reports.", "승인 요청 방법", "제출 자료 목록"),
    ],
)
def test_only_missing_approval_information_is_requested(
    instructions: str,
    missing: str,
    present: str,
) -> None:
    c = clause("A", "Provide a relay with Owner approval. " + instructions)
    output = generate_question_drafts(tree(c), [])
    assert len(output) == 1
    assert missing in output[0].question and present not in output[0].question


@pytest.mark.parametrize(
    "instruction",
    [
        "Do not submit drawings for approval.",
        "No need to submit datasheets for approval.",
        "Submit drawings for approval; procedure and materials TBD.",
        "Submit TBD drawings for approval.",
        "Submit drawings for approval under a procedure to be defined.",
        "Submit drawings for approval pending confirmation.",
    ],
)
def test_negative_or_unconfirmed_instructions_do_not_suppress_question(instruction: str) -> None:
    # 세미콜론 뒤의 미정 문구도 그 지시의 일부이므로 같은 문장에 유지한다.
    text = "Provide a relay or approved equal. " + instruction
    output = generate_question_drafts(tree(clause("A", text)), [])
    approval = [d for d in output if d.kind == "APPROVAL_PROCESS"]
    assert len(approval) == 1
    assert "승인 요청 방법" in approval[0].question and "제출 자료 목록" in approval[0].question


def test_confirmed_sentence_is_not_hidden_by_an_unrelated_tbd_sentence() -> None:
    text = (
        "Provide a relay or approved equal. Delivery date TBD. "
        "Submit drawings for Owner approval before procurement."
    )
    output = generate_question_drafts(tree(clause("A", text)), [])
    assert [d.kind for d in output] == ["PLACEHOLDER"]


@pytest.mark.parametrize("change", [{"operator": "GTE"}, {"tolerance": "±5%"}])
def test_operator_and_tolerance_differences_are_clarification_candidates(
    change: dict[str, object],
) -> None:
    a, b = "Current shall be 800 A.", "Current: 800 A."
    output = generate_question_drafts(
        tree(clause("A", a), clause("B", b)),
        [req("A", a, "800"), req("B", b, "800").model_copy(update=change)],
    )
    assert len(output) == 1 and output[0].kind == "INCONSISTENCY"


def test_same_clause_and_placeholder_values_do_not_create_inconsistency() -> None:
    text = "Main: 800 A; auxiliary: 400 A; spare: TBD A."
    output = generate_question_drafts(
        tree(clause("A", text)),
        [
            req("A", "Main: 800 A", "800"),
            req("A", "auxiliary: 400 A", "400"),
            req("A", "spare: TBD A", "TBD"),
        ],
    )
    assert [d.kind for d in output] == ["PLACEHOLDER"]
