"""KAN-96 DOCX·PDF 네이티브 추출. 픽스처는 코드로 만든다(바이너리 커밋 없음, 실제 RFP 없음)."""

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from backend.engine.documents.docx import extract_docx
from backend.engine.documents.model import ExtractedDocument
from backend.engine.documents.pdf import MAX_BYTES, MAX_PAGES, extract_pdf
from backend.engine.documents.router import DOCX_MIME, extract

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
)


def docx(body: str, **parts: str) -> bytes:
    """본문 XML과 추가 부분으로 최소 DOCX를 만든다.

    Args:
        body: w:body 안 XML.
        parts: 부분 경로(점은 "_"로) → XML. 예 word_styles_xml.

    Returns:
        DOCX 바이트.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml", f"<w:document {NS}><w:body>{body}</w:body></w:document>"
        )
        for name, xml in parts.items():
            archive.writestr(name.replace("_xml", ".xml").replace("_", "/"), xml)
    return buffer.getvalue()


def run(text: str, props: str = "") -> str:
    return f'<w:r><w:rPr>{props}</w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'


def para(*runs: str, props: str = "") -> str:
    return f"<w:p><w:pPr>{props}</w:pPr>{''.join(runs)}</w:p>"


STRIKE = "<w:strike/>"


def only(document: ExtractedDocument) -> dict:
    [block] = [b for b in document.blocks if b.kind == "PARAGRAPH"]
    return block.model_dump()


def test_struck_value_is_history_not_current_text() -> None:
    document = extract_docx(docx(para(run("50", STRIKE), run("33% with fans"))))
    block = only(document)
    assert block["text"] == "33% with fans"
    assert block["deleted_values"] == [{"text": "50", "position": 0, "origin": "STRIKE"}]
    assert document.edit_trace_support is True and document.summary.strike_runs == 1


def test_struck_parenthesis_split_over_runs_counts_once() -> None:
    body = para(
        run("EXECUTION "), run("(NOT ", STRIKE), run("IN CONTRACT)", '<w:dstrike w:val="1"/>')
    )
    document = extract_docx(docx(body))
    block = only(document)
    assert block["text"] == "EXECUTION"
    assert block["deleted_values"] == [
        {"text": "(NOT IN CONTRACT)", "position": 9, "origin": "STRIKE"}
    ]
    assert document.summary.strike_runs == 1


def test_strike_turned_off_is_current_text() -> None:
    block = only(extract_docx(docx(para(run("kept", '<w:strike w:val="0"/>')))))
    assert block["text"] == "kept" and block["deleted_values"] == []


def test_tracked_delete_is_kept_apart_from_strike() -> None:
    body = para(
        run("Rated "),
        "<w:del><w:r><w:delText>40</w:delText></w:r></w:del>",
        f"<w:ins>{run('38')}</w:ins>",
        run(" kA"),
    )
    document = extract_docx(docx(body))
    block = only(document)
    assert block["text"] == "Rated 38 kA"
    assert block["deleted_values"] == [{"text": "40", "position": 6, "origin": "TRACKED_DELETE"}]
    assert document.summary.strike_runs == 0


def test_placeholder_highlight_and_line_break_flags() -> None:
    body = para(run("&lt;Insert value&gt;"), run(" kV")) + para(
        run("Owner "), run("approval", '<w:highlight w:val="yellow"/>'), run(" required")
    )
    body += para("<w:r><w:t>first</w:t><w:br/><w:t>second</w:t></w:r>")
    document = extract_docx(docx(body))
    placeholder, highlighted, joined = (b for b in document.blocks if b.kind == "PARAGRAPH")
    assert placeholder.text == "<Insert value> kV" and placeholder.flags == ["TEMPLATE_PLACEHOLDER"]
    assert highlighted.flags == ["HIGHLIGHT"] and highlighted.text == "Owner approval required"
    assert joined.text == "first second" and joined.flags == ["LINE_BREAK_REJOINED"]
    assert document.summary.placeholders == 1 and document.summary.highlight_runs == 1


def cell(text: str, props: str = "") -> str:
    return f"<w:tc><w:tcPr>{props}</w:tcPr>{para(run(text)) if text else '<w:p/>'}</w:tc>"


def test_table_cells_keep_merges_and_empty_cells() -> None:
    span, restart, cont = '<w:gridSpan w:val="2"/>', '<w:vMerge w:val="restart"/>', "<w:vMerge/>"
    rows = [
        cell("Item", span) + cell("Value"),
        cell("Breaker", restart) + cell("") + cell("1200 A"),
        cell("", cont) + cell("Frame") + cell(""),
    ]
    table = "<w:tbl>" + "".join(f"<w:tr>{row}</w:tr>" for row in rows) + "</w:tbl>"
    document = extract_docx(docx(table))
    cells = {(b.position.row, b.position.col): b for b in document.blocks}
    assert document.summary.tables == 1 and len(cells) == 8
    assert cells[(0, 0)].cell_state.col_span == 2  # type: ignore[union-attr]
    assert cells[(0, 2)].text == "Value"
    origin = cells[(1, 0)]
    assert cells[(2, 0)].cell_state.merged_with == origin.block_id  # type: ignore[union-attr]
    assert origin.cell_state.merged_with is None  # type: ignore[union-attr]
    assert cells[(1, 1)].cell_state.is_empty is True  # type: ignore[union-attr]
    assert cells[(2, 1)].text == "Frame" and cells[(2, 1)].position.page is None


def test_images_textboxes_equations_footnotes_and_headers_are_listed_not_read() -> None:
    image = "<w:r><w:drawing><a:graphic><a:blip/></a:graphic></w:drawing></w:r>"
    textbox = (
        f"<w:r><w:pict><w:txbxContent>{para(run('hidden box text'))}</w:txbxContent></w:pict></w:r>"
    )
    body = para(run("See figure"), image, textbox, "<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>")
    body += para(run("Note"), '<w:r><w:footnoteReference w:id="1"/></w:r>')
    document = extract_docx(docx(body, word_header1_xml=f"<w:hdr {NS}/>"))
    texts = [b.text for b in document.blocks]
    assert "See figure" in texts and not any("hidden" in t or "x" == t for t in texts)
    kinds = sorted(item.kind for item in document.unprocessed)
    assert kinds == ["EQUATION", "FOOTNOTE", "HEADER", "IMAGE", "TEXTBOX"]
    [image_block] = [b for b in document.blocks if b.kind == "IMAGE"]
    assert image_block.position.paragraph_index == 0
    assert any(
        i.kind == "IMAGE" and i.location == image_block.block_id for i in document.unprocessed
    )
    assert document.summary.images == 1 and document.summary.unprocessed == 5


STYLES = f"""<w:styles {NS}>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="SpecBase"><w:name w:val="Spec Base"/>
  <w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="PRT"><w:name w:val="PRT"/><w:basedOn w:val="SpecBase"/></w:style>
