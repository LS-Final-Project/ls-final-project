"""추출 문서(ExtractedDocument)에서 조항 트리를 복원한다(KAN-185, 기획서 5.2). DB·모델 호출이 없다.

제공 RFP는 제목 서식·자동 번호 없이 번호를 본문 글자로 적는다(14번은 모든 문단이 Normal). 그래서 문단 앞
번호 패턴으로 깊이를 정한다.

| 깊이 | 패턴 | 조항 번호 예 |
| --- | --- | --- |
| 1 | `PART 2 - PRODUCTS` | `PART 2` |
| 2 | `2.5 EPOXY …` | `2.5` |
| 3 | `C. …` | `2.5.C` |
| 4 | `1. …` | `2.5.C.1` |
| 5 | `a. …` | `2.5.C.1.a` |
| 6 | `1)`·`(1)` | `2.5.C.1.a.1` |

- 번호 없는 문단은 바로 앞 조항에 붙인다. 앞 글이 문장 끝(`.`·`:`·`;`)이 아니거나 이번 문단이 소문자로
  시작하면 줄이 중간에서 끊긴 것으로 보고 공백으로 잇는다(`LINE_BREAK_REJOINED`). 아니면 줄을 바꿔 붙인다.
- 표: 한 행의 첫 글자 있는 칸이 번호만이면 그 행을 번호 조항으로 읽는다(14번 2.5 D·G·I…가 이런 배치다).
  그 밖의 표 행은 바로 앞 조항의 `table_rows`에 넣는다.
- 반복 문구: 숫자를 지운 모양이 3번 이상 나오는 번호 없는 짧은 문단(페이지 머리말·바닥글이 본문에 들어온
  것), `END OF SECTION …`은 조항에 넣지 않고 `boilerplate_block_ids`에 둔다. 첫 PART 앞 문단은 머리말이다.
- 표 칸 하나가 `2.4 A.`처럼 절·항목 번호만이면 그 절과 항목을 연다. 같은 행 뒤가 본문이면 그 본문이
  항목 본문이고, 뒤가 번호 칸(`1.`·`ABB.`)이면 그 칸들은 앞 조항 목록의 이어짐이라 앞 조항에 두고, 절은 번호가
  `1`·`a`로 다시 시작하는 곳에서 연다(`DEFERRED_ARTICLE`).
- 한 문단에 항목 두 개가 붙어 있으면(PDF 변환, 14번 2.4 C.3 "… open. b. Expulsion …") 문장 끝 바로 뒤에
  **같은 깊이의 다음 번호**가 대문자 글 앞에 올 때만 나눈다(`INLINE_SPLIT`). 그 밖의 "No. 2." 같은 글은 두지 않는다.
- 같은 번호가 다시 나오면 `~2`를 붙여 구분하고, 건너뛴 번호(c. 다음 e.)와 함께 `numbering_issues`에 남긴다.
  번호를 고쳐 쓰지 않는다.
- 삭제 값(취소선)은 조항 text 기준 위치로 옮긴다. 번호 자리의 삭제 값은 `label_history`(예전 번호)다.
  해석은 edits.py가 한다.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from backend.engine.documents.model import Block, CrossReference, DeletedValue, ExtractedDocument

CLAUSE_TREE_VERSION = "clause-tree-v1"
PART = re.compile(r"^PART\s+(\d+)\s*[-–—]\s*(.+)$")
ARTICLE = re.compile(r"^(\d+)\.(\d+)\s+(\S.*)$")
# PDF에서 바꾼 문서는 절 번호와 첫 항목 번호가 표 칸 하나에 함께 떠 있다(14번 "2.2 A.").
COMPOUND = re.compile(r"^(\d+)\.(\d+)\s+([A-Z])\.$")
LABELS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (3, re.compile(r"^([A-Z])\.(?:\s+|$)")),
    (4, re.compile(r"^(\d{1,2})\.(?:\s+|$)")),
    (5, re.compile(r"^([a-z])\.(?:\s+|$)")),
    (6, re.compile(r"^\(?(\d{1,2})\)(?:\s+|$)")),
)
END_OF_SECTION = re.compile(r"^END OF SECTION\b", re.I)
SENTENCE_END = (".", ":", ";", "?", "!")
BOILERPLATE_MAX = 160
BOILERPLATE_REPEAT = 3


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Clause(Strict):
    clause_no: str
    label: str = Field(description="문단 앞 번호 그대로(PART 2, 2.5, C., 1.).")
    label_history: list[str] = Field(default=[], description="취소선으로 지운 예전 번호.")
    depth: int = Field(ge=1, le=6)
    parent_no: str | None
    ordinal: int = Field(ge=0)
    title: str | None = Field(description="PART·절(깊이 1·2)만. 그 밖에는 null.")
    text: str = Field(description="번호를 뺀 현행 본문. 이어 붙인 문단 포함.")
    deleted_values: list[DeletedValue] = Field(description="text 기준 위치로 옮긴 삭제 값.")
    flags: list[str] = []
    block_ids: list[str]
    table_rows: list[list[str]] = []
    table_deleted_values: list["TableDeleted"] = []
    page: int | None = None
    cross_references: list[CrossReference] = []


class TableDeleted(Strict):
    row: int = Field(ge=0, description="table_rows 안 행 순번.")
    col: int = Field(ge=0, description="그 행의 글자 있는 칸 순번.")
    value: DeletedValue


class NumberingIssue(Strict):
    clause_no: str
    kind: str = Field(description="DUPLICATE(같은 번호 반복) 또는 GAP(건너뜀).")
    detail: str


class ClauseTree(Strict):
    version: str = CLAUSE_TREE_VERSION
    sha256: str
    edit_trace_support: bool
    section_title: str | None
    clauses: list[Clause]
    preamble_block_ids: list[str]
    boilerplate_block_ids: list[str]
    numbering_issues: list[NumberingIssue]


def _next(token: str) -> str | None:
    if token.isdigit():
        return str(int(token) + 1)
    if len(token) == 1 and token.isalpha() and token not in "zZ":
        return chr(ord(token) + 1)
    return None


@dataclass
class _Open:
    clause_no: str
    token: str
    label: str
    depth: int
    parent_no: str | None
    title: str | None
    text: str
    deleted: list[DeletedValue]
    history: list[str]
    block_ids: list[str]
    page: int | None
    flags: set[str] = field(default_factory=set)
    rows: list[list[str]] = field(default_factory=list)
    row_deleted: list[TableDeleted] = field(default_factory=list)
    refs: list[CrossReference] = field(default_factory=list)


def _shape(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip())


def _label(text: str) -> tuple[int, str, str, str | None] | None:
    """문단 앞 번호를 읽는다.

    Returns:
        (깊이, 번호 토큰, 번호 글자 그대로, 제목) 또는 None. 제목은 PART·절만.
    """
    if m := PART.match(text):
        head = text[: m.start(2)].strip()
        return 1, f"PART {m.group(1)}", head, m.group(2).strip()
    if m := ARTICLE.match(text):
        return 2, f"{m.group(1)}.{m.group(2)}", f"{m.group(1)}.{m.group(2)}", m.group(3).strip()
    for depth, pattern in LABELS:
        if m := pattern.match(text):
            return depth, m.group(1), m.group(0).strip(), None
    return None


def _strip(
    text: str, deleted: list[DeletedValue], cut: int
) -> tuple[str, list[DeletedValue], list[str]]:
    """앞 cut 글자(번호)를 떼고 삭제 값 위치를 옮긴다. 번호 자리 삭제 값은 예전 번호로 돌려준다."""
    body = text[cut:]
    lead = len(body) - len(body.lstrip())
    body = body.lstrip()
    moved: list[DeletedValue] = []
    history: list[str] = []
    for value in deleted:
        if value.position == 0 and _label(value.text.strip() + " x") is not None:
            history.append(value.text.strip())
            continue
        position = max(0, value.position - cut - lead)
        moved.append(value.model_copy(update={"position": min(position, len(body))}))
    return body, moved, history


def _shift(deleted: list[DeletedValue], offset: int) -> list[DeletedValue]:
    return [v.model_copy(update={"position": v.position + offset}) for v in deleted]


def _row_label(cells: list[Block]) -> tuple[int, list[Block]] | None:
    """표 한 행의 첫 글자 있는 칸이 번호만이면 그 칸 위치와 나머지 칸을 돌려준다."""
    filled = [i for i, c in enumerate(cells) if c.text.strip()]
    if len(filled) < 2:
        return None
    first = cells[filled[0]]
    found = _label(first.text.strip() + " x")
    if found is None or found[0] < 3 or first.text.strip() != found[2]:
        return None
    return filled[0], [c for c in cells[filled[0] + 1 :] if c.text.strip()]


def _boilerplate(blocks: list[Block]) -> set[str]:
    counts = Counter(
        _shape(b.text)
        for b in blocks
        if b.kind == "PARAGRAPH" and b.text.strip() and len(b.text) <= BOILERPLATE_MAX
    )
    noisy = {shape for shape, n in counts.items() if n >= BOILERPLATE_REPEAT}
    out = set()
    for b in blocks:
        text = b.text.strip()
        if b.kind != "PARAGRAPH" or not text:
            continue
        if END_OF_SECTION.match(text) or (_shape(text) in noisy and _label(text) is None):
            out.add(b.block_id)
    return out


def build_clause_tree(document: ExtractedDocument) -> ClauseTree:
    """문서 순서대로 번호 패턴을 읽어 조항 트리를 만든다.

    Args:
        document: KAN-96 추출 결과.

    Returns:
        ClauseTree: 문서 순서의 조항, 머리말·반복 문구 블록, 번호 문제.
    """
    noise = _boilerplate(document.blocks)
    clauses: list[Clause] = []
    issues: list[NumberingIssue] = []
    preamble: list[str] = []
    boilerplate = sorted(noise, key=lambda x: [b.block_id for b in document.blocks].index(x))
    stack: list[_Open] = []
    current: _Open | None = None
    seen: Counter[str] = Counter()
    last_token: dict[tuple[str | None, int], str] = {}
    section_title: str | None = None
    started = False
    pending: tuple[str, str, Block] | None = None

    def close() -> None:
        nonlocal current
        while current is not None and current.depth >= 3:
            nxt = _next(current.token)
            if nxt is None:
                break
            found = re.search(
                r"(?<=[.;:])\s+(" + re.escape(nxt) + r")\.\s+(?=[A-Z(<\"])", current.text
            )
            if found is None:
                break
            head_text, tail = current.text[: found.start()], current.text[found.end() :]
            cut = found.end()
            kept = [v for v in current.deleted if v.position <= len(head_text)]
            moved = [
                v.model_copy(update={"position": v.position - cut})
                for v in current.deleted
                if v.position >= cut
            ]
            sibling_ids = list(current.block_ids[-1:])
            current.text, current.deleted = head_text, kept
            emit(current)
            base = f"{current.parent_no}.{nxt}" if current.parent_no else nxt
            last_token[(current.parent_no, current.depth)] = nxt
            seen[base] += 1
            clause_no = base if seen[base] == 1 else f"{base}~{seen[base]}"
            sibling = _Open(
                clause_no=clause_no,
                token=nxt,
                label=f"{nxt}.",
                depth=current.depth,
                parent_no=current.parent_no,
                title=None,
                text=tail,
                deleted=moved,
                history=[],
                block_ids=sibling_ids,
                page=current.page,
                flags={"INLINE_SPLIT"},
            )
            if stack and stack[-1] is current:
                stack[-1] = sibling
            current = sibling
        if current is None:
            return
        emit(current)
        current = None

    def emit(item: _Open) -> None:
        current_ = item
        clauses.append(
            Clause(
                clause_no=current_.clause_no,
                label=current_.label,
                label_history=current_.history,
                depth=current_.depth,
                parent_no=current_.parent_no,
                ordinal=len(clauses),
                title=current_.title,
                text=current_.text,
                deleted_values=current_.deleted,
                flags=sorted(current_.flags),
                block_ids=current_.block_ids,
                table_rows=current_.rows,
                table_deleted_values=current_.row_deleted,
                page=current_.page,
                cross_references=current_.refs,
            )
        )

    def open_clause(
        depth: int,
        token: str,
        label: str,
        title: str | None,
        block: Block,
        body: str,
        deleted: list[DeletedValue],
        history: list[str],
        extra_ids: list[str],
    ) -> None:
        nonlocal current
        close()
        while stack and stack[-1].depth >= depth:
            stack.pop()
        parent = stack[-1] if stack else None
        if depth == 1:
            base = token
        elif depth == 2:
            base = token
        else:
            base = f"{parent.clause_no}.{token}" if parent else token
        _check_sequence(parent.clause_no if parent else None, depth, token, base)
        seen[base] += 1
        clause_no = base if seen[base] == 1 else f"{base}~{seen[base]}"
        if seen[base] > 1:
            issues.append(
                NumberingIssue(clause_no=clause_no, kind="DUPLICATE", detail=f"{base} 반복")
            )
        current = _Open(
            clause_no=clause_no,
            token=token,
            label=label,
            depth=depth,
            parent_no=parent.clause_no if parent else None,
            title=title,
            text=body,
            deleted=deleted,
            history=history,
            block_ids=[block.block_id, *extra_ids],
            page=block.position.page,
            refs=list(block.cross_references),
        )
        stack.append(current)

    def _check_sequence(parent_no: str | None, depth: int, token: str, base: str) -> None:
        key = (parent_no, depth)
        previous = last_token.get(key)
        last_token[key] = token
        if previous is None or depth <= 2:
            return
        if previous.isdigit() and token.isdigit():
            expected = str(int(previous) + 1)
        elif len(previous) == 1 and len(token) == 1 and previous.isalpha():
            expected = chr(ord(previous) + 1)
        else:
            return
        if token != expected and token != previous:
            issues.append(
                NumberingIssue(clause_no=base, kind="GAP", detail=f"{previous} 다음 {token}")
            )

    def append(text: str, deleted: list[DeletedValue], block: Block) -> None:
        if current is None:
            preamble.append(block.block_id)
            return
        joined = current.text.rstrip()
        broken = bool(joined) and (not joined.endswith(SENTENCE_END) or text[:1].islower())
        sep = " " if broken else "\n"
        if not joined:
            sep = ""
        offset = len(joined) + len(sep)
        current.text = joined + sep + text
        current.deleted = current.deleted + _shift(deleted, offset)
        current.block_ids.append(block.block_id)
        current.refs.extend(block.cross_references)
        if broken:
            current.flags.add("LINE_BREAK_REJOINED")

    def open_compound(
        article: str,
        letter: str,
        head: Block,
        body: str,
        deleted: list[DeletedValue],
        extra: list[str],
        flag: str | None,
    ) -> None:
        open_clause(2, article, article, None, head, "", [], [], [])
        open_clause(3, letter, f"{letter}.", None, head, body, deleted, [], extra)
        if current is not None:
            current.flags.add("FROM_TABLE_ROW")
            if flag:
                current.flags.add(flag)

    def apply_pending(depth: int, token: str) -> None:
        """미뤄 둔 절(2.2 A.)을 번호가 1·a로 다시 시작하는 곳에서 연다."""
        nonlocal pending
        if pending is None or depth != 4 or token != "1" or not stack:
            return
        parent = next((o for o in reversed(stack) if o.depth < depth), None)
        previous = last_token.get((parent.clause_no if parent else None, depth))
        if previous is None or previous == token:
            return
        article, letter, head = pending
        pending = None
        open_compound(article, letter, head, "", [], [], "DEFERRED_ARTICLE")

    def row_body(rest: list[Block]) -> tuple[str, list[DeletedValue]]:
        body, deleted = "", []
        for cell in rest:
            piece, moved, _h = _strip(cell.text, cell.deleted_values, 0)
            sep = " " if body else ""
            deleted += _shift(moved, len(body) + len(sep))
            body += sep + piece
        return body, deleted

    blocks = document.blocks
    i = 0
    while i < len(blocks):
        block = blocks[i]
        text = block.text.strip()
        if block.block_id in noise:
            i += 1
            continue
        if block.kind == "TABLE_CELL":
            table, row = block.position.table_index, block.position.row
            cells = []
            while (
                i < len(blocks)
                and blocks[i].kind == "TABLE_CELL"
                and blocks[i].position.table_index == table
                and blocks[i].position.row == row
            ):
                cells.append(blocks[i])
                i += 1
            filled = [c for c in cells if c.text.strip()]
            compound = COMPOUND.match(filled[0].text.strip()) if filled and started else None
            if compound is not None:
                article = f"{compound.group(1)}.{compound.group(2)}"
                rest = filled[1:]
                lead = _label(rest[0].text.strip() + " x") if rest else None
                if rest and lead is not None and rest[0].text.strip() == lead[2]:
                    # 뒤가 번호 칸이면 앞 목록의 이어짐이다. 절은 번호가 다시 시작할 때 연다.
                    pending = (article, compound.group(3), filled[0])
                    cells = rest
                else:
                    body, deleted = row_body(rest)
                    open_compound(
                        article,
                        compound.group(3),
                        filled[0],
                        body,
                        deleted,
                        [c.block_id for c in rest],
                        None,
                    )
                    continue
            labeled = _row_label(cells)
            if labeled is not None and started:
                at, rest = labeled
                head = cells[at]
                found = _label(head.text.strip() + " x")
                assert found is not None
                depth, token, label, _ = found
                history = [v.text.strip() for v in head.deleted_values if v.position == 0]
                body, deleted = row_body(rest)
                apply_pending(depth, token)
                open_clause(
                    depth,
                    token,
                    label,
                    None,
                    head,
                    body,
                    deleted,
                    history,
                    [c.block_id for c in rest],
                )
                if current is not None:
                    current.flags.add("FROM_TABLE_ROW")
            elif started and current is not None:
                kept = [c for c in cells if c.text.strip()]
                if kept:
                    row_no = len(current.rows)
                    current.rows.append([c.text.strip() for c in kept])
                    for col, cell in enumerate(kept):
                        indent = len(cell.text) - len(cell.text.lstrip())
                        for value in cell.deleted_values:
                            position = max(0, value.position - indent)
                            current.row_deleted.append(
                                TableDeleted(
                                    row=row_no,
                                    col=col,
                                    value=value.model_copy(update={"position": position}),
                                )
                            )
                    current.block_ids.extend(c.block_id for c in kept)
            else:
                preamble.extend(c.block_id for c in cells)
            continue
        if block.kind != "PARAGRAPH" or not text:
            i += 1
            continue
        if section_title is None and text.upper().startswith("SECTION "):
            section_title = text
            preamble.append(block.block_id)
            i += 1
            continue
        found = _label(text)
        if found is not None and (found[0] == 1 or started):
            depth, token, label, title = found
            started = True
            cut = block.text.find(label) + len(label)
            body, deleted, history = _strip(block.text, block.deleted_values, cut)
            if depth <= 2:
                # 제목 줄의 삭제 값(예: PART 3의 "(NOT IN CONTRACT)")은 제목 기준으로 남긴다.
                title_deleted = deleted
                open_clause(
                    depth,
                    token,
                    label,
                    body.strip() or title,
                    block,
                    "",
                    title_deleted,
                    history,
                    [],
                )
            else:
                apply_pending(depth, token)
                open_clause(depth, token, label, None, block, body, deleted, history, [])
            i += 1
            continue
        if not started:
            preamble.append(block.block_id)
        else:
            append(block.text.strip(), block.deleted_values, block)
        i += 1
    close()
    return ClauseTree(
        sha256=document.sha256,
        edit_trace_support=document.edit_trace_support,
        section_title=section_title,
        clauses=clauses,
        preamble_block_ids=preamble,
        boilerplate_block_ids=boilerplate,
        numbering_issues=issues,
    )
