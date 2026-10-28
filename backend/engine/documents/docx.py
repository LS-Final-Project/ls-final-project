"""DOCX 본문 추출. 표준 라이브러리(zipfile·xml.etree)로 word/document.xml을 읽는다.

편집 흔적: 런 서식의 취소선(w:strike·w:dstrike)과 변경 추적 삭제(w:del)는 현행 text에서 빼고
deleted_values에 origin과 함께 둔다. 변경 추적 삽입(w:ins)은 현행으로 읽는다.

범위 밖: 문자·문단 스타일에서 상속한 취소선·형광펜(런에 직접 준 서식만 읽는다), 이동 추적(w:moveFrom),
머리글·바닥글·각주·미주·텍스트박스·수식 본문(종류·위치만 unprocessed에 남긴다), 중첩 표.
"""

import io
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from xml.etree import ElementTree as ET

from backend.engine.documents.model import (
    PLACEHOLDER,
    Block,
    CellState,
    DeletedValue,
    ExtractedDocument,
    Flag,
    Hints,
    NumberingRaw,
    Position,
    Unprocessed,
    UnprocessedKind,
    build_document,
)

Origin = Literal["STRIKE", "TRACKED_DELETE"]

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
# 압축 해제 뒤 부분 하나의 상한. 25 MiB 업로드가 비정상적으로 부풀어도 메모리를 지킨다.
PART_LIMIT = 64 * 1024 * 1024
# 안의 런을 현행으로 읽는 래퍼. w:moveTo는 이동 추적의 도착 위치라 현행 본문이다.
PASS_THROUGH = {
    f"{W}{name}"
    for name in (
        "ins", "moveTo", "hyperlink", "smartTag", "sdt", "sdtContent", "fldSimple", "customXml",
        "dir", "bdo",
    )
}  # fmt: skip
# 텍스트가 없는 표식과 범위 밖으로 정한 w:moveFrom(이동 전 위치)은 조용히 넘긴다.
IGNORED = {
    f"{W}{name}"
    for name in (
        "pPr", "rPr", "sdtPr", "sdtEndPr", "customXmlPr", "smartTagPr", "proofErr",
        "bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd",
        "permStart", "permEnd", "moveFrom", "moveFromRangeStart", "moveFromRangeEnd",
        "moveToRangeStart", "moveToRangeEnd",
    )
}  # fmt: skip
CONTAINERS = {f"{W}sdt", f"{W}sdtContent", f"{W}customXml"}
OBJECTS = {f"{W}drawing", f"{W}pict", f"{W}object", f"{MC}AlternateContent"}
HEADER_PART = re.compile(r"^word/(header|footer)\d*\.xml$")


