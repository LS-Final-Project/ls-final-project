"""KAN-151 원문·데이터 교체 회귀. 형식이 달라도 같은 계약, 참조·판본·표 셀 불변식, 검색 인용 차단을 고정한다.

픽스처는 코드로 만든다(바이너리·실제 RFP 없음). DOCX·PDF 생성 헬퍼는 KAN-96 추출 검사에서 가져온다.
"""

import hashlib

import pytest

from backend.engine.documents.model import ExtractedDocument
from backend.engine.documents.router import extract
from backend.engine.search import chunking
from backend.engine.search.chunking import chunk_document
from backend.engine.search.hybrid import run_hybrid
from backend.engine.search.model import Candidate, SearchFilters
from tests.tft.test_document_extraction import STRIKE, cell, docx, para, pdf, run, styled, table_xml

LINES = ["SCOPE", "Breakers shall be rated 4000 A."]


def same_content() -> list[ExtractedDocument]:
    """같은 두 문단을 DOCX와 PDF로 만들어 추출한다.

    Returns:
        list[ExtractedDocument]: [DOCX 추출, PDF 추출].
    """
    return [
        extract(docx("".join(para(run(line)) for line in LINES)), source_name="spec.docx"),
        extract(pdf(["\n\n".join(LINES)]), source_name="spec.pdf"),
    ]


