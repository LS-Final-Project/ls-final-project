"""KAN-99 섹션 청킹. 추출 문서를 코드로 직접 만든다(실제 RFP 없음)."""

import hashlib
from typing import Any

from backend.engine.documents.model import (
    Block,
    CellState,
    DeletedValue,
    Footnote,
    Hints,
    Position,
    build_document,
)
from backend.engine.search.chunking import INDEX_VERSION, chunk_document


def para(block_id: str, text: str, index: int, **extra: Any) -> Block:
    fields: dict[str, Any] = {
        "block_id": block_id,
        "kind": "PARAGRAPH",
        "text": text,
        "position": Position(paragraph_index=index),
    }
    return Block.model_validate(fields | extra)


def numbered(
    block_id: str, text: str, index: int, list_text: str | None, level: int | None, **extra: Any
) -> Block:
    hints = Hints(
        style_name=extra.pop("style", None),
        numbering_level=level,
        list_text=list_text,
        is_heading=extra.pop("heading", False),
        numbering_raw=None,
    )
    return para(block_id, text, index, hints=hints, **extra)


def cell(block_id: str, row: int, col: int, text: str, **extra: Any) -> Block:
    fields: dict[str, Any] = {
        "block_id": block_id,
        "kind": "TABLE_CELL",
        "text": text,
        "position": Position(table_index=0, row=row, col=col),
        "cell_state": CellState(
            col_span=1, merged_with=extra.pop("merged_with", None), is_empty=not text
        ),
    }
    return Block.model_validate(fields | extra)


def doc(*blocks: Block, data: bytes = b"synthetic-rfp", kind: str = "DOCX"):
    return build_document(kind, data, list(blocks), [], tables=1)  # type: ignore[arg-type]


SPEC = [
    numbered("h1", "PART 2 PRODUCTS", 0, None, None, style="Heading 1", heading=True),
    numbered("h2", "Transformer", 1, "2.5", 1),
    numbered("h3", "Ratings", 2, "C.", 2),
    numbered("p1", "Capacity 630 kVA.", 3, "1.", 3),
    numbered("h4", "Accessories", 4, "D.", 2),
    numbered("p2", "Provide two fans.", 5, "1.", 3),
    numbered("h5", "Switchgear", 6, "2.6", 1),
]


def test_section_path_follows_heading_and_numbering_levels() -> None:
    result = chunk_document(doc(*SPEC))
    paths = [(chunk.section_path, chunk.block_ids) for chunk in result.chunks]
    assert paths == [
        # v2: 제목만 든 PART·2.5 청크는 따로 두지 않고 다음 청크 앞에 합친다.
        # level 3인 "1."은 경계가 아니라 C. 섹션 본문이다. 경계 문장도 첫 청크에 들어간다.
        (["PART 2 PRODUCTS", "2.5 Transformer", "C. Ratings"], ["h1", "h2", "h3", "p1"]),
        (["PART 2 PRODUCTS", "2.5 Transformer", "D. Accessories"], ["h4", "p2"]),
        # 문서 끝의 제목만 청크는 합칠 곳이 없어 그대로 둔다.
        (["PART 2 PRODUCTS", "2.6 Switchgear"], ["h5"]),
    ]
    assert result.chunks[0].text == "PART 2 PRODUCTS\nTransformer\nRatings\nCapacity 630 kVA."
    assert result.chunks[0].source_position == Position(paragraph_index=0)
    assert all(chunk.kind == "TEXT" and chunk.page_start is None for chunk in result.chunks)


def test_long_heading_is_cut_in_path_only() -> None:
    title = "Ratings " + "x" * 100
    [chunk] = chunk_document(doc(numbered("h", title, 0, "C.", 2))).chunks
    assert chunk.section_path == [("C. " + title)[:80] + "…"]
    assert chunk.text == title


def test_budget_splits_on_paragraph_boundary_and_keeps_big_paragraph_whole() -> None:
    blocks = [para(f"p{i}", "a" * 500, i) for i in range(3)]
    big = para("big", "b" * 1500, 3)
    result = chunk_document(doc(*blocks, big, para("tail", "c" * 10, 4)))
    assert [chunk.block_ids for chunk in result.chunks] == [
        ["p0", "p1"],
        ["p2"],
        ["big"],
        ["tail"],
    ]
    assert result.chunks[0].char_count == 1001
    assert result.chunks[2].text == "b" * 1500 and result.chunks[2].char_count == 1500