def _read_part(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    """DOCX 안 XML 부분 하나를 상한과 DTD 거부 검사 뒤 읽는다.

    Args:
        archive: 열린 DOCX zip.
        name: 부분 경로(예 word/document.xml).

    Returns:
        루트 요소. 부분이 없으면 None.

    Raises:
        ValueError: 상한 초과, DOCTYPE 포함 또는 XML 오류.
    """
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    with archive.open(info) as stream:
        data = stream.read(PART_LIMIT + 1)
    if len(data) > PART_LIMIT:
        raise ValueError(f"{name}이 크기 상한을 넘습니다.")
    if b"<!DOCTYPE" in data:
        raise ValueError(f"{name}에 DOCTYPE이 있습니다.")
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError(f"{name}을 해석할 수 없습니다.") from error


def _val(element: ET.Element | None, default: str | None = None) -> str | None:
    return default if element is None else element.get(f"{W}val", default)


def _on(props: ET.Element | None, name: str) -> bool:
    """런 서식 토글(w:strike 등)이 켜져 있는지 본다.

    Args:
        props: w:rPr 요소.
        name: 토글 이름.

    Returns:
        요소가 있고 val이 끄는 값이 아니면 True.
    """
    element = None if props is None else props.find(f"{W}{name}")
    return element is not None and _val(element, "true") not in {"0", "false", "off"}


@dataclass
class Style:
    name: str
    based_on: str | None
    num_id: str | None
    ilvl: int | None
    outline: bool


@dataclass
class Level:
    fmt: str
    text: str
    start: int
    style: str | None


def _styles(root: ET.Element | None) -> tuple[dict[str, Style], str | None]:
    """styles.xml에서 문단 스타일의 이름·상속·번호 연결을 읽는다.

    Args:
        root: styles.xml 루트(없으면 None).

    Returns:
        스타일 ID별 정보와 기본 문단 스타일 ID.
    """
    styles: dict[str, Style] = {}
    default = None
    for style in [] if root is None else root.iter(f"{W}style"):
        style_id = style.get(f"{W}styleId")
        if style_id is None or style.get(f"{W}type") != "paragraph":
            continue
        if style.get(f"{W}default") in {"1", "true"}:
            default = style_id
        num = style.find(f"{W}pPr/{W}numPr")
        ilvl = _val(None if num is None else num.find(f"{W}ilvl"))
        styles[style_id] = Style(
            name=_val(style.find(f"{W}name"), style_id) or style_id,
            based_on=_val(style.find(f"{W}basedOn")),
            num_id=_val(None if num is None else num.find(f"{W}numId")),
            ilvl=None if ilvl is None else int(ilvl),
            outline=style.find(f"{W}pPr/{W}outlineLvl") is not None,
        )
    return styles, default


def _letters(value: int) -> str:
    # Word 방식: 26 뒤는 AA, BB…(같은 글자 반복).
    return chr(ord("A") + (value - 1) % 26) * ((value - 1) // 26 + 1)


def _roman(value: int) -> str:
    out = ""
    for number, symbol in (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
        (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ):  # fmt: skip
        count, value = divmod(value, number)
        out += symbol * count
    return out


FORMATS: dict[str, Callable[[int], str]] = {
    "decimal": str,
    "decimalZero": lambda value: f"{value:02d}",
    "upperLetter": _letters,
    "lowerLetter": lambda value: _letters(value).lower(),
    "upperRoman": _roman,
    "lowerRoman": lambda value: _roman(value).lower(),
}


class Numbering:
    """numbering.xml의 자동 번호를 문서 순서대로 계산한다.

    ponytail: 카운터를 numId별로 둔다. 같은 abstractNum을 공유하는 서로 다른 numId 사이의 이어 세기와
    lvlRestart·isLgl은 다루지 않는다. 틀릴 수 있으므로 numbering_raw를 함께 낸다.
    """

    def __init__(self, root: ET.Element | None) -> None:
        self.abstract: dict[str, dict[int, Level]] = {}
        self.nums: dict[str, tuple[str, dict[int, int]]] = {}
        self.counters: dict[str, dict[int, int]] = {}
        for abstract in [] if root is None else root.iter(f"{W}abstractNum"):
            levels = {}
            for level in abstract.iter(f"{W}lvl"):
                levels[int(level.get(f"{W}ilvl", "0"))] = Level(
                    fmt=_val(level.find(f"{W}numFmt"), "decimal") or "decimal",
                    text=_val(level.find(f"{W}lvlText"), "") or "",
                    start=int(_val(level.find(f"{W}start"), "1") or "1"),
                    style=_val(level.find(f"{W}pStyle")),
                )
            self.abstract[abstract.get(f"{W}abstractNumId", "")] = levels
        for num in [] if root is None else root.iter(f"{W}num"):
            overrides = {
                int(item.get(f"{W}ilvl", "0")): int(
                    _val(item.find(f"{W}startOverride"), "1") or "1"
                )
                for item in num.iter(f"{W}lvlOverride")
                if item.find(f"{W}startOverride") is not None
            }
            abstract_id = _val(num.find(f"{W}abstractNumId"), "") or ""
            self.nums[num.get(f"{W}numId", "")] = (abstract_id, overrides)

    def level_for_style(self, num_id: str, style_id: str) -> int | None:
        """스타일에 연결된 번호 수준(abstractNum의 lvl/pStyle)을 찾는다.

        Args:
            num_id: 번호 정의 ID.
            style_id: 문단 스타일 ID.

        Returns:
            그 스타일이 연결된 수준. 없으면 None.
        """
        abstract_id, _ = self.nums.get(num_id, ("", {}))
        levels = self.abstract.get(abstract_id, {})
        return next((ilvl for ilvl, level in levels.items() if level.style == style_id), None)

    def label(self, num_id: str, ilvl: int) -> str | None:
        """번호를 한 칸 진행하고 표시 문자열을 만든다.

        Args:
            num_id: 번호 정의 ID.
            ilvl: 수준(0부터).

        Returns:
            "2.5"·"A." 같은 표시 문자열. 지원하지 않는 형식이거나 계산할 수 없으면 None.
        """
        try:
            abstract_id, overrides = self.nums[num_id]
            levels = self.abstract[abstract_id]
            counters = self.counters.setdefault(num_id, {})
            start = {key: overrides.get(key, level.start) for key, level in levels.items()}
            counters[ilvl] = counters[ilvl] + 1 if ilvl in counters else start[ilvl]
            for deeper in [key for key in counters if key > ilvl]:
                del counters[deeper]
            level = levels[ilvl]
            if level.fmt == "bullet":
                return level.text
            text = level.text
            for index in range(ilvl + 1):
                token = f"%{index + 1}"
                if token in text:
                    value = counters.get(index, start[index])
                    text = text.replace(token, FORMATS[levels[index].fmt](value))
            return text
        except (KeyError, ValueError):
            return None


@dataclass
class Piece:
    text: str
    deleted: Origin | None  # None이면 현행
    highlight: bool


@dataclass
class Collected:
    pieces: list[Piece] = field(default_factory=list)
    line_break: bool = False
    images: int = 0
    unprocessed: list[tuple[UnprocessedKind, str]] = field(default_factory=list)  # (종류, 사유)


def _object(element: ET.Element, out: Collected) -> None:
    """그림·도형·텍스트박스 요소를 분류한다. 안의 텍스트는 문단에 섞지 않는다.

    Args:
        element: w:drawing·w:pict·w:object·mc:AlternateContent.
        out: 문단 수집 상태.
    """
    if element.find(f".//{W}txbxContent") is not None:
        out.unprocessed.append(("TEXTBOX", "텍스트박스·도형 안 텍스트는 읽지 않음"))
    elif any(child.tag.endswith(("}blip", "}imagedata")) for child in element.iter()):
        out.images += 1
    else:
        out.unprocessed.append(("OTHER", "그림이 아닌 개체(도형·OLE)는 읽지 않음"))


def _run(run: ET.Element, out: Collected, tracked_delete: bool) -> None:
    """런 하나의 텍스트를 서식과 함께 모은다.

    Args:
        run: w:r 요소.
        out: 문단 수집 상태.
        tracked_delete: 변경 추적 삭제(w:del) 안의 런인지.
    """
    props = run.find(f"{W}rPr")
    deleted: Origin | None = None
    if tracked_delete:
        deleted = "TRACKED_DELETE"
    elif _on(props, "strike") or _on(props, "dstrike"):
        deleted = "STRIKE"
    highlight = props is not None and _val(props.find(f"{W}highlight"), "none") != "none"
    for child in run:
        tag = child.tag
        if tag in {f"{W}t", f"{W}delText"}:
            out.pieces.append(Piece(child.text or "", deleted, highlight))
        elif tag in {f"{W}tab", f"{W}noBreakHyphen"}:
            out.pieces.append(Piece(" " if tag == f"{W}tab" else "-", deleted, highlight))
        elif tag in {f"{W}br", f"{W}cr"}:
            out.pieces.append(Piece(" ", deleted, highlight))
            out.line_break = True
        elif tag in {f"{W}footnoteReference", f"{W}endnoteReference"}:
            out.unprocessed.append(("FOOTNOTE", "각주·미주 본문은 읽지 않음"))
        elif tag in OBJECTS:
            _object(child, out)


def _collect(container: ET.Element, out: Collected, tracked_delete: bool = False) -> None:
    """문단(또는 그 안 래퍼)의 자식을 문서 순서로 모은다.

    Args:
        container: w:p 또는 w:ins·w:hyperlink 같은 래퍼.
        out: 문단 수집 상태.
        tracked_delete: 변경 추적 삭제 안인지.
    """
    for child in container:
        tag = child.tag
        if tag == f"{W}r":
            _run(child, out, tracked_delete)
        elif tag == f"{W}del":
            _collect(child, out, tracked_delete=True)
        elif tag in PASS_THROUGH:
            _collect(child, out, tracked_delete)
        elif tag in {f"{MATH}oMath", f"{MATH}oMathPara"}:
            out.unprocessed.append(("EQUATION", "수식은 읽지 않음"))
        elif tag in OBJECTS:
            _object(child, out)
        elif tag not in IGNORED:
            # 모르는 요소를 조용히 버리면 본문 누락을 요약에서 알 수 없다.
            out.unprocessed.append(("OTHER", f"읽지 않은 요소 {tag.rsplit('}', 1)[-1]}"))


def _assemble(out: Collected) -> tuple[str, list[DeletedValue], list[Flag], int, int]:
    """모은 조각을 현행 text와 지워진 값으로 나눈다.

    연속 공백은 한 칸으로 줄이고 앞뒤 공백을 지우며, 지워진 값의 위치도 같은 매핑으로 옮긴다.
    이어진 같은 종류의 지워진 조각은 하나로 합친다(런이 쪼개져 있어도 한 곳으로 센다).

    Args:
        out: 문단 수집 상태.

    Returns:
        (현행 text, 지워진 값, 플래그, 취소선 곳 수, 형광펜 구간 수).
    """
    raw = ""
    marks: list[tuple[int, str, Origin]] = []  # (원시 위치, 텍스트, origin)
    highlights = 0
    previous_highlight = False
    for piece in out.pieces:
        if piece.deleted is not None:
            if marks and marks[-1][0] == len(raw) and marks[-1][2] == piece.deleted:
                marks[-1] = (len(raw), marks[-1][1] + piece.text, piece.deleted)
            else:
                marks.append((len(raw), piece.text, piece.deleted))
            continue
        raw += piece.text
        if piece.highlight and piece.text.strip() and not previous_highlight:
            highlights += 1
        previous_highlight = piece.highlight and bool(piece.text.strip())
    chars: list[str] = []
    index: list[int] = []
    for char in raw:
        index.append(len(chars))
        if not char.isspace():
            chars.append(char)
        elif chars and chars[-1] != " ":
            chars.append(" ")
    index.append(len(chars))
    text = "".join(chars).rstrip(" ")
    deleted = [
        DeletedValue(text=value, position=min(index[at], len(text)), origin=origin)
        for at, value, origin in marks
        if value.strip()
    ]
    flags: list[Flag] = []
    if highlights:
        flags.append("HIGHLIGHT")
    if PLACEHOLDER.search(text):
        flags.append("TEMPLATE_PLACEHOLDER")
    if out.line_break:
        flags.append("LINE_BREAK_REJOINED")
    strikes = sum(value.origin == "STRIKE" for value in deleted)
    return text, deleted, flags, strikes, highlights


def _cell_items(container: ET.Element) -> list[ET.Element]:
    """표 셀의 문단·표를 콘텐츠 컨트롤(w:sdt) 안까지 문서 순서로 모은다.

    Args:
        container: w:tc 또는 그 안 래퍼.

    Returns:
        w:p·w:tbl 요소 목록.
    """
    items: list[ET.Element] = []
    for child in container:
        if child.tag in {f"{W}p", f"{W}tbl"}:
            items.append(child)
        elif child.tag in CONTAINERS:
            items.extend(_cell_items(child))
    return items


class _Extractor:
    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.styles, self.default_style = _styles(_read_part(archive, "word/styles.xml"))
        self.numbering = Numbering(_read_part(archive, "word/numbering.xml"))
        self.blocks: list[Block] = []
        self.unprocessed: list[Unprocessed] = []
        self.next_id = 0
        self.paragraph_index = -1
        self.table_index = -1
        self.strikes = 0
        self.highlights = 0

    def new_id(self) -> str:
        self.next_id += 1
        return f"b{self.next_id:04d}"

    def style_chain(self, style_id: str | None) -> list[tuple[str, Style]]:
        chain: list[tuple[str, Style]] = []
        while style_id in self.styles and len(chain) < 20:
            chain.append((style_id, self.styles[style_id]))
            style_id = self.styles[style_id].based_on
        return chain

    def hints(self, paragraph: ET.Element) -> Hints:
        """문단 스타일·자동 번호에서 조항 트리 단서를 만든다.

        Args:
            paragraph: 본문 w:p.

        Returns:
            스타일 이름·번호 수준·계산한 번호·원래 번호 참조·제목 여부.
        """
        props = paragraph.find(f"{W}pPr")
        style_id = _val(None if props is None else props.find(f"{W}pStyle")) or self.default_style
        chain = self.style_chain(style_id)
        num = None if props is None else props.find(f"{W}numPr")
        num_id = _val(None if num is None else num.find(f"{W}numId"))
        ilvl_text = _val(None if num is None else num.find(f"{W}ilvl"))
        ilvl = None if ilvl_text is None else int(ilvl_text)
        # 문단에 번호가 없으면 스타일(basedOn 체인)에 연결된 번호를 쓴다.
        for _, style in chain:
            if num_id is None and style.num_id is not None:
                num_id = style.num_id
                ilvl = style.ilvl if style.ilvl is not None else ilvl
        # 수준이 없으면 abstractNum의 lvl/pStyle이 가리키는 스타일(자기 스타일부터 체인 순)로 찾는다.
        for linked_id, _ in chain:
            if num_id is not None and ilvl is None:
                ilvl = self.numbering.level_for_style(num_id, linked_id)
        raw = None
        label = None
        if num_id is not None and num_id != "0":
            raw = NumberingRaw(num_id=num_id, ilvl=ilvl or 0)
            label = self.numbering.label(raw.num_id, raw.ilvl)
        name = chain[0][1].name if chain else style_id
        outline = (props is not None and props.find(f"{W}outlineLvl") is not None) or any(
            style.outline for _, style in chain
        )
        heading = outline or (name or "").lower().startswith(("heading", "제목"))
        return Hints(
            style_name=name,
            numbering_level=None if raw is None else raw.ilvl,
            list_text=label,
            is_heading=heading,
            numbering_raw=raw,
        )

    def record(self, out: Collected, block_id: str, paragraph_index: int | None) -> None:
        """문단 수집 결과의 그림·미처리 요소를 남긴다.

        Args:
            out: 문단 수집 상태.
            block_id: 텍스트 블록 ID(미처리 위치로 쓴다).
            paragraph_index: 본문 문단 순번(표 셀이면 None).
        """
        for kind, reason in out.unprocessed:
            self.unprocessed.append(Unprocessed(kind=kind, location=block_id, reason=reason))
        for _ in range(out.images):
            image_id = self.new_id()
            self.blocks.append(
                Block(
                    block_id=image_id,
                    kind="IMAGE",
                    text="",
                    position=Position(paragraph_index=paragraph_index),
                )
            )
            self.unprocessed.append(
                Unprocessed(kind="IMAGE", location=image_id, reason="그림 내용은 읽지 않음")
            )

    def paragraph(self, paragraph: ET.Element) -> None:
        self.paragraph_index += 1
        block_id = self.new_id()
        out = Collected()
        _collect(paragraph, out)
        text, deleted, flags, strikes, highlights = _assemble(out)
        self.strikes += strikes
        self.highlights += highlights
        # 빈 번호 문단도 Word에서는 번호를 한 칸 쓰므로 블록이 없어도 번호는 진행한다.
        hints = self.hints(paragraph)
        if text or deleted:
            self.blocks.append(
                Block(
                    block_id=block_id,
                    kind="PARAGRAPH",
                    text=text,
                    deleted_values=deleted,
                    flags=flags,
                    position=Position(paragraph_index=self.paragraph_index),
                    hints=hints,
                )
            )
        self.record(out, block_id, self.paragraph_index)

    def table(self, table: ET.Element) -> None:
        self.table_index += 1
        origins: dict[int, str] = {}  # 열별 세로 병합 시작 셀
        for row_index, row in enumerate(table.findall(f"{W}tr")):
            col = 0
            for cell in row.findall(f"{W}tc"):
                block_id = self.new_id()
                props = cell.find(f"{W}tcPr")
                span = int(_val(None if props is None else props.find(f"{W}gridSpan"), "1") or "1")
                merge = None if props is None else props.find(f"{W}vMerge")
                merged_with = None
                if merge is not None and _val(merge, "continue") == "continue":
                    merged_with = origins.get(col)
                else:
                    origins[col] = block_id
                out = Collected()
                for child in _cell_items(cell):
                    if child.tag == f"{W}p":
                        if out.pieces:
                            out.pieces.append(Piece(" ", None, False))
                        _collect(child, out)
                    elif child.tag == f"{W}tbl":
                        out.unprocessed.append(("OTHER", "중첩 표는 펼치지 않음"))
                text, deleted, flags, strikes, highlights = _assemble(out)
                self.strikes += strikes
                self.highlights += highlights
                self.blocks.append(
                    Block(
                        block_id=block_id,
                        kind="TABLE_CELL",
                        text=text,
                        deleted_values=deleted,
                        flags=flags,
                        position=Position(table_index=self.table_index, row=row_index, col=col),
                        cell_state=CellState(
                            col_span=span, merged_with=merged_with, is_empty=not text
                        ),
                    )
                )
                self.record(out, block_id, None)
                col += span

    def body(self, container: ET.Element) -> None:
        for child in container:
            if child.tag == f"{W}p":
                self.paragraph(child)
            elif child.tag == f"{W}tbl":
                self.table(child)
            elif child.tag in CONTAINERS:
                self.body(child)


def extract_docx(data: bytes) -> ExtractedDocument:
    """DOCX 바이트에서 본문 문단·표 셀·그림 블록을 뽑는다.

    Args:
        data: DOCX 파일 바이트.

    Returns:
        공통 표현 문서(edit_trace_support=True).

    Raises:
        ValueError: zip이 아니거나 word/document.xml이 없거나 부분이 상한·DTD 검사에 걸릴 때.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError("DOCX zip을 열 수 없습니다.") from error
    with archive:
        document = _read_part(archive, "word/document.xml")
        body = None if document is None else document.find(f"{W}body")
        if body is None:
            raise ValueError("word/document.xml 본문이 없습니다.")
        extractor = _Extractor(archive)
        extractor.body(body)
        for name in sorted(archive.namelist()):
            match = HEADER_PART.match(name)
            if match:
                kind = "HEADER" if match.group(1) == "header" else "FOOTER"
                extractor.unprocessed.append(
                    Unprocessed(kind=kind, location=name, reason="머리글·바닥글은 읽지 않음")
                )
    return build_document(
        "DOCX",
        extractor.blocks,
        extractor.unprocessed,
        tables=extractor.table_index + 1,
        strike_runs=extractor.strikes,
        highlight_runs=extractor.highlights,
    )
