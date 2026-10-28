"""추출 문서(ExtractedDocument)를 섹션 단위 검색 청크로 나눈다(KAN-99). 순수 함수이며 DB·모델 호출이 없다.

섹션: is_heading 블록 또는 numbering_level 0~2의 list_text 블록이 경계다(PART·2.5·C.). 깊이는
numbering_level, 없으면 style_name "Heading N"의 N-1, 둘 다 없으면 0이다. 같은 깊이 이상의 새 경계가
오면 경로 스택을 그 깊이까지 자르고 바꾼다. 경로 항목은 "번호 제목"이며 80자를 넘으면 자른다. 경계 블록의
문장도 그 섹션 첫 청크 본문에 넣는다.

본문 청크(TEXT): 같은 섹션 문단을 순서대로 예산(기본 1,200자)까지 묶고, 넘치면 문단 경계에서 나눈다.
문단 하나가 예산을 넘으면 자르지 않고 단독 청크로 둔다. overlap은 없고 section_path가 연결 문맥이다.

표 청크(TABLE): 표 하나를 행마다 "열 머리 (단위): 셀"을 " | "로 이은 줄로 쓰고 표제를 첫 줄에 둔다. 머리 행은
열 머리로만 쓰고 줄로 내지 않는다(열 머리가 하나도 없는 표는 첫 행도 데이터로 쓴다). 예산을 넘으면 행
경계에서 나누고 표제를 반복한다. 빈 셀·세로 병합 이어짐 셀은 줄에서 뺀다.

각주는 청크 끝에 "[각주 ref_id] 본문"으로 덧붙이고 char_count에 넣는다. 삭제 값은 text에 넣지 않고
has_deleted_values로만 표시한다. 그림·현행 텍스트가 빈 블록·세로 병합 이어짐 셀은 skipped_block_ids에 둔다.
cross_references·hints 원문은 청크에 넣지 않는다.

재색인: 같은 입력과 INDEX_VERSION이면 chunk_id·text·순서가 같다. chunk_id는 문서 sha256·INDEX_VERSION·
ordinal로 만들므로 규칙을 바꿔 INDEX_VERSION을 올리면 모든 청크를 새로 만든다(이전 판본 청크와 섞지 않는다).

범위 밖: 임베딩 차원·FTS 색인·search 스키마·DB 저장은 소유 서비스·마이그레이션 결정 후다.
권한·제품·판본 메타데이터는 null로 두고 호출자가 채운다.
"""

import hashlib
import re
from dataclasses import dataclass, field

from backend.engine.documents.model import Block, ExtractedDocument
from backend.engine.search.model import Chunk, ChunkingResult, ChunkKind

INDEX_VERSION = "chunking-v1"
BUDGET = 1200
PATH_LIMIT = 80
HEADING_STYLE = re.compile(r"(?i)heading\s*(\d)")


@dataclass
class Unit:
    """나눌 수 없는 본문 한 줄(문단 하나 또는 표 행 하나)과 그 블록·각주."""

    body: str
    blocks: list[Block]
    notes: list[str] = field(default_factory=list)


def _boundary_depth(block: Block) -> int | None:
    """섹션 경계 블록이면 깊이를 돌려준다.

    Args:
        block: 본문 문단 블록.

    Returns:
        int | None: 경계면 깊이(0부터), 아니면 None.
    """
    hints = block.hints
    if block.kind != "PARAGRAPH" or hints is None:
        return None
    level = hints.numbering_level
    if not hints.is_heading and not (hints.list_text and level is not None and 0 <= level <= 2):
        return None
    if level is not None:
        return level
    match = HEADING_STYLE.search(hints.style_name or "")
    return max(int(match.group(1)) - 1, 0) if match else 0


def _label(block: Block) -> str:
    """경로 항목 "번호 제목"을 만든다.

    Args:
        block: 경계 블록.

    Returns:
        str: text가 이미 번호로 시작하면 text만. 80자를 넘으면 잘라 "…"를 붙인다.
    """
    number = (block.hints.list_text or "").strip() if block.hints else ""
    label = block.text if not number or block.text.startswith(number) else f"{number} {block.text}"
    return label if len(label) <= PATH_LIMIT else label[:PATH_LIMIT] + "…"


def _notes(block: Block) -> list[str]:
    """블록 각주를 덧붙일 줄로 바꾼다.

    Args:
        block: 블록.

    Returns:
        list[str]: "[각주 ref_id] 본문" 줄. 본문 현행 텍스트만 쓴다.
    """
    return [f"[각주 {note.ref_id}] {note.text}" for note in block.footnotes if note.text]


def _text(prefix: list[str], units: list[Unit]) -> str:
    """청크 text를 조립한다.

    Args:
        prefix: 앞에 둘 줄(표제).
        units: 본문 줄.

    Returns:
        str: 줄바꿈으로 이은 머리·본문·각주.
    """
    lines = [*prefix, *(unit.body for unit in units), *(n for unit in units for n in unit.notes)]
    return "\n".join(lines)