TABLE = [
    para("t0", "Table 1 Ratings", 0),
    cell("c00", 0, 0, "Item", table_title="Table 1 Ratings"),
    cell("c01", 0, 1, "Value (kV)", table_title="Table 1 Ratings"),
    cell("c10", 1, 0, "BIL", table_title="Table 1 Ratings", column_header="Item"),
    cell(
        "c11",
        1,
        1,
        "95",
        table_title="Table 1 Ratings",
        column_header="Value",
        column_unit="kV",
    ),
    cell("c20", 2, 0, "Max voltage", table_title="Table 1 Ratings", column_header="Item"),
    cell("c21", 2, 1, "", table_title="Table 1 Ratings", column_header="Value", column_unit="kV"),
    # 세로 병합 이어짐 셀은 글자가 있어도(시작 셀 반복) 줄에 넣지 않는다.
    cell(
        "c30",
        3,
        0,
        "Max voltage",
        table_title="Table 1 Ratings",
        column_header="Item",
        merged_with="c20",
    ),
    cell(
        "c31",
        3,
        1,
        "15",
        table_title="Table 1 Ratings",
        column_header="Value",
        column_unit="kV",
    ),
]


def test_table_rows_use_column_headers_and_title_first() -> None:
    result = chunk_document(doc(*TABLE))
    # v2: 표제와 같은 캡션 문단(t0)은 제목만 청크라 표 청크에 합치고, 같은 글자는 되풀이하지 않는다.
    [table_chunk] = result.chunks
    assert table_chunk.kind == "TABLE" and table_chunk.table_title == "Table 1 Ratings"
    assert table_chunk.text == (
        "Table 1 Ratings\nItem: BIL | Value (kV): 95\nItem: Max voltage\nValue (kV): 15"
    )
    # 머리 행 셀은 열 머리로 쓰였으므로 첫 줄 블록에 함께 둔다.
    assert table_chunk.block_ids == ["t0", "c00", "c01", "c10", "c11", "c20", "c31"]
    assert table_chunk.source_position == Position(paragraph_index=0)
    assert result.skipped_block_ids == ["c21", "c30"]


def test_table_splits_on_row_boundary_and_repeats_title() -> None:
    chunks = chunk_document(doc(*TABLE[1:]), budget=45).chunks
    assert [chunk.text.splitlines()[0] for chunk in chunks] == ["Table 1 Ratings"] * 3
    assert [chunk.text.splitlines()[1:] for chunk in chunks] == [
        ["Item: BIL | Value (kV): 95"],
        ["Item: Max voltage"],
        ["Value (kV): 15"],
    ]


def test_table_without_headers_uses_first_row_as_data() -> None:
    [chunk] = chunk_document(doc(cell("a", 0, 0, "only"), cell("b", 0, 1, "row"))).chunks
    assert chunk.text == "only | row"


def test_footnotes_are_appended_and_deleted_values_left_out() -> None:
    struck = para(
        "p",
        "33% with fans",
        0,
        deleted_values=[DeletedValue(text="50", position=0, origin="STRIKE")],
        footnotes=[Footnote(ref_id="2", note_kind="FOOTNOTE", text="At 115 °C rise.")],
        flags=["HIGHLIGHT"],
    )
    plain = para("q", "Provide fans.", 1, flags=["TEMPLATE_PLACEHOLDER", "HIGHLIGHT"])
    [chunk] = chunk_document(doc(struck, plain)).chunks
    assert chunk.text == "33% with fans\nProvide fans.\n[각주 2] At 115 °C rise."
    assert "50" not in chunk.text and chunk.has_deleted_values
    assert chunk.flags == ["HIGHLIGHT", "TEMPLATE_PLACEHOLDER"]
    assert chunk.char_count == len(chunk.text)
    note_only = para(
        "r",
        "See note.",
        0,
        footnotes=[
            Footnote(
                ref_id="3",
                note_kind="FOOTNOTE",
                text="kept",
                deleted_values=[DeletedValue(text="old", position=0, origin="TRACKED_DELETE")],
            )
        ],
    )
    [noted] = chunk_document(doc(note_only)).chunks
    assert noted.has_deleted_values and "old" not in noted.text


