"""추출 문서(ExtractedDocument)를 섹션 단위 검색 청크로 나눈다(KAN-99). 순수 함수이며 DB·모델 호출이 없다.

섹션: is_heading 블록 또는 numbering_level 0~2의 list_text 블록이 경계다(PART·2.5·C.). 깊이는
numbering_level, 없으면 style_name "Heading N"의 N-1, 둘 다 없으면 0이다. 같은 깊이 이상의 새 경계가
오면 경로 스택을 그 깊이까지 자르고 바꾼다. 경로 항목은 "번호 제목"이며 80자를 넘으면 자른다. 경계 블록의
문장도 그 섹션 첫 청크 본문에 넣는다.

v2(KAN-99·KAN-184): 제목·번호 힌트가 없는 문단(PDF에서 바꾼 DOCX 등)은 줄머리 조항 번호로 경계를 찾는다.
제목꼴(80자 이하, 끝 문장부호 없음)인 줄만 경계로 본다.
번호 패턴은 조항 트리(backend/engine/rfp/clauses.py)의 정규식을 그대로 쓰고 깊이도 같다: SECTION 머리 0,
PART 1, 조(2.5) 2. A.·1.·a.처럼 더 깊은 번호는 경계로 삼지 않고 본문 줄로 둔다(14번 DOCX에서 A.까지 경계로
삼으면 청크 140개 중 100자 미만 31개, 조까지면 82개 중 3개).

제목만 든 짧은 TEXT 청크(40자 이하, 끝 문장부호 없음)는 따로 두지 않고 다음 청크 앞에 합친다. 섹션 머리가
아닌 짧은 줄은 다음 청크가 같은 섹션일 때만 합친다(앞 섹션 끝 줄이 다음 섹션 경로로 색인되지 않게). 번호 칸으로
목록을 흉내 낸 레이아웃 표(모든 행 첫 칸이 번호뿐)는 행을 문장 줄로 바꿔 다음 TEXT 청크 앞에 합친다. 합칠
TEXT 청크가 없으면(다음이 표이거나 문서 끝) 그대로 둔다. 합친 청크는 예산을 조금 넘을 수 있다.

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
from backend.engine.rfp.clauses import ARTICLE, LABELS, PART, SECTION_HEAD, SENTENCE_END
from backend.engine.search.model import Chunk, ChunkingResult, ChunkKind, ChunkMetadata

INDEX_VERSION = "chunking-v3"
BUDGET = 1200
PATH_LIMIT = 80
HEADING_STYLE = re.compile(r"(?i)heading\s*(\d)")
YAML_KEY = re.compile(r"^[A-Za-z_][\w-]*:")
# 이 글자 수 이하이고 끝 문장부호가 없는 TEXT 청크는 제목만 든 것으로 보고 다음 청크에 합친다.
TITLE_ONLY_MAX = 40
# 줄머리 번호로 경계를 삼는 가장 깊은 깊이(clauses.py 깊이: SECTION 0·PART 1·조 2·A. 3). 조까지만 쓴다.
CLAUSE_BOUNDARY_MAX = 2


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
    if block.kind != "PARAGRAPH":
        return None
    level = hints.numbering_level if hints else None
    if hints and (hints.is_heading or hints.list_text):
        if not hints.is_heading and not (level is not None and 0 <= level <= 2):
            return None
        if level is not None:
            return level
        match = HEADING_STYLE.search(hints.style_name or "")
        return max(int(match.group(1)) - 1, 0) if match else 0
    if level is not None:
        return None
    return _clause_depth(block.text)


def _clause_depth(text: str) -> int | None:
    """제목·번호 힌트가 없는 문단의 줄머리 조항 번호 깊이(clauses.py와 같은 패턴·깊이).

    Args:
        text: 문단 현행 텍스트.

    Returns:
        int | None: SECTION 머리 0, PART 1, 조(2.5) 2. CLAUSE_BOUNDARY_MAX보다 깊은 번호나 번호가 없으면 None.
            제목꼴이 아닌 줄(80자 초과이거나 끝 문장부호가 있음)은 번호로 시작해도 None이다. 문서 구조 맥락 없이
            보므로 "2.5 mm 이상의 전선은 …"처럼 소수로 시작하는 본문 문장을 경계로 잡지 않게 한다.
    """
    line = text.strip()
    if len(line) > PATH_LIMIT or line.endswith(SENTENCE_END):
        return None
    if SECTION_HEAD.match(line):
        return 0
    if PART.match(line):
        return 1
    if ARTICLE.match(line):
        return 2
    for depth, pattern in LABELS:
        if pattern.match(line):
            return depth if depth <= CLAUSE_BOUNDARY_MAX else None
    return None


def _title_only(text: str) -> bool:
    """제목만 든 짧은 TEXT인지.

    Args:
        text: 청크 text.

    Returns:
        bool: 40자 이하이고 끝이 문장부호가 아니면 True.
    """
    body = text.strip()
    return 0 < len(body) <= TITLE_ONLY_MAX and not body.endswith(SENTENCE_END)


def _label_only(text: str) -> bool:
    """칸 글자가 번호 하나뿐인지(예: "1.", "a.", "J.").

    Args:
        text: 표 칸 현행 텍스트.

    Returns:
        bool: clauses.py 번호 패턴과 글자 전체가 맞으면 True.
    """
    cell = text.strip()
    return any((m := pattern.match(cell)) and m.group(0).strip() == cell for _, pattern in LABELS)


def _layout_units(cells: list[Block], skipped: list[str]) -> list[Unit] | None:
    """모든 행 첫 칸이 번호뿐인 레이아웃 표면 행을 "번호 내용" 문장 줄로 바꾼다.

    Args:
        cells: 같은 표의 셀 블록.
        skipped: 빈 셀·세로 병합 이어짐 셀 ID를 더할 목록.

    Returns:
        list[Unit] | None: 레이아웃 표면 행 줄, 아니면 None(skipped는 건드리지 않는다).
    """
    rows: dict[int, list[Block]] = {}
    empty: list[str] = []
    for cell in cells:
        if not cell.text or (cell.cell_state and cell.cell_state.merged_with):
            empty.append(cell.block_id)
            continue
        rows.setdefault(cell.position.row or 0, []).append(cell)
    ordered = [sorted(rows[r], key=lambda c: c.position.col or 0) for r in sorted(rows)]
    if not ordered or not all(len(row) >= 2 and _label_only(row[0].text) for row in ordered):
        return None
    skipped.extend(empty)
    return [
        Unit(" ".join(cell.text for cell in row), row, [n for c in row for n in _notes(c)])
        for row in ordered
    ]


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


def _preamble(document: ExtractedDocument) -> list[str]:
    """Markdown 머리말 블록 ID를 찾는다. 머리말은 본문이 아니라 색인에 넣지 않는다.

    Args:
        document: 추출 문서.

    Returns:
        list[str]: 맨 앞 YAML 머리말과 바로 뒤 인용 문단의 블록 ID. 닫는 줄이 없으면
            머리말이 아니라 빈 목록이다. DOCX·PDF도 빈 목록이다.
    """
    blocks = document.blocks
    if document.source_kind != "MARKDOWN" or not blocks:
        return []
    if blocks[0].text.splitlines()[:1] != ["---"]:
        return []
    for index, block in enumerate(blocks):
        lines = block.text.splitlines()[0 if index else 1 :]
        fields = lines[: lines.index("---")] if "---" in lines else lines
        # 가로줄로 시작하는 본문을 머리말로 삼키지 않게 값 줄만 지나간다.
        if not all(YAML_KEY.match(line) or line.startswith((" ", "- ")) for line in fields):
            return []
        if "---" in lines:
            break
    else:
        return []
    end = index + 1
    if end < len(blocks) and blocks[end].text.startswith(">"):
        end += 1
    return [block.block_id for block in blocks[:end]]


def search_context(chunk: Chunk, metadata: ChunkMetadata) -> str:
    """검색 색인·임베딩에만 쓰는 맥락 머리를 만든다. 스니펫과 모델 입력에는 넣지 않는다.

    Args:
        chunk: 청크.
        metadata: 문서 메타데이터.

    Returns:
        str: 문서 제목·제품·문서 종류·계열·절 경로를 공백으로 이은 값(중복 제외).
    """
    parts = (
        metadata.title,
        metadata.product_code,
        metadata.document_kind,
        metadata.product_family,
        *chunk.section_path,
    )
    return " ".join(dict.fromkeys(part for part in parts if part))


def is_document_listing(chunk: Chunk, document_ids: frozenset[str]) -> bool:
    """제목을 뺀 줄이 모두 문서 식별자를 든 목록 청크인지 본다. 이런 청크는 근거가 못 된다.

    Args:
        chunk: 청크.
        document_ids: 문서 묶음의 문서 식별자.

    Returns:
        bool: 줄이 둘 이상이고 전부 문서 식별자를 담으면 True.
    """
    lines = [
        line.strip()
        for line in chunk.text.splitlines()
        if line.strip() and line.strip() not in chunk.section_path
    ]
    return len(lines) >= 2 and all(
        any(document_id in line for document_id in document_ids) for line in lines
    )


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
    # 섹션 경계 블록 ID. 제목만 청크가 섹션 머리인지 볼 때 쓴다.
    heads: set[str] = set()
    # 다음 청크 앞에 합칠 묶음: (줄, 레이아웃 표인지, 표제, 그때의 섹션 경로). 제목만 TEXT는 다음 청크(종류
    # 무관)에, 레이아웃 표는 다음 TEXT 청크에만 합친다. 섹션 머리가 아닌 묶음은 같은 섹션 안에서만 합친다.
    pending: list[tuple[list[Unit], bool, str | None, list[str]]] = []

    def emit(units: list[Unit], kind: ChunkKind, title: str | None, layout: bool = False) -> None:
        """묶음 하나를 청크로 내거나, 제목만 TEXT·레이아웃 표면 다음 청크에 합치려고 미룬다.

        Args:
            units: 청크에 넣을 줄.
            kind: TEXT 또는 TABLE.
            title: 표제(표 청크만).
            layout: 번호 칸 레이아웃 표의 행 줄인지.

        Returns:
            None: chunks에 더하거나 pending에 둔다.
        """
        current = [label for _, label in path]
        headings = all(block.block_id in heads for unit in units for block in unit.blocks)
        if layout or (kind == "TEXT" and (headings or _title_only(_text([], units)))):
            pending.append((units, layout, title, current))
            return
        lead: list[Unit] = []
        for held, is_layout, held_title, held_path in pending:
            head = not is_layout and held[0].blocks[0].block_id in heads
            if held_path != current and not head:
                # 앞 섹션 끝의 짧은 줄·레이아웃 표는 다른 섹션 청크에 붙이지 않고 제 경로로 그대로 낸다.
                write([*lead, *held], "TABLE" if is_layout else "TEXT", held_title, None, held_path)
                lead = []
            elif is_layout and kind == "TABLE":
                # 레이아웃 표는 표 청크에 합치지 않는다. 앞서 모은 제목과 함께 그대로 낸다.
                write([*lead, *held], "TABLE", held_title, None, held_path)
                lead = []
            else:
                lead += [Unit(held_title, []), *held] if is_layout and held_title else held
        pending.clear()
        write(
            units if kind == "TABLE" else [*lead, *units],
            kind,
            title,
            lead if kind == "TABLE" else [],
        )

    def release() -> None:
        """문서 끝에서 합칠 곳이 없는 묶음을 그대로 낸다.

        Returns:
            None: 남은 pending을 원래 종류로 낸다.
        """
        for held, is_layout, held_title, held_path in pending:
            write(held, "TABLE" if is_layout else "TEXT", held_title, None, held_path)
        pending.clear()

    def write(
        units: list[Unit],
        kind: ChunkKind,
        title: str | None,
        lead: list[Unit] | None = None,
        section_path: list[str] | None = None,
    ) -> None:
        """묶음 하나를 청크로 만든다.

        Args:
            units: 청크에 넣을 줄.
            kind: TEXT 또는 TABLE.
            title: 표제(표 청크만).
            lead: 표 청크 표제 앞에 둘 합친 제목 줄.
            section_path: 미뤄 둔 묶음을 낼 때의 원래 섹션 경로. 없으면 지금 경로다.

        Returns:
            None: chunks에 더한다.
        """
        head = lead or []
        blocks = [block for unit in [*head, *units] for block in unit.blocks]
        # 표 앞 캡션 문단이 표제와 같으면 블록만 넣고 글자는 되풀이하지 않는다.
        shown = [unit for unit in head if unit.body != title]
        text = "\n".join(
            filter(
                None, [_text([], shown) if shown else "", _text([title] if title else [], units)]
            )
        )
        pages = [block.position.page for block in blocks if block.position.page is not None]
        ordinal = len(chunks)
        chunk_key = f"{document.sha256}:{INDEX_VERSION}:{ordinal}"
        chunks.append(
            Chunk(
                chunk_id=hashlib.sha256(chunk_key.encode()).hexdigest()[:32],
                document_sha256=document.sha256,
                ordinal=ordinal,
                index_version=INDEX_VERSION,
                section_path=section_path
                if section_path is not None
                else [label for _, label in path],
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

    preamble = _preamble(document)
    skipped.extend(preamble)
    blocks = [block for block in document.blocks if block.block_id not in preamble]
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
            layout = _layout_units(cells, skipped)
            if layout is not None:
                emit(layout, "TABLE", title, layout=True)
                continue
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
            heads.add(block.block_id)
            flush()
            while path and path[-1][0] >= depth:
                path.pop()
            path.append((depth, _label(block)))
        section.append(Unit(block.text, [block], _notes(block)))
    flush()
    release()
    return ChunkingResult(chunks=chunks, skipped_block_ids=skipped, index_version=INDEX_VERSION)