def _pack(units: list[Unit], prefix: list[str], budget: int) -> list[list[Unit]]:
    """줄을 순서대로 예산까지 묶는다. 줄 하나가 예산을 넘으면 단독 묶음이다.

    Args:
        units: 본문 줄.
        prefix: 묶음마다 반복할 머리 줄.
        budget: 글자 예산.

    Returns:
        list[list[Unit]]: 묶음 목록. 줄은 자르지 않는다.
    """
    groups: list[list[Unit]] = []
    current: list[Unit] = []
    for unit in units:
        if current and len(_text(prefix, [*current, unit])) > budget:
            groups.append(current)
            current = []
        current.append(unit)
    if current:
        groups.append(current)
    return groups


def _table_units(cells: list[Block], skipped: list[str]) -> list[Unit]:
    """표 셀을 행 줄로 바꾼다.

    Args:
        cells: 같은 표의 셀 블록(문서 순서).
        skipped: 빈 셀·세로 병합 이어짐 셀 ID를 더할 목록.

    Returns:
        list[Unit]: 행 순서의 줄. 머리 행 셀은 첫 줄의 블록으로 함께 둔다(열 머리로 쓰였으므로).
    """
    has_headers = any(cell.column_header for cell in cells)
    rows: dict[int, list[Block]] = {}
    header_cells: list[Block] = []
    for cell in cells:
        if not cell.text or (cell.cell_state and cell.cell_state.merged_with):
            skipped.append(cell.block_id)
            continue
        row = cell.position.row or 0
        if row == 0 and has_headers:
            header_cells.append(cell)
            continue
        rows.setdefault(row, []).append(cell)
    units = []
    for row in sorted(rows):
        parts = []
        for cell in sorted(rows[row], key=lambda c: c.position.col or 0):
            head = cell.column_header
            if head and cell.column_unit:
                head = f"{head} ({cell.column_unit})"
            parts.append(f"{head}: {cell.text}" if head else cell.text)
        row_cells = rows[row]
        units.append(
            Unit(" | ".join(parts), row_cells, [n for cell in row_cells for n in _notes(cell)])
        )
    if units:
        units[0].blocks[:0] = header_cells
    else:
        # 데이터 행이 없으면 머리 행도 청크에 들어가지 않는다.
        skipped.extend(cell.block_id for cell in header_cells)
    return units


def chunk_document(document: ExtractedDocument, *, budget: int = BUDGET) -> ChunkingResult:
    """추출 문서를 섹션 경로가 붙은 청크로 나눈다.

    Args:
        document: KAN-96·98 추출 결과.
        budget: 청크 글자 예산(각주·표제 포함). 줄 하나가 넘으면 그 줄만 단독 청크다.

    Returns:
        ChunkingResult: 문서 순서의 청크, 청크에 넣지 않은 블록 ID, 색인 판본.
    """
    chunks: list[Chunk] = []
    skipped: list[str] = []
    path: list[tuple[int, str]] = []
    section: list[Unit] = []

    def emit(units: list[Unit], kind: ChunkKind, title: str | None) -> None:
        """묶음 하나를 청크로 만든다.

        Args:
            units: 청크에 넣을 줄.
            kind: TEXT 또는 TABLE.
            title: 표제(표 청크만).

        Returns:
            None: chunks에 더한다.
        """
        blocks = [block for unit in units for block in unit.blocks]
        text = _text([title] if title else [], units)
        pages = [block.position.page for block in blocks if block.position.page is not None]
        ordinal = len(chunks)
        chunk_key = f"{document.sha256}:{INDEX_VERSION}:{ordinal}"
        chunks.append(
            Chunk(
                chunk_id=hashlib.sha256(chunk_key.encode()).hexdigest()[:32],
                document_sha256=document.sha256,
                ordinal=ordinal,
                index_version=INDEX_VERSION,
                section_path=[label for _, label in path],
                block_ids=[block.block_id for block in blocks],
                source_position=blocks[0].position,
                text=text,
                char_count=len(text),
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                kind=kind,
                table_title=title,
                has_deleted_values=any(
                    block.deleted_values or any(note.deleted_values for note in block.footnotes)
                    for block in blocks
                ),
                flags=sorted({flag for block in blocks for flag in block.flags}),
            )
        )

    def flush() -> None:
        """모은 섹션 문단을 예산대로 청크로 낸다.

        Returns:
            None: 섹션 버퍼를 비운다.
        """
        for group in _pack(section, [], budget):
            emit(group, "TEXT", None)
        section.clear()

    blocks = document.blocks
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if block.kind == "TABLE_CELL":
            table = block.position.table_index
            cells = []
            while (
                index < len(blocks)
                and blocks[index].kind == "TABLE_CELL"
                and blocks[index].position.table_index == table
            ):
                cells.append(blocks[index])
                index += 1
            flush()
            title = next((cell.table_title for cell in cells if cell.table_title), None)
            prefix = [title] if title else []
            for group in _pack(_table_units(cells, skipped), prefix, budget):
                emit(group, "TABLE", title)
            continue
        index += 1
        if block.kind == "IMAGE" or not block.text:
            skipped.append(block.block_id)
            continue
        depth = _boundary_depth(block)
        if depth is not None:
            flush()
            while path and path[-1][0] >= depth:
                path.pop()
            path.append((depth, _label(block)))
        section.append(Unit(block.text, [block], _notes(block)))
    flush()
    return ChunkingResult(chunks=chunks, skipped_block_ids=skipped, index_version=INDEX_VERSION)
