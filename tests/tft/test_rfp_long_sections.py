"""KAN-185 긴 절: 일반 합성 조항의 보존과 호출 비용 제한을 확인한다."""

import json

import pytest

from backend.engine.documents.model import Block, Position, build_document
from backend.engine.rfp.clauses import build_clause_tree
from backend.engine.rfp.requirements import (
    MAX_SECTION_CHARS,
    MAX_SECTION_PARTS,
    PART_CHARS,
    ExtractedRequirement,
    ExtractionOutput,
    build_inputs,
    extract_section,
    requirement_sentences,
    split_input,
)
from backend.shared.ai import Generation, Usage


def synthetic_tree(groups=5, children=2, chars=1500):
    lines = ["PART 3 - PRODUCTS", "3.1 CONTROL EQUIPMENT"]
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:groups]:
        lines.append(f"{letter}. Control requirements:")
        for number in range(1, children + 1):
            sentence = "The enclosure shall resist corrosion. "
            text = (sentence * (chars // len(sentence) + 1))[:chars]
            lines.append(f"{number}. {text}")
    lines += ["3.2 INSPECTION", "A. The enclosure shall be inspected."]
    blocks = [
        Block(
            block_id=f"synthetic-{index}",
            kind="PARAGRAPH",
            text=line,
            position=Position(paragraph_index=index),
        )
        for index, line in enumerate(lines)
    ]
    return build_clause_tree(build_document("DOCX", b"synthetic", blocks, [], tables=0))


@pytest.mark.parametrize("groups,children", [(8, 2), (1, 16)])
def test_long_sections_preserve_every_clause_in_order_and_context(groups, children):
    tree = synthetic_tree(groups, children)
    inputs = build_inputs(tree, ["3.2", "3.1"])
    assert inputs[0].section_no == "3.2"
    assert len(inputs) == 2
    item = inputs[1]
    assert sum(len(c.text) for c in item.clauses) >= 23000
    parts = split_input(item)
    assert len(parts) > 1
    expected = [c for c in tree.clauses if c.clause_no.startswith("3.1.")]
    actual = [c for part in parts for c in part.clauses]
    assert [(c.clause_no, c.text, c.table_rows) for c in actual] == [
        (c.clause_no, c.text, c.table_rows) for c in expected
    ]
    assert all(
        part.section_no == "3.1" and part.section_title == "CONTROL EQUIPMENT" for part in parts
    )
    assert all(c.path == "Control requirements:" for c in actual if c.clause_no.count(".") == 3)
    assert all(split_input(part) == [part] for part in parts)
    assert all(sum(len(c.text) for c in part.clauses) <= PART_CHARS for part in parts)
    calls = []

    def provider(text):
        clauses = json.loads(text.split("Clauses (JSON):\n", 1)[1])
        calls.append([c["clause_no"] for c in clauses])
        requirements = [
            ExtractedRequirement(
                clause_no=c["clause_no"],
                quote=c["text"],
                kind="SPEC",
                equipment="enclosure",
                task=None,
                attribute="corrosion resistance",
                value=None,
                unit=None,
                operator="TEXT",
                standard_system="NONE",
                necessity="MANDATORY",
                designated_model=None,
            )
            for c in clauses
            if not c["text"].endswith(":")
        ]
        return Generation(
            ExtractionOutput(requirements=requirements).model_dump_json(), Usage(), "fake", []
        )

    result = extract_section(item, provider)
    assert result.outcome == "VALID"
    assert len(calls) == result.parts == len(parts)
    called_clauses = [no for call in calls for no in call]
    assert called_clauses == [c.clause_no for c in expected]
    assert len(set(called_clauses)) == len(called_clauses)
    assert [r.clause_no for r in result.requirements] == [
        c.clause_no for c in expected if not c.text.endswith(":")
    ]
    assert result.sentences == len(requirement_sentences(item))
    assert result.covered_first == result.covered_final == result.sentences
    assert result.parts_failed == 0


@pytest.mark.parametrize("chars", [PART_CHARS + 1, MAX_SECTION_CHARS])
def test_single_clause_above_target_is_preserved_without_text_slicing(chars):
    tree = synthetic_tree(groups=2, children=1, chars=chars)
    parts = build_inputs(tree, ["3.1"])
    actual = [c for part in parts for c in part.clauses if c.clause_no.endswith(".1")]
    assert len(actual) == 2
    assert all(len(c.text) == chars for c in actual)
    assert all(
        len(c.text) <= MAX_SECTION_CHARS
        for part in parts
        for piece in split_input(part)
        for c in piece.clauses
    )


def test_individual_clause_above_hard_limit_is_rejected_before_calls():
    with pytest.raises(ValueError, match="^RFP_SECTION_TOO_LONG$"):
        build_inputs(synthetic_tree(groups=1, children=1, chars=MAX_SECTION_CHARS + 1), ["3.1"])


def test_hard_limit_counts_table_rows():
    tree = synthetic_tree(groups=1, children=1, chars=MAX_SECTION_CHARS)
    clauses = [
        c.model_copy(update={"table_rows": [["Additional requirement"]]})
        if c.clause_no == "3.1.A.1"
        else c
        for c in tree.clauses
    ]
    with pytest.raises(ValueError, match="^RFP_SECTION_TOO_LONG$"):
        build_inputs(tree.model_copy(update={"clauses": clauses}), ["3.1"])


def test_cost_guard_counts_deeper_splits_and_allows_exact_limit():
    tree = synthetic_tree(groups=1, children=MAX_SECTION_PARTS - 1, chars=PART_CHARS)
    inputs = build_inputs(tree, ["3.1"])
    assert len(inputs) == 1
    # 머리글도 단독 부분이 되어 실제 호출 수에 포함된다.
    actual_parts = split_input(inputs[0])
    assert len(actual_parts) == MAX_SECTION_PARTS
    calls = []

    def provider(text):
        calls.append(text)
        return Generation('{"requirements": []}', Usage(), "fake", [])

    result = extract_section(inputs[0], provider)
    assert result.parts == MAX_SECTION_PARTS
    # 머리글은 보완 호출이 없고, 요구가 있는 부분은 최초+보완 두 번이다.
    assert len(calls) == 2 * MAX_SECTION_PARTS - 1
    with pytest.raises(ValueError, match="^RFP_SECTION_TOO_MANY_PARTS$"):
        build_inputs(
            synthetic_tree(groups=1, children=MAX_SECTION_PARTS, chars=PART_CHARS), ["3.1"]
        )


def test_cost_guard_counts_existing_first_level_splits():
    parts = build_inputs(synthetic_tree(groups=MAX_SECTION_PARTS, children=1, chars=2100), ["3.1"])
    calls = []

    def provider(text):
        calls.append(text)
        return Generation('{"requirements": []}', Usage(), "fake", [])

    for part in parts:
        extract_section(part, provider)
    assert len(calls) == 2 * MAX_SECTION_PARTS
    with pytest.raises(ValueError, match="^RFP_SECTION_TOO_MANY_PARTS$"):
        build_inputs(synthetic_tree(groups=MAX_SECTION_PARTS + 1, children=1, chars=2100), ["3.1"])


@pytest.mark.parametrize("chars", [2500, 4000, 5989])
def test_first_level_group_above_target_is_split_at_clause_boundaries(chars):
    item = build_inputs(synthetic_tree(groups=1, children=2, chars=chars), ["3.1"])[0]
    assert 5000 <= sum(len(c.text) for c in item.clauses) <= MAX_SECTION_CHARS
    parts = split_input(item)
    assert len(parts) == (2 if chars < PART_CHARS else 3)
    assert [c for part in parts for c in part.clauses] == item.clauses
    for part in parts:
        assert sum(len(c.text) for c in part.clauses) <= PART_CHARS or len(part.clauses) == 1


@pytest.mark.parametrize("chars", [PART_CHARS + 1, MAX_SECTION_CHARS])
def test_individual_clause_above_target_is_a_standalone_part(chars):
    item = build_inputs(synthetic_tree(groups=1, children=1, chars=chars), ["3.1"])[0]
    parts = split_input(item)
    assert len(parts) == 2
    assert parts[0].clauses == item.clauses[:1]
    assert parts[1].clauses == item.clauses[1:]
    assert len(parts[1].clauses[0].text) == chars


def test_custom_target_controls_first_level_group_splitting():
    item = build_inputs(synthetic_tree(groups=1, children=2, chars=100), ["3.1"])[0]
    assert split_input(item) == [item]
    parts = split_input(item, limit=150)
    assert [len(part.clauses) for part in parts] == [2, 1]
    assert [c for part in parts for c in part.clauses] == item.clauses


@pytest.mark.parametrize("chars", [100, 2488, 3950, 3978])
def test_section_at_or_below_target_remains_one_input_and_one_part(chars):
    inputs = build_inputs(synthetic_tree(groups=1, children=1, chars=chars), ["3.1"])
    assert len(inputs) == 1
    assert sum(len(c.text) for c in inputs[0].clauses) <= PART_CHARS
    assert split_input(inputs[0]) == inputs
