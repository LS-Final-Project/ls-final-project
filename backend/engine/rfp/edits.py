"""조항의 편집 흔적(취소선·변경 추적 삭제)을 해석한다(KAN-185, 기획서 4.9·5.2). 순수 함수다.

원칙: **현행 텍스트가 요구다. 삭제 값은 이력으로만 둔다.** 일반 텍스트 추출은 서식을 버려 지운 값과 남은
값이 붙어 읽힌다(14번 2.5 C.1 "5033%"). KAN-96 추출이 DOCX XML에서 두 값을 나눠 주므로 여기서는
나눈 결과를 원문 복원(`original`)과 현행 해석(`current`)으로 따로 보여 주고 종류를 붙인다.

| 종류 | 판정 | 해석 |
| --- | --- | --- |
| `RENUMBER` | 번호 자리의 삭제(조항 트리의 `label_history`) | 번호만 바뀜. 현행 번호로 조항을 찾는다 |
| `SCOPE` | 괄호로 둘러싼 문구 삭제(`(NOT IN CONTRACT)`) | 범위 표시가 지워짐. 그 조항 전체가 현행 범위다 |
| `TYPO` | 두 글자 이하 영문자가 단어에 붙어 지워짐 | 오탈자 정정. 의미 변화 없음 |
| `VALUE` | 그 밖(숫자·낱말) | 값이 바뀜. 현행 값을 요구로 쓰고 지운 값은 이력이다 |

군(group): 사람이 한 번 판단하면 되는 단위다. 번호 다시 매김은 절(2.5 등)마다 한 번의 편집이라 절별로 한 군이고
(14번 2.5 D.→E.…O.→P. 12건이 한 군), 값·범위·오탈자는 각각 따로 확인할 대상이라 흔적 하나가 한 군이다.

PDF는 서식을 읽을 수 없어 `edit_trace_support=false`이고, 흔적이 0건이어도 "없음"이 아니라 "확인 불가"다.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.engine.documents.model import DeletedValue
from backend.engine.rfp.clauses import Clause, ClauseTree

EditKind = Literal["RENUMBER", "SCOPE", "TYPO", "VALUE"]
Location = Literal["label", "title", "text", "table"]
WINDOW = 30
MEANING: dict[str, str] = {
    "RENUMBER": "번호만 바뀜. 현행 번호로 조항을 찾는다.",
    "SCOPE": "범위 표시가 지워짐. 조항 전체를 현행 범위로 본다.",
    "TYPO": "오탈자 정정. 의미 변화 없음.",
    "VALUE": "값이 바뀜. 현행 값을 요구로 쓰고 지운 값은 이력으로만 둔다.",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EditTrace(Strict):
    clause_no: str
    kind: EditKind
    location: Location
    deleted: str
    position: int | None = Field(description="본문·제목·표 칸 기준 위치. 번호 자리는 null.")
    original: str = Field(description="지운 글자를 되살린 원문 조각(앞뒤 30자).")
    current: str = Field(description="현행 조각. 요구 해석은 이쪽을 쓴다.")
    marked: str = Field(description="~~지운 글자~~를 표시한 조각(처리 전후 예시용).")
    meaning: str


class EditGroup(Strict):
    group_no: int = Field(ge=1)
    kind: EditKind
    article: str
    clause_nos: list[str]
    traces: int = Field(ge=1)


class EditReport(Strict):
    edit_trace_support: bool
    status: Literal["FOUND", "NONE", "UNSUPPORTED"]
    traces: list[EditTrace]
    groups: list[EditGroup]
    counts: dict[str, int]


def restore(text: str, deleted: list[DeletedValue]) -> str:
    """지운 글자를 제자리에 되살린 원문.

    Args:
        text: 현행 텍스트.
        deleted: text 기준 위치의 삭제 값.

    Returns:
        str: 원문(편집 전) 텍스트.
    """
    out = text
    for value in sorted(deleted, key=lambda v: v.position, reverse=True):
        at = min(value.position, len(out))
        out = out[:at] + value.text + out[at:]
    return out


def mark(text: str, deleted: list[DeletedValue]) -> str:
    """지운 글자를 ~~ ~~로 표시한 텍스트."""
    out = text
    for value in sorted(deleted, key=lambda v: v.position, reverse=True):
        at = min(value.position, len(out))
        out = out[:at] + f"~~{value.text}~~" + out[at:]
    return out


def classify(text: str, value: DeletedValue) -> EditKind:
    """삭제 값 하나의 종류(번호 자리 제외).

    Args:
        text: 삭제 값이 있던 현행 텍스트.
        value: 삭제 값.

    Returns:
        EditKind: SCOPE·TYPO·VALUE.
    """
    word = value.text.strip()
    if word.startswith("(") and word.endswith(")"):
        return "SCOPE"
    at = min(value.position, len(text))
    before = text[at - 1] if at > 0 else ""
    after = text[at] if at < len(text) else ""
    if (
        word.isalpha()
        and len(word) <= 2
        and value.text == word
        and (before.isalpha() or after.isalpha())
    ):
        return "TYPO"
    return "VALUE"


def _window(text: str, deleted: DeletedValue) -> tuple[str, str, str]:
    at = min(deleted.position, len(text))
    start, end = max(0, at - WINDOW), min(len(text), at + WINDOW)
    local = deleted.model_copy(update={"position": at - start})
    piece = text[start:end]
    return restore(piece, [local]), piece, mark(piece, [local])


def _article(clause_no: str) -> str:
    return clause_no if clause_no.startswith("PART") else ".".join(clause_no.split(".")[:2])


def _clause_traces(clause: Clause) -> list[EditTrace]:
    traces: list[EditTrace] = []
    for old in clause.label_history:
        traces.append(
            EditTrace(
                clause_no=clause.clause_no,
                kind="RENUMBER",
                location="label",
                deleted=old,
                position=None,
                original=old,
                current=clause.label,
                marked=f"~~{old}~~{clause.label}",
                meaning=MEANING["RENUMBER"],
            )
        )
    title_line = clause.depth <= 2
    base = clause.title or "" if title_line else clause.text
    for value in clause.deleted_values:
        kind = classify(base, value)
        original, current, marked = _window(base, value)
        traces.append(
            EditTrace(
                clause_no=clause.clause_no,
                kind=kind,
                location="title" if title_line else "text",
                deleted=value.text,
                position=value.position,
                original=original,
                current=current,
                marked=marked,
                meaning=MEANING[kind],
            )
        )
    for cell in clause.table_deleted_values:
        text = clause.table_rows[cell.row][cell.col]
        kind = classify(text, cell.value)
        original, current, marked = _window(text, cell.value)
        traces.append(
            EditTrace(
                clause_no=clause.clause_no,
                kind=kind,
                location="table",
                deleted=cell.value.text,
                position=cell.value.position,
                original=original,
                current=current,
                marked=marked,
                meaning=MEANING[kind],
            )
        )
    return traces


def interpret_edits(tree: ClauseTree) -> EditReport:
    """조항 트리의 모든 편집 흔적을 해석하고 군으로 묶는다.

    Args:
        tree: build_clause_tree 결과.

    Returns:
        EditReport: 흔적, 군, 종류별 건수. PDF는 UNSUPPORTED.
    """
    if not tree.edit_trace_support:
        return EditReport(
            edit_trace_support=False, status="UNSUPPORTED", traces=[], groups=[], counts={}
        )
    traces = [t for clause in tree.clauses for t in _clause_traces(clause)]
    groups: list[EditGroup] = []
    renumber: dict[str, int] = {}
    for trace in traces:
        article = _article(trace.clause_no)
        at = renumber.get(article) if trace.kind == "RENUMBER" else None
        if at is not None:
            group = groups[at]
            nos = (
                group.clause_nos
                if trace.clause_no in group.clause_nos
                else [*group.clause_nos, trace.clause_no]
            )
            groups[at] = group.model_copy(update={"clause_nos": nos, "traces": group.traces + 1})
        else:
            if trace.kind == "RENUMBER":
                renumber[article] = len(groups)
            groups.append(
                EditGroup(
                    group_no=len(groups) + 1,
                    kind=trace.kind,
                    article=article,
                    clause_nos=[trace.clause_no],
                    traces=1,
                )
            )
    counts: dict[str, int] = {}
    for trace in traces:
        counts[trace.kind] = counts.get(trace.kind, 0) + 1
    return EditReport(
        edit_trace_support=True,
        status="FOUND" if traces else "NONE",
        traces=traces,
        groups=groups,
        counts=counts,
    )
