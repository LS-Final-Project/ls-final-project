"""KAN-189 일반 합성 조항으로 질문 초안과 오탐 방지 경계를 검증한다."""

from copy import deepcopy

import pytest

from backend.engine.documents.model import DeletedValue
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
        ("1", "MW", "1000000", "W"),
        ("1", "MVA", "1000000", "VA"),
        ("1e3", None, "1000.0", None),
        ("1e-7", None, "0.0000001", None),
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


@pytest.mark.parametrize(("left_unit", "right_unit"), [("mW", "MW"), ("mw", "MW"), ("mVA", "MVA")])
@pytest.mark.parametrize("inline", [False, True])
def test_case_sensitive_or_ambiguous_units_are_not_equivalent(
    left_unit: str, right_unit: str, inline: bool
) -> None:
    a, b = f"Required: 1 {left_unit}.", f"Required: 1 {right_unit}."
    left = req("A", a, f"1 {left_unit}" if inline else "1", unit=None if inline else left_unit)
    right = req("B", b, f"1 {right_unit}" if inline else "1", unit=None if inline else right_unit)
    output = generate_question_drafts(tree(clause("A", a), clause("B", b)), [left, right])
    assert len(output) == 1 and output[0].kind == "INCONSISTENCY"


def test_case_sensitive_condition_units_do_not_match() -> None:
    a, b = "Required: 800 A.", "Required: 1600 A."
    assert (
        generate_question_drafts(
            tree(clause("A", a), clause("B", b)),
            [
                req("A", a, "800", conditions=[Condition(name="power", value="1", unit="mW")]),
                req("B", b, "1600", conditions=[Condition(name="power", value="1", unit="MW")]),
            ],
        )
        == []
    )


def test_unsupported_unit_keeps_literal_numeric_notation() -> None:
    a, b = "Required: 1 mw.", "Required: 1.0 mw."
    output = generate_question_drafts(
        tree(clause("A", a), clause("B", b)),
        [req("A", a, "1", unit="mw"), req("B", b, "1.0", unit="mw")],
    )
    assert len(output) == 1 and output[0].kind == "INCONSISTENCY"


def test_hyphenated_equipment_properties_are_not_identifiers() -> None:
    a, b = "Required: 800 A.", "Required: 1600 A."
    output = generate_question_drafts(
        tree(clause("A", a), clause("B", b)),
        [
            req("A", a, "800", equipment="switchgear IP-54"),
            req("B", b, "1600", equipment="switchgear IP-65"),
        ],
    )
    assert len(output) == 1 and output[0].kind == "INCONSISTENCY"


@pytest.mark.parametrize(
    ("current", "stale"),
    [("1,125", "125"), ("1.25", "25"), ("1250", "125"), ("125.5", "125"), ("125,000", "125")],
)
def test_numeric_fragments_are_not_current_values(current: str, stale: str) -> None:
    a, b = f"Required: {current} A.", "Required: 300 A."
    t = tree(clause("A", a), clause("B", b))
    assert generate_question_drafts(t, [req("A", a, stale), req("B", b, "300")]) == []
    assert len(generate_question_drafts(t, [req("A", a, current), req("B", b, "300")])) == 1


@pytest.mark.parametrize("text", ["Required: 125.", "Required,125, nominal.", "(125)."])
def test_punctuation_around_whole_numeric_values_is_allowed(text: str) -> None:
    b = "Required: 300."
    output = generate_question_drafts(
        tree(clause("A", text), clause("B", b)),
        [req("A", text, "125", unit=None), req("B", b, "300", unit=None)],
    )
    assert len(output) == 1 and output[0].kind == "INCONSISTENCY"


def test_whitespace_value_is_not_comparable() -> None:
    a, b = "Required: 125 A.", "Required: 300 A."
    assert (
        generate_question_drafts(
            tree(clause("A", a), clause("B", b)),
            [req("A", a, " \t "), req("B", b, "300")],
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
                req("A", a, "800", equipment="switchgear tag SWG-1"),
                req("B", b, "1600", equipment="switchgear tag SWG-2"),
            ],
        )
        == []
    )


@pytest.mark.parametrize("word", ["units", "tagged", "tags"])
def test_equipment_keyword_prefixes_are_not_identifiers(word: str) -> None:
    a, b = "Required: 800 A.", "Required: 1600 A."
    output = generate_question_drafts(
        tree(clause("A", a), clause("B", b)),
        [
            req("A", a, "800", equipment=f"switchgear {word}"),
            req("B", b, "1600", equipment="switchgear unit A"),
        ],
    )
    assert len(output) == 1 and output[0].kind == "INCONSISTENCY"


@pytest.mark.parametrize("keyword", ["unit", "tag"])
def test_explicit_unit_and_tag_identifiers_still_exclude_different_equipment(keyword: str) -> None:
    a, b = "Required: 800 A.", "Required: 1600 A."
    assert (
        generate_question_drafts(
            tree(clause("A", a), clause("B", b)),
            [
                req("A", a, "800", equipment=f"switchgear {keyword} A"),
                req("B", b, "1600", equipment=f"switchgear {keyword} B"),
            ],
        )
        == []
    )


@pytest.mark.parametrize("value", ["1e999999999", "1e999999999999999999999999"])
@pytest.mark.parametrize("equal", [False, True])
@pytest.mark.parametrize("in_condition", [False, True])
def test_unrepresentable_decimal_uses_text_comparison(
    value: str, equal: bool, in_condition: bool
) -> None:
    other = value if equal else "2e999999999999999999999999"
    if in_condition:
        a, b = "Required: 800 A.", "Required: 1600 A."
        left = req("A", a, "800", conditions=[Condition(name="limit", value=value, unit="A")])
        right = req("B", b, "1600", conditions=[Condition(name="limit", value=other, unit="A")])
        expected = int(equal)
    else:
        a, b = f"Required: {value} A.", f"Required: {other} A."
        left, right = req("A", a, value), req("B", b, other)
        expected = int(not equal)
    output = generate_question_drafts(tree(clause("A", a), clause("B", b)), [left, right])
    assert len(output) == expected
    assert all(draft.kind == "INCONSISTENCY" for draft in output)


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
