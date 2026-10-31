"""합성 문서에서 worker 절 선정 → 추출 fake까지. 원문·실모델은 사용하지 않는다."""

import json

import pytest

from backend.api.tft.worker_processor import sections_of
from backend.engine.documents.model import Block, Position, build_document
from backend.engine.rfp.clauses import build_clause_tree
from backend.engine.rfp.requirements import (
    ExtractedRequirement,
    ExtractionOutput,
    build_inputs,
    extract_section,
    split_input,
)
from backend.shared.ai import Generation, Usage


def tree_of(*lines):
    blocks = [
        Block(block_id=str(i), kind="PARAGRAPH", text=line, position=Position(paragraph_index=i))
        for i, line in enumerate(lines)
    ]
    return build_clause_tree(build_document("DOCX", b"synthetic", blocks, [], tables=0))


@pytest.mark.parametrize("heading", ["제7조", "1. 일반", "제1장 일반"])
def test_korean_leaf_body_reaches_fake_extraction_once(heading):
    body = "외함은 부식 방지 처리가 필요하다."
    tree = tree_of(heading, body)
    sections = sections_of(tree)
    assert len(sections) == 1
    [item] = build_inputs(tree, sections)
    assert [(c.clause_no, c.text) for c in item.clauses] == [(sections[0], body)]
    calls = []

    def fake(prompt):
        clauses = json.loads(prompt.split("Clauses (JSON):\n", 1)[1])
        calls.append(clauses)
        return Generation(
            ExtractionOutput(
                requirements=[
                    ExtractedRequirement(
                        clause_no=sections[0],
                        quote=body,
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
                ]
            ).model_dump_json(),
            Usage(),
            "fake",
            [],
        )

    result = extract_section(item, fake)
    assert result.outcome == "VALID"
    assert len(calls) == result.parts == 1
    assert len(result.requirements) == 1
    assert result.covered_final == result.sentences == 1


def test_korean_parent_with_children_does_not_duplicate_head_body():
    tree = tree_of("제1장 일반", "제1조", "1. 외함은 도장한다.", "2. 외함은 접지한다.")
    assert sections_of(tree) == ["제1조"]
    [item] = build_inputs(tree, sections_of(tree))
    assert [c.clause_no for c in item.clauses] == ["제1조.1", "제1조.2"]


def test_english_leaf_and_group_input_and_logical_calls_are_unchanged():
    tree = tree_of(
        "PART 1 - GENERAL",
        "1.1 SUMMARY",
        "A. Enclosure shall resist corrosion.",
        "1.2 UNUSED LEAF",
        "This section has no numbered children.",
    )
    assert sections_of(tree) == ["1.1"]
    [item] = build_inputs(tree, sections_of(tree))
    assert item.model_dump() == {
        "section_no": "1.1",
        "section_title": "SUMMARY",
        "clauses": [
            {
                "clause_no": "1.1.A",
                "path": "",
                "text": "Enclosure shall resist corrosion.",
                "table_rows": [],
            }
        ],
    }
    assert len(split_input(item)) == 1


def test_korean_decimal_leaf_body_is_extracted_without_repeating_chapter_title():
    tree = tree_of("제1장 일반", "1.1 범위", "외함은 도장한다.")
    assert sections_of(tree) == ["1.1"]
    [item] = build_inputs(tree, sections_of(tree))
    assert item.section_title == "범위"
    assert [(c.clause_no, c.text) for c in item.clauses] == [("1.1", "외함은 도장한다.")]