def test_docx_and_pdf_meet_the_same_document_contract() -> None:
    """documents.router.extract 경계: 형식이 달라도 식별·블록 필수 필드와 현행 텍스트가 같다."""
    docx_doc, pdf_doc = same_content()
    for document, kind, mime in (
        (
            docx_doc,
            "DOCX",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (pdf_doc, "PDF", "application/pdf"),
    ):
        assert (document.source_kind, document.mime) == (kind, mime)
        assert len(document.sha256) == 64 and document.size > 0
        assert document.source_name == f"spec.{kind.lower()}"
        ids = [block.block_id for block in document.blocks]
        assert len(set(ids)) == len(ids) and all(ids)
        assert all(
            block.kind and block.position.model_dump(exclude_none=True) for block in document.blocks
        )
    assert [(b.kind, b.text) for b in docx_doc.blocks] == [(b.kind, b.text) for b in pdf_doc.blocks]


def test_chunk_ids_are_deterministic_and_follow_the_index_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search.chunking 경계: 같은 입력은 같은 chunk_id, INDEX_VERSION이 바뀌면 모든 chunk_id가 바뀐다."""
    for document in same_content():
        first, again = chunk_document(document), chunk_document(document)
        assert [c.chunk_id for c in first.chunks] == [c.chunk_id for c in again.chunks]
        expected = [
            hashlib.sha256(
                f"{document.sha256}:{first.index_version}:{c.ordinal}".encode()
            ).hexdigest()[:32]
            for c in first.chunks
        ]
        assert [c.chunk_id for c in first.chunks] == expected
        monkeypatch.setattr(chunking, "INDEX_VERSION", "chunking-v-next")
        bumped = chunk_document(document)
        monkeypatch.undo()
        assert bumped.index_version == "chunking-v-next"
        assert not {c.chunk_id for c in bumped.chunks} & {c.chunk_id for c in first.chunks}
    docx_chunks, pdf_chunks = (chunk_document(d) for d in same_content())
    assert [c.text for c in docx_chunks.chunks] == [c.text for c in pdf_chunks.chunks]


def mixed_document() -> ExtractedDocument:
    """해결·미해결 참조, 세로 병합 표, 단위 머리, 취소선·추적 삭제가 섞인 문서를 추출한다.

    Returns:
        ExtractedDocument: 추출 결과.
    """
    restart, cont = '<w:vMerge w:val="restart"/>', "<w:vMerge/>"
    body = styled("Heading1", "2.6 SWITCHGEAR")
    body += para(run("Comply with see 2.6, see 9.9 and Drawing E-101."))
    body += table_xml(
        [
            cell("Item")
            + cell("Rating (V)")
            + cell("Power (W)")
            + cell("Resistance (Ω)")
            + cell("Class (C)")
            + cell("Code (2)"),
            cell("Main", restart) + cell("480") + cell("10") + cell("5") + cell("x") + cell("y"),
            cell("", cont) + cell("600") + cell("12") + cell("6") + cell("x") + cell("y"),
        ],
        caption="Ratings",
    )
    body += table_xml([cell("Feeder", restart) + cell("Id"), cell("", cont) + cell("7")])
    body += para(run("Rated "), run("40", STRIKE), run(" 38 kA"))
    body += para(
        run("Delay "),
        "<w:del><w:r><w:delText>500</w:delText></w:r></w:del>",
        run("300 ms"),
    )
    return extract(docx(body))


def test_unresolved_references_never_point_at_a_block() -> None:
    """documents.references 경계: 미해결 참조는 target_block_id가 없고 해결 참조는 실제 블록을 가리킨다."""
    document = mixed_document()
    ids = {block.block_id for block in document.blocks}
    references = [r for block in document.blocks for r in block.cross_references]
    assert {r.target_hint for r in references if not r.resolved} >= {"9.9", "E-101"}
    assert [r.target_hint for r in references if r.resolved] == ["2.6"]
    for reference in references:
        assert (reference.target_block_id is None) == (not reference.resolved)
        assert reference.target_block_id is None or reference.target_block_id in ids


def test_merged_cells_point_at_an_origin_in_the_same_table() -> None:
    """documents.docx 표 경계: merged_with는 같은 표·같은 열의 실제 시작 셀 block_id다."""
    cells = [b for b in mixed_document().blocks if b.kind == "TABLE_CELL"]
    by_id = {b.block_id: b for b in cells}
    merged = [b for b in cells if b.cell_state and b.cell_state.merged_with]
    assert len(merged) == 2
    for block in merged:
        origin = by_id[block.cell_state.merged_with]  # type: ignore[union-attr,index]
        assert origin.position.table_index == block.position.table_index
        assert origin.position.col == block.position.col
        assert origin.cell_state.merged_with is None  # type: ignore[union-attr]


def test_single_letter_units_are_only_a_v_w_ohm() -> None:
    """documents.docx 단위 경계: 한 글자 단위는 A·V·W·Ω만, 다른 대문자·숫자 괄호는 단위가 아니다."""
    data = [
        b
        for b in mixed_document().blocks
        if b.kind == "TABLE_CELL" and b.position.table_index == 0 and b.position.row == 1
    ]
    assert [(b.column_header, b.column_unit) for b in data[1:]] == [
        ("Rating", "V"),
        ("Power", "W"),
        ("Resistance", "Ω"),
        ("Class (C)", None),
        ("Code (2)", None),
    ]


def test_deleted_values_never_leak_into_current_text() -> None:
    """documents.docx 판본 경계: 취소선·추적 삭제 값은 text에 없고 deleted_values에만 있다."""
    paragraphs = {b.text: b for b in mixed_document().blocks if b.kind == "PARAGRAPH"}
    struck, tracked = paragraphs["Rated 38 kA"], paragraphs["Delay 300 ms"]
    assert [(d.text, d.origin) for d in struck.deleted_values] == [("40", "STRIKE")]
    assert [(d.text, d.origin) for d in tracked.deleted_values] == [("500", "TRACKED_DELETE")]
    assert "40" not in struck.text and "500" not in tracked.text


def candidate(chunk_id: str, position: int, **fields: str | None) -> Candidate:
    """FTS 후보 하나를 만든다.

    Args:
        chunk_id: 청크 ID.
        position: 검색기 안 순위.
        fields: snippet·text_excerpt·table_title.

    Returns:
        Candidate: 후보.
    """
    return Candidate(
        chunk_id=chunk_id,
        document_sha256="a" * 64,
        ordinal=position,
        section_path=[],
        snippet=fields.get("snippet") or "",
        text_excerpt=fields.get("text_excerpt"),
        kind="TEXT",
        table_title=fields.get("table_title"),
        page_start=None,
        source="FTS",
        position=position,
        score=None,
    )


def blocked(candidates: list[Candidate]) -> list[str]:
    """기본 차단 코드(FLT-042)로 결합하고 빠진 chunk_id를 돌려준다.

    Args:
        candidates: FTS 후보.

    Returns:
        list[str]: dropped_blocked.
    """
    result = run_hybrid(
        "leakage",
        SearchFilters(document_sha256s=None, index_version="chunking-v1", limit=10),
        fts=lambda query, filters: candidates,
    )
    assert not {item.candidate.chunk_id for item in result.items} & set(result.dropped_blocked)
    return result.dropped_blocked


def test_flt_042_title_tag_and_parenthesis_variants_are_dropped() -> None:
    """search.hybrid 인용 차단 경계: 표제의 혼합 대소문자, 소문자 강조 태그, 괄호 안 FLT-042를 뺀다."""
    candidates = [
        candidate("title", 1, table_title="Flt-042 sensitivity table"),
        candidate("tagged", 2, snippet="see <b>flt</b>-<b>042</b> advice"),
        candidate("paren", 3, text_excerpt="Recommended (FLT-042)."),
        candidate("clean", 4, text_excerpt="Leakage sensitivity guidance"),
    ]
    assert blocked(candidates) == ["title", "tagged", "paren"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="KAN-151: 문자열 차단이 HTML 엔티티·다른 대시·공백 표기의 FLT-042를 막지 못한다.",
)
@pytest.mark.parametrize(
    "fields",
    [
        {"snippet": "FLT&#45;042 advice"},
        {"text_excerpt": "FLT–042 advice"},
        {"text_excerpt": "FLT 042 advice"},
    ],
    ids=["entity", "en-dash", "space"],
)
def test_flt_042_spelling_variants_should_be_dropped(fields: dict[str, str]) -> None:
    """search.hybrid 인용 차단 경계: 표기만 다른 FLT-042도 빠져야 한다. 지금은 빈틈이라 xfail로 기록한다."""
    assert blocked([candidate("variant", 1, **fields)]) == ["variant"]
