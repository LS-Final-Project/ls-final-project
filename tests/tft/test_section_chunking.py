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
from backend.engine.search.chunking import (
    INDEX_VERSION,
    chunk_document,
    is_document_listing,
    search_context,
)
from backend.engine.search.model import ChunkMetadata


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
        chunk.metadata.model_dump() == dict.fromkeys(ChunkMetadata.model_fields)
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


def test_decimal_sentences_are_not_clause_boundaries() -> None:
    """힌트 없는 문단은 제목꼴(80자 이하·끝 문장부호 없음)일 때만 번호 경계다. 소수로 시작하는 본문은 아니다."""
    result = chunk_document(
        doc(
            para("a25", "2.5 EPOXY CAST RESIN", 0),
            para("body", "2.5 mm 이상의 전선은 단말 처리를 한다.", 1),
            para("long", "3.5 kV " + "x" * 90, 2),
        )
    )
    [chunk] = result.chunks
    assert chunk.section_path == ["2.5 EPOXY CAST RESIN"]
    assert chunk.block_ids == ["a25", "body", "long"]


def test_short_trailing_line_stays_in_its_own_section() -> None:
    """섹션 머리가 아닌 짧은 줄은 다음 섹션 청크에 붙지 않고 제 섹션 경로로 남는다(섹션 머리는 합친다)."""
    result = chunk_document(
        doc(
            para("a24", "2.4 TRANSFORMER", 0),
            para("body", "Provide a cast resin transformer rated 630 kVA.", 1),
            para("note", "See drawings E-101", 2),
            para("a25", "2.5 SWITCHGEAR", 3),
            para("body2", "Provide metal-enclosed switchgear rated 15 kV.", 4),
        )
    )
    assert [(c.section_path, c.block_ids) for c in result.chunks] == [
        (["2.4 TRANSFORMER"], ["a24", "body", "note"]),
        (["2.5 SWITCHGEAR"], ["a25", "body2"]),
    ]
    split = chunk_document(
        doc(
            para("a24", "2.4 TRANSFORMER", 0),
            para("body", "x" * 1190 + ".", 1),
            para("note", "See drawings E-101", 2),
            para("a25", "2.5 SWITCHGEAR", 3),
            para("body2", "Provide metal-enclosed switchgear rated 15 kV.", 4),
        )
    )
    # 예산으로 갈라진 짧은 꼬리 줄도 2.5 청크에 붙지 않고 2.4 경로로 따로 남는다.
    assert [(c.section_path, c.block_ids) for c in split.chunks] == [
        (["2.4 TRANSFORMER"], ["a24", "body"]),
        (["2.4 TRANSFORMER"], ["note"]),
        (["2.5 SWITCHGEAR"], ["a25", "body2"]),
    ]


def markdown(*texts: str) -> Any:
    blocks = [para(f"b{i}", text, i) for i, text in enumerate(texts)]
    return build_document("MARKDOWN", b"md", blocks, [], tables=0)


def test_markdown_front_matter_and_the_notice_after_it_are_not_indexed():
    """머리말과 바로 뒤 인용 문단은 뺀다. 본문의 인용은 남기고 머리말만 있는 문서는 빈다."""
    yaml = "---\ndoc_id: X\ntitle: Y\n---"
    body = "본문 문단은 사양과 절차를 충분히 길게 설명하는 문장이다."
    result = chunk_document(markdown(yaml, "> 합성 안내", body, "> 본문 인용 문장이다."))
    assert result.skipped_block_ids[:2] == ["b0", "b1"]
    assert all("doc_id" not in c.text and "합성 안내" not in c.text for c in result.chunks)
    assert "본문 인용" in "".join(c.text for c in result.chunks)
    # YAML 안의 빈 줄로 단락이 갈려도 닫는 줄까지 머리말이다.
    split = chunk_document(markdown("---\ndoc_id: X", "title: Y\n---", body))
    assert split.skipped_block_ids[:2] == ["b0", "b1"] and body in split.chunks[0].text
    alone = chunk_document(markdown("---\ndoc_id: X", "title: Y", "---", body))
    assert alone.skipped_block_ids[:3] == ["b0", "b1", "b2"] and body in alone.chunks[0].text


def test_only_a_closed_markdown_front_matter_is_dropped():
    """닫는 줄이 없으면 머리말이 아니고, DOCX는 같은 글자여도 그대로 둔다."""
    text = "---\n닫는 줄이 없는 문단이고 본문으로 충분히 긴 문장이다."
    assert chunk_document(markdown(text)).chunks
    yaml = "---\ndoc_id: X\n---"
    blocks = [para("b0", yaml, 0), para("b1", "본문 문단은 충분히 긴 문장으로 쓴다.", 1)]
    docx = build_document("DOCX", b"d", blocks, [], tables=0)
    assert any("doc_id" in c.text for c in chunk_document(docx).chunks)


def test_a_long_heading_alone_joins_the_next_chunk():
    """제목 블록만 든 청크는 40자를 넘어도 따로 두지 않는다."""
    title = "Cast Resin Dry-Type Transformer 630kVA (TRF-DRY-630) — Datasheet"
    head = numbered("h1", title, 0, None, 0, heading=True)
    sub_head = numbered("h2", "1. Overview", 1, None, 1, heading=True)
    result = chunk_document(doc(head, sub_head, para("p", "변압기 개요 문장이다.", 2)))
    assert len(result.chunks) == 1 and title in result.chunks[0].text


def test_a_rule_at_the_top_is_not_front_matter():
    """닫는 줄이 뒤에 있어도 값 줄이 아니면 머리말이 아니고, 인용은 바로 뒤 하나만 뺀다."""
    rule = "---\n이 문단은 가로줄 뒤에 쓴 본문이고 뒤에도 가로줄이 또 나온다."
    assert chunk_document(markdown(rule, "---", "뒤 문단도 본문이다.")).skipped_block_ids == []
    body = "본문 문단은 사양과 절차를 충분히 길게 설명하는 문장이다."
    result = chunk_document(markdown("---\na: 1\n---", "> 안내", "> 둘째 인용 문장이다.", body))
    assert result.skipped_block_ids == ["b0", "b1"]


def test_search_context_holds_document_identity_without_repeats():
    heading = numbered("h", "1. Overview", 0, None, 0, heading=True)
    chunk = chunk_document(doc(heading, para("p", "개요 문단이다.", 1))).chunks[0]
    metadata = ChunkMetadata(title="T", product_code="P-1", document_kind="TG", product_family="F")
    assert search_context(chunk, metadata) == "T P-1 TG F 1. Overview"
    assert search_context(chunk, ChunkMetadata(title="1. Overview")) == "1. Overview"


def test_only_a_chunk_of_nothing_but_document_names_is_a_listing():
    ids = frozenset({"IG-A", "TG-A"})
    heading = numbered("h", "6. Other", 0, None, 0, heading=True)

    def chunk(*lines: str) -> Any:
        blocks = [para(f"p{i}", line, i + 1) for i, line in enumerate(lines)]
        return chunk_document(doc(heading, *blocks)).chunks[0]

    assert is_document_listing(chunk("- IG-A — 설치", "- TG-A — 장애"), ids)
    assert not is_document_listing(chunk("- IG-A — 설치"), ids)  # 줄 하나는 본문 인용일 수 있다
    assert not is_document_listing(chunk("- IG-A — 설치", "차단기를 내린 뒤 점검한다."), ids)
    assert not is_document_listing(chunk("- IG-A — 설치", "- TG-A — 장애"), frozenset())
