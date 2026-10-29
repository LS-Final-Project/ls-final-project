"""Markdown 추출(KAN-99 3차). 제공 문서 묶음(제품·FAQ·정책 .md)을 DOCX·PDF와 같은 공통 표현으로 바꾼다.

규칙:
- UTF-8(BOM 허용) 텍스트만 받는다. NUL 바이트·디코드 실패·DOCTYPE은 거부하고, 크기 상한은 PDF·DOCX와 같은
  25 MiB다.
- '#'~'######' 줄은 PARAGRAPH + hints.is_heading=True·numbering_level=레벨-1이다. chunking의 섹션 경계로 쓰인다.
- 빈 줄로 나뉜 단락은 PARAGRAPH 하나다(줄은 줄바꿈으로 잇는다).
- '|'로 시작하는 연속 줄은 표다. 둘째 줄이 구분선(|---|)이면 첫 행이 머리 행이고, 데이터 셀에 column_header·
  column_unit(괄호 단위)을 붙인다. table_title은 표 바로 앞 표제·단락이다.
- ``` 코드 블록은 안쪽 줄을 그대로 PARAGRAPH 하나로 둔다(취소선·링크를 해석하지 않는다).
- ~~x~~는 deleted_values(origin STRIKE)로 나누고 text에서 뺀다. 링크·이미지는 보이는 글자만 남긴다.
"""

import re

from backend.engine.documents.docx import _split_unit
from backend.engine.documents.model import (
    Block,
    CellState,
    DeletedValue,
    ExtractedDocument,
    Hints,
    Position,
    build_document,
)
from backend.engine.documents.pdf import MAX_BYTES

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
FENCE = re.compile(r"^\s*```")
STRIKE = re.compile(r"~~(.+?)~~")
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def _inline(text: str) -> tuple[str, list[DeletedValue]]:
    """링크를 글자로 바꾸고 취소선을 지운 값으로 나눈다.

    Args:
        text: 한 블록의 원문.

    Returns:
        tuple: (현행 텍스트, 지운 값 목록). 지운 값의 position은 현행 텍스트에서 그 값이 있던 자리다.
    """
    text = LINK.sub(lambda match: match.group(1), text)
    deleted: list[DeletedValue] = []
    parts: list[str] = []
    cursor = 0
    for match in STRIKE.finditer(text):
        parts.append(text[cursor : match.start()])
        deleted.append(
            DeletedValue(text=match.group(1), position=len("".join(parts)), origin="STRIKE")
        )
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts).strip(), deleted


def _cells(line: str) -> list[str]:
    """표 한 줄을 셀 문자열로 나눈다.

    Args:
        line: '|'로 시작하는 줄.

    Returns:
        list[str]: 앞뒤 공백을 뗀 셀. 양 끝 '|'는 빼고, '\\|'는 셀 안 글자로 둔다.
    """
    body = line.strip().replace("\\|", "\x00")
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") else body
    return [cell.replace("\x00", "|").strip() for cell in body.split("|")]


def extract_markdown(data: bytes, *, source_name: str | None = None) -> ExtractedDocument:
    """Markdown 바이트를 공통 표현 문서로 바꾼다.

    Args:
        data: .md 파일 바이트.
        source_name: 원래 파일 이름(선택).

    Returns:
        ExtractedDocument: source_kind=MARKDOWN 문서. 표제·단락·코드 블록은 PARAGRAPH, 표는 TABLE_CELL이다.

    Raises:
        ValueError: 크기 상한 초과, UTF-8이 아님, NUL 바이트, DOCTYPE 포함.
    """
    if len(data) > MAX_BYTES:
        raise ValueError("문서가 크기 상한(25 MiB)을 넘습니다.")
    if b"\x00" in data:
        raise ValueError("Markdown에 NUL 바이트가 있습니다.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Markdown은 UTF-8이어야 합니다.") from None
    if "<!doctype" in text.casefold():
        raise ValueError("Markdown에 DOCTYPE이 있습니다.")
    lines = text.splitlines()
    blocks: list[Block] = []
    paragraph_index = table_index = strike_runs = 0
    last_text: str | None = None

    def add_paragraph(raw: str, hints: Hints | None = None, *, verbatim: bool = False) -> None:
        """문단 블록 하나를 더한다.

        Args:
            raw: 원문.
            hints: 표제 단서.
            verbatim: True면 취소선·링크를 해석하지 않는다(코드 블록).

        Returns:
            None: blocks에 더한다.
        """
        nonlocal paragraph_index, strike_runs, last_text
        current, deleted = (raw, []) if verbatim else _inline(raw)
        if not current and not deleted:
            return
        strike_runs += len(deleted)
        blocks.append(
            Block(
                block_id=f"p{paragraph_index}",
                kind="PARAGRAPH",
                text=current,
                deleted_values=deleted,
                position=Position(paragraph_index=paragraph_index),
                hints=hints,
            )
        )
        paragraph_index += 1
        last_text = current or last_text

    def add_table(rows: list[str]) -> None:
        """연속한 표 줄을 셀 블록으로 더한다.

        Args:
            rows: '|'로 시작하는 줄들.

        Returns:
            None: blocks에 더한다.
        """
        nonlocal table_index, strike_runs
        has_header = len(rows) > 1 and SEPARATOR.match(rows[1]) is not None
        data_rows = [rows[0], *rows[2:]] if has_header else rows
        headers = [_inline(cell)[0] for cell in _cells(rows[0])] if has_header else []
        for row_number, line in enumerate(data_rows):
            for col, raw in enumerate(_cells(line)):
                current, deleted = _inline(raw)
                strike_runs += len(deleted)
                header = (
                    headers[col] if has_header and row_number > 0 and col < len(headers) else None
                )
                column_header, column_unit = _split_unit(header) if header else (None, None)
                blocks.append(
                    Block(
                        block_id=f"t{table_index}r{row_number}c{col}",
                        kind="TABLE_CELL",
                        text=current,
                        deleted_values=deleted,
                        position=Position(table_index=table_index, row=row_number, col=col),
                        cell_state=CellState(col_span=1, merged_with=None, is_empty=not current),
                        table_title=last_text,
                        column_header=column_header,
                        column_unit=column_unit,
                    )
                )
        table_index += 1

    index = 0
    paragraph: list[str] = []

    def flush() -> None:
        """모은 단락 줄을 문단 하나로 낸다.

        Returns:
            None: 단락 버퍼를 비운다.
        """
        if paragraph:
            add_paragraph("\n".join(paragraph))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        if FENCE.match(line):
            flush()
            index += 1
            code: list[str] = []
            while index < len(lines) and not FENCE.match(lines[index]):
                code.append(lines[index])
                index += 1
            index += 1  # 닫는 펜스(없으면 문서 끝까지가 코드다).
            add_paragraph("\n".join(code), verbatim=True)
            continue
        heading = HEADING.match(line)
        if heading:
            flush()
            add_paragraph(
                heading.group(2),
                Hints(
                    style_name=None,
                    numbering_level=len(heading.group(1)) - 1,
                    list_text=None,
                    is_heading=True,
                    numbering_raw=None,
                ),
            )
            index += 1
            continue
        if line.lstrip().startswith("|"):
            flush()
            rows = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(lines[index])
                index += 1
            add_table(rows)
            continue
        if not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
        index += 1
    flush()
    document = build_document(
        "MARKDOWN", data, blocks, [], tables=table_index, strike_runs=strike_runs
    )
    return document.model_copy(update={"source_name": source_name})
