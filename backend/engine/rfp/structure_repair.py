"""번호 목록이 빈 자리만 모델로 다시 나눈다(KAN-188). 모델 호출은 주입한 함수가 한다.

조항 트리는 코드가 만든다(clauses.build_clause_tree). 코드가 표시한 자리(`detect_structure_gaps`)에서만 모델을 부르고,
모델은 **경계만** 낸다: 빠진 번호마다 그 항목이 시작하는 원문 글자(`start_text`). 글을 새로 쓰지 않는다.
코드가 경계를 검사한다.

- 번호 목록이 표시한 빈 번호와 순서까지 같다(지어낸 번호·빠진 번호 거부).
- `start_text`가 조항 현행 텍스트에 그대로, 한 번만 있다. 앞 경계보다 뒤에 있고 앞 경계의 글과 겹치지 않는다.
- 경계 자리가 낱말 시작이고 그 번호 표지(`b.`·`(b)`·`b)`)로 시작하며 뒤에 글이 있다.
- 만들 조항 번호가 이미 있으면 거부한다.

하나라도 어기면 그 자리는 고치지 않고 이유를 남긴다(`Rejected`). 통과하면 그 조항을 경계에서 나눠 새 조항을 바로 뒤에
넣는다(블록 ID는 원래 조항 것을 그대로 쓴다, `_inline_items`와 같은 방식). 삭제 값·표 행이 있는 조항은 위치를 옮기지 않고
부르지도 않는다.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.engine.documents.model import CrossReference
from backend.engine.rfp.clauses import Clause, ClauseTree, StructureGap, detect_structure_gaps
from backend.engine.rfp.requirements import _sum_usage
from backend.shared.ai import Generation, Usage

STRUCTURE_REPAIR_VERSION = "rfp-structure-repair-v1"
# 문서 하나의 호출 상한. 넘는 자리는 부르지 않고 CALL_LIMIT로 남긴다.
MAX_REPAIR_CALLS = 20
# 한 번에 보낼 조항 글 상한(글자). 더 긴 조항은 부르지 않는다.
MAX_REPAIR_CHARS = 4000
REPAIRED = "STRUCTURE_REPAIRED"


class Boundary(BaseModel):
    label: str = Field(description="번호 토큰(예 a, 2).")
    start_text: str = Field(description="그 항목이 시작하는 원문 글자 그대로(번호 표지부터).")


class RepairOutput(BaseModel):
    """응답 스키마. Gemini가 additionalProperties를 받지 않아 extra=forbid를 두지 않는다."""

    items: list[Boundary]


class Rejected(BaseModel):
    model_config = ConfigDict(frozen=True)
    clause_no: str
    labels: list[str]
    reason: str


@dataclass
class RepairResult:
    tree: ClauseTree
    applied: list[StructureGap] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    calls: int = 0
    usage: Usage = field(default_factory=lambda: Usage(0, 0, 0, 0, 0))
    model_versions: set[str] = field(default_factory=set)


PROMPT = """You mark where numbered list items begin inside one clause of an electrical equipment specification.
Return JSON matching the schema. The clause text should contain the list items {labels} (numbered like
"{example}") that were not separated from the surrounding text.
For each item, in this order, return its label and start_text: an exact copy of the first 20 to 60 characters
of the item as written in the text, beginning with its number. Copy characters exactly; do not fix spelling or
spacing. Do not write any other text. If an item is not written in the clause, return an empty items list.