<w:style w:type="paragraph" w:styleId="ART"><w:name w:val="ART"/><w:basedOn w:val="SpecBase"/></w:style>
<w:style w:type="paragraph" w:styleId="PR1"><w:name w:val="PR1"/><w:basedOn w:val="SpecBase"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
</w:styles>"""
NUMBERING = f"""<w:numbering {NS}>
<w:abstractNum w:abstractNumId="7">
  <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:pStyle w:val="PRT"/>
    <w:lvlText w:val="PART %1 -"/></w:lvl>
  <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:pStyle w:val="ART"/>
    <w:lvlText w:val="%1.%2"/></w:lvl>
  <w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="upperLetter"/><w:pStyle w:val="PR1"/>
    <w:lvlText w:val="%3."/></w:lvl>
  <w:lvl w:ilvl="3"><w:numFmt w:val="chicago"/><w:lvlText w:val="%4"/></w:lvl>
</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="7"/></w:num>
</w:numbering>"""


def styled(style: str, text: str) -> str:
    return para(run(text), props=f'<w:pStyle w:val="{style}"/>')


def test_style_linked_numbering_through_based_on_chain() -> None:
    body = "".join(
        styled(style, text)
        for style, text in [
            ("PRT", "GENERAL"),
            ("ART", "SUMMARY"),
            ("PR1", "Section includes"),
            ("PR1", "Related sections"),
            ("ART", "SUBMITTALS"),
            ("PR1", "Product data"),
            ("Heading1", "Title"),
        ]
    )
    body += para(run("odd"), props='<w:numPr><w:ilvl w:val="3"/><w:numId w:val="1"/></w:numPr>')
    body += para(run("missing"), props='<w:numPr><w:numId w:val="9"/></w:numPr>')
    document = extract_docx(docx(body, word_styles_xml=STYLES, word_numbering_xml=NUMBERING))
    hints = [b.hints for b in document.blocks]
    assert [h.list_text for h in hints] == [  # type: ignore[union-attr]
        "PART 1 -", "1.1", "A.", "B.", "1.2", "A.", None, None, None,
    ]  # fmt: skip
    assert [h.numbering_level for h in hints[:3]] == [0, 1, 2]  # type: ignore[union-attr]
    assert hints[2].style_name == "PR1"  # type: ignore[union-attr]
    assert hints[6].is_heading and not hints[0].is_heading  # type: ignore[union-attr]
    # 계산하지 못해도 원래 번호 참조는 남는다.
    assert hints[7].numbering_raw.model_dump() == {"num_id": "1", "ilvl": 3}  # type: ignore[union-attr]
    assert hints[8].numbering_raw.model_dump() == {"num_id": "9", "ilvl": 0}  # type: ignore[union-attr]
    assert hints[6].numbering_raw is None  # type: ignore[union-attr]


def pdf(pages: list[str], image_on: int | None = None) -> bytes:
    """페이지마다 한 줄 텍스트를 가진 최소 PDF를 만든다.

    Args:
        pages: 페이지별 텍스트(줄은 "\\n", 빈 줄은 문단 간격).
        image_on: 1x1 그림 XObject를 붙일 페이지 번호(1부터).

    Returns:
        PDF 바이트.
    """
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for number, text in enumerate(pages, start=1):
        # 빈 줄은 문단 사이 세로 간격(30pt)으로 그린다.
        lines = "".join(
            f"({line}) Tj 0 -14 Td " if line else "0 -30 Td " for line in text.split("\n")
        )
        stream = f"BT /F1 12 Tf 72 720 Td {lines}ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content = len(objects)
        xobject = ""
        if number == image_on:
            objects.append(
                "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
                "/BitsPerComponent 8 /Length 1 >>\nstream\n\x00\nendstream"
            )
            xobject = f" /XObject << /Im1 {len(objects)} 0 R >>"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
            f"/Resources << /Font << /F1 3 0 R >>{xobject} >> >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out


def test_pdf_pages_text_and_images() -> None:
    pages = ["Section 2.4 Switchgear\n\nA. Rated 15 kV\ncontinued line", "Rated <Insert value> kV"]
    document = extract(pdf(pages, image_on=2))
    assert document.source_kind == "PDF" and document.edit_trace_support is False
    paragraphs = [b for b in document.blocks if b.kind == "PARAGRAPH"]
    assert [(b.text, b.position.page) for b in paragraphs] == [
        ("Section 2.4 Switchgear", 1),
        ("A. Rated 15 kV\ncontinued line", 1),
        ("Rated <Insert value> kV", 2),
    ]
    assert paragraphs[2].flags == ["TEMPLATE_PLACEHOLDER"] and paragraphs[1].flags == []
    assert all(b.deleted_values == [] for b in document.blocks)
    [image] = [b for b in document.blocks if b.kind == "IMAGE"]
    assert image.position.page == 2 and document.summary.images == 1
    assert [u.kind for u in document.unprocessed] == ["IMAGE"]


def test_router_uses_content_and_rejects_mismatch(tmp_path: Path) -> None:
    data = docx(para(run("hello")))
    path = tmp_path / "spec.docx"
    path.write_bytes(data)
    assert extract(path).source_kind == "DOCX"
    assert extract(data, DOCX_MIME).source_kind == "DOCX"
    with pytest.raises(ValueError, match="선언한 형식"):
        extract(data, "application/pdf")
    renamed = tmp_path / "spec.pdf"
    renamed.write_bytes(data)
    with pytest.raises(ValueError, match="선언한 형식"):
        extract(renamed)
    other_zip = io.BytesIO()
    with zipfile.ZipFile(other_zip, "w") as archive:
        archive.writestr("xl/workbook.xml", "<x/>")
    for unsupported in (b"\xd0\xcf\x11\xe0legacy doc", other_zip.getvalue(), b"plain"):
        with pytest.raises(ValueError, match="DOCX·PDF만"):
            extract(unsupported)


def test_untrusted_docx_parts_are_rejected() -> None:
    doctype = io.BytesIO()
    with zipfile.ZipFile(doctype, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<!DOCTYPE d [<!ENTITY x "y">]><w:document ' + NS + "><w:body/></w:document>",
        )
    with pytest.raises(ValueError, match="DOCTYPE"):
        extract_docx(doctype.getvalue())
    broken = io.BytesIO()
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("word/document.xml", "<w:document")
    with pytest.raises(ValueError, match="해석할 수 없"):
        extract_docx(broken.getvalue())


def test_moved_text_is_current_and_unknown_elements_are_listed() -> None:
    body = para(
        '<w:bookmarkStart w:id="0" w:name="x"/>',
        run("Provide "),
        f"<w:moveTo>{run('moved clause')}</w:moveTo>",
        f"<w:moveFrom>{run('old place')}</w:moveFrom>",
        "<w:unknownWrapper/>",
    )
    document = extract_docx(docx(body))
    assert only(document)["text"] == "Provide moved clause"
    assert [(u.kind, u.reason) for u in document.unprocessed] == [
        ("OTHER", "읽지 않은 요소 unknownWrapper")
    ]


def test_empty_numbered_paragraph_still_uses_a_number() -> None:
    body = styled("PR1", "first") + para(props='<w:pStyle w:val="PR1"/>') + styled("PR1", "third")
    document = extract_docx(docx(body, word_styles_xml=STYLES, word_numbering_xml=NUMBERING))
    assert [b.hints.list_text for b in document.blocks] == ["A.", "C."]  # type: ignore[union-attr]


def test_cell_text_inside_content_control_is_read() -> None:
    wrapped = f"<w:sdt><w:sdtPr/><w:sdtContent>{para(run('600 A'))}</w:sdtContent></w:sdt>"
    table = f"<w:tbl><w:tr>{cell('Rating')}<w:tc>{wrapped}</w:tc></w:tr></w:tbl>"
    document = extract_docx(docx(table))
    assert [b.text for b in document.blocks] == ["Rating", "600 A"]
    assert document.unprocessed == []


def test_router_ignores_mime_parameters_and_generic_type() -> None:
    assert extract(pdf(["one"]), "application/pdf; charset=binary").source_kind == "PDF"
    assert extract(docx(para(run("x"))), "application/octet-stream").source_kind == "DOCX"
    with pytest.raises(ValueError, match="선언한 형식"):
        extract(pdf(["one"]), f"{DOCX_MIME}; charset=binary")


def test_pdf_limits_and_unexpected_reader_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="크기 상한"):
        extract_pdf(b"%PDF-" + b"0" * MAX_BYTES)
    with pytest.raises(ValueError, match="페이지 수 상한"):
        extract_pdf(pdf(["p"] * (MAX_PAGES + 1)))

    def explode(stream: object) -> None:
        raise RecursionError("deep")

    monkeypatch.setattr("backend.engine.documents.pdf.PdfReader", explode)
    with pytest.raises(ValueError, match="읽을 수 없"):
        extract_pdf(pdf(["one"]))


def test_upload_size_cap_and_utf16_doctype_are_rejected() -> None:
    oversized = docx(para(run("x")))
    with pytest.raises(ValueError, match="크기 상한"):
        extract(oversized + b"\0" * (MAX_BYTES - len(oversized) + 1))
    utf16 = io.BytesIO()
    with zipfile.ZipFile(utf16, "w") as archive:
        xml = f'<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d><w:document {NS}><w:body/></w:document>'
        archive.writestr("word/document.xml", xml.encode("utf-16"))
    with pytest.raises(ValueError, match="DOCTYPE"):
        extract_docx(utf16.getvalue())


def note(tag: str, ref_id: str, *runs: str, kind: str = "") -> str:
    attribute = f' w:type="{kind}"' if kind else ""
    return f'<w:{tag} w:id="{ref_id}"{attribute}>{para(*runs)}</w:{tag}>'


def test_footnotes_attach_in_reference_order_with_edit_traces() -> None:
    body = para(
        run("Rating"),
        '<w:r><w:footnoteReference w:id="2"/></w:r>',
        run(" and test"),
        '<w:r><w:footnoteReference w:id="1"/></w:r>',
        '<w:r><w:endnoteReference w:id="1"/></w:r>',
        '<w:r><w:footnoteReference w:id="9"/></w:r>',
    )
    footnotes = (
        f"<w:footnotes {NS}>"
        + note("footnote", "-1", run("----"), kind="separator")
        + note("footnote", "1", run("First note"))
        + note("footnote", "2", run("40", STRIKE), run("38 kA per Owner"))
        + "</w:footnotes>"
    )
    endnotes = f"<w:endnotes {NS}>{note('endnote', '1', run('End note'))}</w:endnotes>"
    document = extract_docx(docx(body, word_footnotes_xml=footnotes, word_endnotes_xml=endnotes))
    block = only(document)
    assert [(n["note_kind"], n["ref_id"], n["text"]) for n in block["footnotes"]] == [
        ("FOOTNOTE", "2", "38 kA per Owner"),
        ("FOOTNOTE", "1", "First note"),
        ("ENDNOTE", "1", "End note"),
    ]
    assert block["footnotes"][0]["deleted_values"] == [
        {"text": "40", "position": 0, "origin": "STRIKE"}
    ]
    assert document.summary.strike_runs == 1
    [missing] = document.unprocessed
    assert missing.kind == "FOOTNOTE" and missing.location == block["block_id"]


def table_xml(rows: list[str], caption: str = "") -> str:
    props = f'<w:tblPr><w:tblCaption w:val="{caption}"/></w:tblPr>' if caption else ""
    return f"<w:tbl>{props}" + "".join(f"<w:tr>{row}</w:tr>" for row in rows) + "</w:tbl>"


def test_table_title_and_column_headers_with_units() -> None:
    span = '<w:gridSpan w:val="2"/>'
    body = table_xml(
        [
            cell("Item") + cell("Voltage (kV)") + cell("Remarks (see note)"),
            cell("Bus") + cell("15") + cell(""),
        ],
        caption="Ratings",
    )
    body += para(run("Table 2-1 Breakers")) + "<w:p/>"
    body += table_xml(
        [cell("Item") + cell("Withstand [kA]", span), cell("Main") + cell("38") + cell("2 s")]
    )
    body += para(run("Notes follow")) + table_xml([cell("A"), cell("1")])
    document = extract_docx(docx(body))
    cells = {
        (b.position.table_index, b.position.row, b.position.col): b
        for b in document.blocks
        if b.kind == "TABLE_CELL"
    }
    assert {b.table_title for (t, _, _), b in cells.items() if t == 0} == {"Ratings"}
    assert (cells[(0, 1, 1)].column_header, cells[(0, 1, 1)].column_unit) == ("Voltage", "kV")
    assert (cells[(0, 1, 2)].column_header, cells[(0, 1, 2)].column_unit) == (
        "Remarks (see note)",
        None,
    )
    assert cells[(0, 0, 1)].column_header is None
    assert cells[(1, 1, 0)].table_title == "Table 2-1 Breakers"
    # 가로 병합 머리 셀은 덮는 열 모두의 머리가 된다.
    for col in (1, 2):
        assert (cells[(1, 1, col)].column_header, cells[(1, 1, col)].column_unit) == (
            "Withstand",
            "kA",
        )
    assert cells[(2, 1, 0)].table_title is None and cells[(2, 1, 0)].column_header == "A"


def test_cross_references_resolve_only_same_document_clauses() -> None:
    body = styled("PRT", "GENERAL") + styled("ART", "SUMMARY")
    body += para(run("2.6 SWITCHGEAR")) + para(run("2.4 E.1.c Power frequency withstand"))
    body += para(
        run(
            "Refer to Section 1.1 and see 2.6; comply with 2.4 E.1.c, Drawing E-101, Addendum B, "
            "Section 26 13 26 and Division 26. Efficiency 2.5% is not a reference."
        )
    )
    document = extract_docx(docx(body, word_styles_xml=STYLES, word_numbering_xml=NUMBERING))
    by_text = {b.text: b for b in document.blocks}
    assert by_text["2.4 E.1.c Power frequency withstand"].cross_references == []
    references = [
        (r.text, r.target_kind, r.target_hint, r.resolved, r.target_block_id)
        for r in document.blocks[-1].cross_references
    ]
    assert references == [
        ("Section 1.1", "CLAUSE", "1.1", True, by_text["SUMMARY"].block_id),
        ("see 2.6", "CLAUSE", "2.6", True, by_text["2.6 SWITCHGEAR"].block_id),
        ("2.4 E.1.c", "CLAUSE", "2.4 E.1.c", False, None),
        ("Drawing E-101", "DRAWING", "E-101", False, None),
        ("Addendum B", "DOCUMENT", "B", False, None),
        ("Section 26 13 26", "DOCUMENT", "26 13 26", False, None),
        ("Division 26", "OTHER", "26", False, None),
    ]


def test_document_identity_matches_input_bytes(tmp_path: Path) -> None:
    data = docx(para(run("hello")))
    document = extract(data)
    assert document.sha256 == hashlib.sha256(data).hexdigest() and document.size == len(data)
    assert document.mime == DOCX_MIME and document.source_name is None
    path = tmp_path / "261116.11 Unit Substations.docx"
    path.write_bytes(data)
    assert extract(path).source_name == path.name
    assert extract(data, source_name="upload.docx").source_name == "upload.docx"
    pdf_data = pdf(["one"])
    assert extract(pdf_data, "application/pdf; charset=binary").model_dump(
        include={"sha256", "size", "mime"}
    ) == {
        "sha256": hashlib.sha256(pdf_data).hexdigest(),
        "size": len(pdf_data),
        "mime": "application/pdf",
    }


def test_deep_nesting_and_oversized_path_are_rejected_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deep = "<w:ins>" * 5000 + run("x") + "</w:ins>" * 5000
    with pytest.raises(ValueError, match="중첩"):
        extract_docx(docx(f"<w:p>{deep}</w:p>"))
    large = tmp_path / "large.pdf"
    with large.open("wb") as handle:
        handle.truncate(MAX_BYTES + 1)

    def refuse(self: Path) -> bytes:
        raise AssertionError("크기 확인 전에 읽음")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    with pytest.raises(ValueError, match="크기 상한"):
        extract(large)