def test_images_and_empty_blocks_are_skipped() -> None:
    image = Block(block_id="img", kind="IMAGE", text="", position=Position(paragraph_index=1))
    deleted_only = para(
        "gone",
        "",
        2,
        deleted_values=[DeletedValue(text="NOT IN CONTRACT", position=0, origin="STRIKE")],
    )
    result = chunk_document(doc(para("p", "Body.", 0), image, deleted_only))
    assert [chunk.block_ids for chunk in result.chunks] == [["p"]]
    assert result.skipped_block_ids == ["img", "gone"]
    assert result.index_version == INDEX_VERSION


def test_pdf_pages_are_ranged() -> None:
    blocks = [
        Block(block_id=f"b{i}", kind="PARAGRAPH", text="x", position=Position(page=page))
        for i, page in enumerate((3, 4))
    ]
    [chunk] = chunk_document(doc(*blocks, kind="PDF")).chunks
    assert (chunk.page_start, chunk.page_end) == (3, 4)


def test_same_input_gives_same_chunks_and_metadata_stays_null() -> None:
    document = doc(*SPEC, *TABLE)
    first, second = chunk_document(document), chunk_document(document)
    assert first == second
    sha = hashlib.sha256(b"synthetic-rfp").hexdigest()
    assert (
        first.chunks[0].chunk_id
        == hashlib.sha256(f"{sha}:{INDEX_VERSION}:0".encode()).hexdigest()[:32]
    )
    assert [chunk.ordinal for chunk in first.chunks] == list(range(len(first.chunks)))
    assert len({chunk.chunk_id for chunk in first.chunks}) == len(first.chunks)
    other = chunk_document(doc(*SPEC, *TABLE, data=b"another-version"))
    assert {c.chunk_id for c in other.chunks}.isdisjoint(c.chunk_id for c in first.chunks)
    assert all(
        chunk.metadata.model_dump()
        == dict.fromkeys(("document_kind", "product_family", "access_scope", "version"))
        for chunk in first.chunks
    )


def test_hintless_clause_numbers_become_section_boundaries() -> None:
    """v2: 제목·번호 힌트가 없는 문단(PDF에서 바꾼 DOCX)은 줄머리 조항 번호로 경계를 찾는다.

    SECTION·PART·조(2.5)까지만 경계이고 A.·1.은 본문이다. '2.5' 머리가 청크 시작이 된다.
    """
    long = "x" * 300
    result = chunk_document(
        doc(
            para("s", "SECTION 261116.11 - SWITCHGEAR", 0),
            para("p2", "PART 2 - PRODUCTS", 1),
            para("a24", "2.4 TRANSFORMER", 2),
            para("b24", f"A. Ratings: {long}.", 3),
            para("a25", "2.5 EPOXY CAST RESIN", 4),
            para("b25", f"A. Coils shall be cast. {long}.", 5),
            para("c25", f"1. Vacuum cast. {long}.", 6),
        )
    )
    starts = [(chunk.section_path, chunk.text.splitlines()[0]) for chunk in result.chunks]
    assert starts == [
        (
            [
                "SECTION 261116.11 - SWITCHGEAR",
                "PART 2 - PRODUCTS",
                "2.4 TRANSFORMER",
            ],
            "SECTION 261116.11 - SWITCHGEAR",
        ),
        (
            [
                "SECTION 261116.11 - SWITCHGEAR",
                "PART 2 - PRODUCTS",
                "2.5 EPOXY CAST RESIN",
            ],
            "2.5 EPOXY CAST RESIN",
        ),
    ]
    assert result.chunks[1].block_ids == ["a25", "b25", "c25"]


def test_title_only_chunks_and_numbered_layout_tables_join_the_next_text() -> None:
    """v2: 제목만 든 짧은 TEXT와 번호 칸 레이아웃 표는 따로 두지 않고 다음 TEXT 청크 앞에 합친다."""
    layout = [
        cell("l0", 0, 0, "1."),
        cell("l1", 0, 1, "ABB."),
        cell("l2", 1, 0, "2."),
        cell("l3", 1, 1, "Eaton."),
    ]
    result = chunk_document(
        doc(
            numbered("h", "Manufacturers", 0, "2.2", 1),
            *layout,
            para("p", "Provide equipment from the listed manufacturers.", 1),
        )
    )
    [chunk] = result.chunks
    assert chunk.kind == "TEXT"
    assert chunk.block_ids == ["h", "l0", "l1", "l2", "l3", "p"]
    assert chunk.text.splitlines() == [
        "Manufacturers",
        "1. ABB.",
        "2. Eaton.",
        "Provide equipment from the listed manufacturers.",
    ]