Clause {clause_no}:
{text}
"""


def prompt(gap: StructureGap, clause: Clause) -> str:
    """모델에 보낼 지시문. 조항 글은 JSON 문자열로 넣어 지시문과 섞이지 않게 한다."""
    return PROMPT.format(
        labels=", ".join(gap.labels),
        example=gap.label_format.format(gap.labels[0]),
        clause_no=clause.clause_no,
        text=json.dumps(clause.text, ensure_ascii=False),
    )


def boundaries(gap: StructureGap, clause: Clause, output: str) -> list[int] | str:
    """응답을 검사해 항목 시작 위치(clause.text 기준)를 돌려준다. 어기면 거부 이유 코드."""
    try:
        items = RepairOutput.model_validate_json(output).items
    except ValidationError:
        return "SCHEMA_INVALID"
    if not items:
        return "NO_ITEMS"
    if [item.label.strip().strip("().") for item in items] != gap.labels:
        return "LABELS_MISMATCH"
    text = clause.text
    starts: list[int] = []
    cursor = 0
    for item, label in zip(items, gap.labels, strict=True):
        piece = item.start_text
        at = text.find(piece) if piece else -1
        if at < 0:
            return "NOT_SUBSTRING"
        if text.find(piece, at + 1) >= 0:
            return "AMBIGUOUS_START"
        if starts and at <= starts[-1]:
            return "OUT_OF_ORDER"
        if at < cursor:
            return "OVERLAP"
        if at > 0 and not text[at - 1].isspace():
            return "NOT_AT_WORD_START"
        if not re.match(r"\(?" + re.escape(label) + r"[.)]\s+\S", text[at:]):
            return "LABEL_NOT_AT_START"
        starts.append(at)
        cursor = at + len(piece)
    return starts


def _marker(text: str) -> int:
    m = re.match(r"\(?[^\s.)]+[.)]\s+", text)
    return m.end() if m else 0


def split(tree: ClauseTree, gap: StructureGap, starts: list[int]) -> ClauseTree | str:
    """검사한 경계에서 조항을 나눈다. 새 조항은 원래 조항 바로 뒤, 원래 블록 ID를 쓴다."""
    clauses = list(tree.clauses)
    index = next(n for n, c in enumerate(clauses) if c.clause_no == gap.clause_no)
    clause = clauses[index]
    numbers = {c.clause_no for c in clauses}
    new_numbers = [f"{gap.parent_no}.{label}" for label in gap.labels]
    if any(no in numbers for no in new_numbers):
        return "CLAUSE_EXISTS"
    text = clause.text
    head = text[: starts[0]].strip()
    if not head and clause.clause_no != gap.parent_no:
        return "EMPTY_HEAD"
    pieces = [text[a:b].strip() for a, b in zip(starts, [*starts[1:], len(text)], strict=True)]

    def refs(part: str) -> list[CrossReference]:
        return [r for r in clause.cross_references if r.text in part]

    added = [
        Clause(
            clause_no=no,
            label=gap.label_format.format(label),
            depth=gap.depth,
            parent_no=gap.parent_no,
            ordinal=0,
            title=None,
            text=piece[_marker(piece) :].strip(),
            deleted_values=[],
            flags=[REPAIRED],
            block_ids=clause.block_ids,
            page=clause.page,
            cross_references=refs(piece),
        )
        for no, label, piece in zip(new_numbers, gap.labels, pieces, strict=True)
    ]
    head_clause = clause.model_copy(
        update={
            "text": head,
            "flags": sorted({*clause.flags, REPAIRED}),
            "cross_references": refs(head),
        }
    )
    clauses[index : index + 1] = [head_clause, *added]
    issue = gap.issue()
    issues = [
        i.model_copy(update={"kind": REPAIRED}) if i == issue else i for i in tree.numbering_issues
    ]
    return tree.model_copy(
        update={
            "clauses": [c.model_copy(update={"ordinal": n}) for n, c in enumerate(clauses)],
            "numbering_issues": issues,
        }
    )


def _check(gap: StructureGap, clause: Clause) -> str | None:
    if clause.deleted_values:
        return "TARGET_HAS_DELETED_VALUES"
    if clause.table_rows:
        return "TARGET_HAS_TABLE_ROWS"
    if not clause.text.strip():
        return "TARGET_EMPTY"
    if len(clause.text) > MAX_REPAIR_CHARS:
        return "TARGET_TOO_LONG"
    return None


def repair_tree(tree: ClauseTree, provider: Callable[[str], Generation]) -> RepairResult:
    """표시한 자리마다 한 번 부르고, 검사를 통과한 경계만 트리에 적용한다.

    Args:
        tree: build_clause_tree 결과.
        provider: 지시문을 받아 공통 Generation을 돌려주는 호출(구조화 출력 RepairOutput). 공급자·lease 예외는
            그대로 올라간다(vision.transcribe와 같다).

    Returns:
        RepairResult: 고친 트리(통과한 자리가 없으면 입력 그대로), 적용·거부 자리, 호출 수·사용량.
    """
    result = RepairResult(tree=tree)
    for gap in detect_structure_gaps(tree.clauses):
        clause = next((c for c in result.tree.clauses if c.clause_no == gap.clause_no), None)
        problem = "TARGET_MISSING" if clause is None else _check(gap, clause)
        if problem is None and result.calls >= MAX_REPAIR_CALLS:
            problem = "CALL_LIMIT"
        if problem is None and clause is not None:
            response = provider(prompt(gap, clause))
            result.calls += 1
            result.usage = _sum_usage(result.usage, response.usage)
            if response.model_version:
                result.model_versions.add(response.model_version)
            if "MAX_TOKENS" in response.finish_reasons:
                problem = "TRUNCATED"
            else:
                starts = boundaries(gap, clause, response.text)
                repaired = split(result.tree, gap, starts) if isinstance(starts, list) else starts
                if isinstance(repaired, ClauseTree):
                    result.tree = repaired
                    result.applied.append(gap)
                    continue
                problem = repaired
        result.rejected.append(Rejected(clause_no=gap.clause_no, labels=gap.labels, reason=problem))
    return result


def fake_provider(_prompt: str) -> Generation:
    """경계를 내지 않는 고정 응답(키 없는 로컬 경로 확인). 모든 자리가 NO_ITEMS로 남는다."""
    return Generation(text='{"items": []}', usage=Usage(), model_version=None, finish_reasons=[])
