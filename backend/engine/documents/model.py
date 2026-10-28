"""DOCX·PDF 추출 결과의 공통 표현. A04(KAN-185)가 조항 트리 복원과 요구 추출의 입력으로 쓴다."""

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SourceKind = Literal["DOCX", "PDF"]
BlockKind = Literal["PARAGRAPH", "TABLE_CELL", "IMAGE"]
Flag = Literal["HIGHLIGHT", "TEMPLATE_PLACEHOLDER", "LINE_BREAK_REJOINED"]
ReferenceKind = Literal["CLAUSE", "DRAWING", "DOCUMENT", "OTHER"]
UnprocessedKind = Literal["HEADER", "FOOTER", "FOOTNOTE", "TEXTBOX", "EQUATION", "IMAGE", "OTHER"]
PLACEHOLDER = re.compile(r"<[^<>]{1,60}>")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIMES: dict[str, str] = {"DOCX": DOCX_MIME, "PDF": "application/pdf"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DeletedValue(Strict):
    """지워진 값. position은 현행 text에서 이 값이 있던 자리(삽입 위치)다."""

    text: str
    position: int = Field(ge=0)
    origin: Literal["STRIKE", "TRACKED_DELETE"]


class Position(Strict):
    """블록 위치. page는 PDF에서만 채우고 DOCX는 null이다."""

    paragraph_index: int | None = None
    table_index: int | None = None
    row: int | None = None
    col: int | None = None
    page: int | None = None


class NumberingRaw(Strict):
    num_id: str
    ilvl: int


class Hints(Strict):
    """조항 트리 복원 단서. list_text는 계산한 자동 번호이며 계산하지 못하면 null이다."""

    style_name: str | None
    numbering_level: int | None
    list_text: str | None
    is_heading: bool
    numbering_raw: NumberingRaw | None


class CellState(Strict):
    """col_span은 가로 병합 칸 수, merged_with는 세로 병합 이어짐 셀의 시작 셀 block_id다."""

    col_span: int = Field(ge=1)
    merged_with: str | None
    is_empty: bool


class Footnote(Strict):
    """본문 참조 위치에 붙인 각주·미주 본문. 각주 안 취소선도 본문과 같은 규칙으로 나눈다."""

    ref_id: str
    note_kind: Literal["FOOTNOTE", "ENDNOTE"]
    text: str
    deleted_values: list[DeletedValue] = []


class CrossReference(Strict):
    """다른 절·도면·문서 참조. CLAUSE만 같은 문서 안에서 해결을 시도한다."""

    text: str
    target_kind: ReferenceKind
    target_hint: str
    resolved: bool
    target_block_id: str | None


class Block(Strict):
    block_id: str
    kind: BlockKind
    text: str = Field(description="현행 텍스트만. 지워진 값은 deleted_values에 따로 둔다.")
    deleted_values: list[DeletedValue] = []
    flags: list[Flag] = []
    position: Position
    hints: Hints | None = None
    cell_state: CellState | None = None
    footnotes: list[Footnote] = []
    cross_references: list[CrossReference] = []
    table_title: str | None = Field(default=None, description="표 셀만. 표 캡션 또는 앞 문단 표제.")
    column_header: str | None = Field(
        default=None, description="데이터 셀만. 단위를 뺀 머리 행 텍스트."
    )
    column_unit: str | None = Field(default=None, description="데이터 셀만. 머리 행 끝 괄호 단위.")


class Unprocessed(Strict):
    kind: UnprocessedKind
    location: str
    reason: str


class Summary(Strict):
    blocks: int
    tables: int
    images: int
    strike_runs: int
    highlight_runs: int
    placeholders: int
    unprocessed: int


class ExtractedDocument(Strict):
    """명세 §4.2 업로드 응답과 같은 이름(sha256·size·mime). 판본 번호는 DB(document_versions) 몫이다."""

    source_kind: SourceKind
    sha256: str
    size: int
    mime: str = Field(description="매직바이트로 판별한 표준 MIME. 호출자가 선언한 값이 아니다.")
    source_name: str | None = None
    edit_trace_support: bool
    blocks: list[Block]
    unprocessed: list[Unprocessed]
    summary: Summary


def build_document(
    source_kind: SourceKind,
    data: bytes,
    blocks: list[Block],
    unprocessed: list[Unprocessed],
    tables: int,
    strike_runs: int = 0,
    highlight_runs: int = 0,
) -> ExtractedDocument:
    """블록과 미처리 목록으로 요약을 계산해 문서를 만든다.

    Args:
        source_kind: 원본 형식.
        data: 원본 바이트(식별 해시·크기 계산용).
        blocks: 문서 순서의 블록.
        unprocessed: 읽지 않은 요소.
        tables: 표 수.
        strike_runs: 취소선 런 수(DOCX만).
        highlight_runs: 형광펜 런 수(DOCX만).

    Returns:
        요약을 채운 추출 문서. DOCX만 편집 흔적을 지원한다.
    """
    return ExtractedDocument(
        source_kind=source_kind,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        mime=MIMES[source_kind],
        edit_trace_support=source_kind == "DOCX",
        blocks=blocks,
        unprocessed=unprocessed,
        summary=Summary(
            blocks=len(blocks),
            tables=tables,
            images=sum(block.kind == "IMAGE" for block in blocks),
            strike_runs=strike_runs,
            highlight_runs=highlight_runs,
            placeholders=sum(len(PLACEHOLDER.findall(block.text)) for block in blocks),
            unprocessed=len(unprocessed),
        ),
    )
