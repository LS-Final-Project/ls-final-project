"""합성 문서에서 worker 절 선정 → 추출 fake까지. 원문·실모델은 사용하지 않는다."""

import json

import pytest

from backend.api.tft.worker_processor import sections_of
from backend.engine.documents.model import Block, Position, build_document
from backend.engine.documents.router import extract
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
    document = extract(f"{heading}\n\n{body}".encode(), mime="text/markdown")
    tree = build_clause_tree(document)
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


def test_compact_headings_repeated_per_chapter_are_not_page_noise():
    """KAN-215(D22 유형): 장마다 같은 모양으로 반복되는 공백 없는 번호 제목을 머리말·바닥글로 빼지 않는다."""
    lines = ["제8장 송변전설비"]
    for n in (1, 2, 3):
        lines += [
            f"{n}.1적용 범위",
            f"{n}.1.1본 규격은 설비의 설계와 제작에 적용한다.",
            f"{n}.4설계 및 제작 기술기준",
            f"{n}.4.1운전조건",
            f"{n}.4.3성능보증",
            "1)계약상대자는 성능을 보증하여야 한다.",
        ]
    tree = tree_of(*lines)
    numbers = [c.clause_no for c in tree.clauses]
    assert {"1.4", "1.4.3", "2.4", "3.4.1"} <= set(numbers)
    assert not [n for n in numbers if "~" in n]
    assert not [e for e in tree.excluded if e.reason == "PAGE_NOISE"]


def test_table_of_contents_lines_and_chapter_item_children():
    """KAN-215(D20 유형): 점선 목차 줄은 조항이 아니고, 장 안의 "1.발전설비" 아래 "1.2설비 구성"은 자식 조항이다."""
    tree = tree_of(
        "제 2 장 계통설명서",
        "목 차",
        "1.발전설비 .......................",
        "1.2설비 구성 ......................",
        "1.발전설비 태양에너지를 전기에너지로 변환한다.",
        "1.2설비 구성",
        "1)수상전기실과 154kV 변전소를 연계한다.",
        "2)인버터를 설치한다.",
        "1.3설비 운영",
        "1)차단기는 고장 시 차단하여야 한다.",
    )
    numbers = [c.clause_no for c in tree.clauses]
    assert {"1.2", "1.3"} <= set(numbers)
    assert not [n for n in numbers if "~" in n]
    assert {e.text for e in tree.excluded if e.reason == "TABLE_OF_CONTENTS"} == {
        "1.발전설비 .......................",
        "1.2설비 구성 ......................",
    }


@pytest.mark.parametrize("line", ["3.25배 이상이어야 한다.", "0.5초 이내에 동작하여야 한다."])
def test_attached_measures_are_not_headings(line):
    tree = tree_of("제1장 일반", "1.1적용 범위", line)
    assert [c.clause_no for c in tree.clauses] == ["제1장", "1.1"]
