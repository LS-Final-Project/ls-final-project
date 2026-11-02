"""RFP 검토본의 A4 문단·표·근거 부록용 OOXML 레이아웃(KAN-188)."""

from collections.abc import Sequence
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def element(parent: ET.Element, name: str, **attributes: str) -> ET.Element:
    return ET.SubElement(parent, W + name, {W + key: value for key, value in attributes.items()})


def paragraph(parent: ET.Element, text: str, style: str = "Normal") -> ET.Element:
    """사용자 문자열을 자르거나 XML 제어 문자를 버리지 않는다."""
    if any(
        not (
            char in "\n\r\t"
            or 0x20 <= ord(char) <= 0xD7FF
            or 0xE000 <= ord(char) <= 0xFFFD
            or 0x10000 <= ord(char) <= 0x10FFFF
        )
        for char in text
    ):
        raise ValueError("TFT_EXPORT_INVALID_CHARACTER")
    para = element(parent, "p")
    element(element(para, "pPr"), "pStyle", val=style)
    run = element(para, "r")
    for index, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        if index:
            element(run, "br")
        for tab_index, part in enumerate(line.split("\t")):
            if tab_index:
                element(run, "tab")
            node = element(run, "t")
            node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            node.text = part
    return para


def sentence_link(parent: ET.Element, number: int) -> None:
    """내보내기 판본 안의 순번만 anchor로 사용한다(영속 UUID·외부 URL 아님)."""
    para = paragraph(parent, "", "Meta")
    link = element(para, "hyperlink", anchor=f"sentence_{number}")
    run = element(link, "r")
    props = element(run, "rPr")
    element(props, "color", val="24518A")
    element(props, "u", val="single")
    element(run, "t").text = f"근거·조건 → 부록 S{number}"


def sentence_heading(parent: ET.Element, number: int) -> None:
    para = paragraph(parent, f"문장 번호: S{number}", "Heading2")
    start = ET.Element(
        W + "bookmarkStart", {W + "id": str(number), W + "name": f"sentence_{number}"}
    )
    # pPr는 문단의 첫 요소여야 한다.
    para.insert(1, start)
    element(para, "bookmarkEnd", id=str(number))


def table(parent: ET.Element, headings: Sequence[str], widths: Sequence[int]) -> ET.Element:
    result = element(parent, "tbl")
    props = element(result, "tblPr")
    element(props, "tblW", w=str(sum(widths)), type="dxa")
    borders = element(props, "tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element(borders, side, val="single", sz="4", color="D8E0E8")
    element(props, "tblLayout", type="fixed")
    margins = element(props, "tblCellMar")
    for side in ("top", "left", "bottom", "right"):
        element(margins, side, w="85", type="dxa")
    grid = element(result, "tblGrid")
    for width in widths:
        element(grid, "gridCol", w=str(width))
    cells = table_row(result, widths, header=True)
    for cell, heading in zip(cells, headings, strict=True):
        paragraph(cell, heading, "TableHeader")
    return result


def table_row(
    table: ET.Element, widths: Sequence[int], *, header: bool = False
) -> list[ET.Element]:
    """행 높이나 행 분할 금지를 지정하지 않아 긴 내용도 페이지를 넘어 보존한다."""
    row = element(table, "tr")
    if header:
        element(element(row, "trPr"), "tblHeader")
    cells = []
    for width in widths:
        cell = element(row, "tc")
        props = element(cell, "tcPr")
        element(props, "tcW", w=str(width), type="dxa")
        if header:
            element(props, "shd", fill="EAF0F6")
        element(props, "vAlign", val="top")
        cells.append(cell)
    return cells


def styles() -> ET.Element:
    root = ET.Element(W + "styles")
    for name, size, color in (
        ("Normal", "20", "233347"),
        ("Title", "36", "153B61"),
        ("Heading1", "26", "153B61"),
        ("Heading2", "22", "153B61"),
        ("Meta", "17", "526275"),
        ("Notice", "18", "804800"),
        ("TableText", "18", "233347"),
        ("TableHeader", "18", "153B61"),
    ):
        style = element(root, "style", type="paragraph", styleId=name)
        element(style, "name", val=name)
        if name != "Normal":
            element(style, "basedOn", val="Normal")
        props = element(style, "pPr")
        if name in {"Title", "Heading1", "Heading2", "TableHeader"}:
            element(props, "keepNext")
        element(props, "widowControl")
        element(
            props,
            "spacing",
            after="100",
            before="180" if name.startswith("Heading") else "0",
            line="290",
            lineRule="auto",
        )
        if name.startswith("Heading"):
            element(props, "outlineLvl", val="0" if name == "Heading1" else "1")
        run = element(style, "rPr")
        element(run, "rFonts", ascii="Calibri", hAnsi="Calibri", eastAsia="맑은 고딕")
        if name in {"Title", "Heading1", "Heading2", "TableHeader", "Notice"}:
            element(run, "b")
        element(run, "color", val=color)
        element(run, "sz", val=size)
        element(run, "szCs", val=size)
        element(run, "lang", val="ko-KR", eastAsia="ko-KR")
    return root


def package_parts(document: ET.Element, body: ET.Element, origin: str) -> dict[str, bytes]:
    """문서 끝의 A4 설정과 고정 내부 relationship만 만든다."""
    section = element(body, "sectPr")
    element(section, "headerReference", type="default").set("{" + R + "}id", "rIdHeader")
    element(section, "footerReference", type="default").set("{" + R + "}id", "rIdFooter")
    element(section, "pgSz", w="11906", h="16838")
    element(
        section,
        "pgMar",
        top="1050",
        bottom="1050",
        left="1213",
        right="1213",
        header="450",
        footer="450",
        gutter="0",
    )
    header = ET.Element(W + "hdr")
    paragraph(header, f"RFP 제안 검토본 | {origin}", "Meta")
    footer = ET.Element(W + "ftr")
    para = paragraph(footer, "검토용 · 고객 제출 전 확인 필요 | ", "Meta")
    field = element(para, "fldSimple", instr=" PAGE ")
    element(element(field, "r"), "t").text = "1"
    types = ET.Element(
        "Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types"
    )
    ET.SubElement(
        types,
        "Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    ET.SubElement(types, "Default", Extension="xml", ContentType="application/xml")
    for name, kind in (
        ("document", "document.main"),
        ("styles", "styles"),
        ("header1", "header"),
        ("footer1", "footer"),
    ):
        ET.SubElement(
            types,
            "Override",
            PartName=f"/word/{name}.xml",
            ContentType=f"application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml",
        )
    relationship_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    root_links = ET.Element("Relationships", xmlns=relationship_ns)
    ET.SubElement(
        root_links,
        "Relationship",
        Id="rId1",
        Type=R + "/officeDocument",
        Target="word/document.xml",
    )
    doc_links = ET.Element("Relationships", xmlns=relationship_ns)
    for id, kind, target in (
        ("rIdStyles", "styles", "styles.xml"),
        ("rIdHeader", "header", "header1.xml"),
        ("rIdFooter", "footer", "footer1.xml"),
    ):
        ET.SubElement(doc_links, "Relationship", Id=id, Type=R + "/" + kind, Target=target)
    return {
        name: ET.tostring(value, encoding="utf-8", xml_declaration=True)
        for name, value in {
            "[Content_Types].xml": types,
            "_rels/.rels": root_links,
            "word/document.xml": document,
            "word/styles.xml": styles(),
            "word/header1.xml": header,
            "word/footer1.xml": footer,
            "word/_rels/document.xml.rels": doc_links,
        }.items()
    }
