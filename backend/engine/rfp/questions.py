"""현행 조항에서 고객 확인용 질문 초안을 만든다(KAN-189). DB·모델 호출은 없다."""

import re
import unicodedata
from collections.abc import Sequence
from decimal import Decimal, DecimalException
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.engine.rfp.clauses import Clause, ClauseTree
from backend.engine.rfp.requirements import ExtractedRequirement

QuestionKind = Literal["PLACEHOLDER", "INCONSISTENCY", "APPROVAL_PROCESS"]
PLACEHOLDER = re.compile(r"<\s*insert\b[^<>]*>|\bTBD\b|\[\s*\]|_{3,}", re.I)
ALTERNATIVE = re.compile(
    r"\bor\s+approved\s+equal\b|\b(?:with|subject\s+to)\s+(?:the\s+)?owner(?:'s)?\s+approval\b",
    re.I,
)
# 단순 언급이 아니라 제출/승인 행동과 시점을 명시한 문장만 절차 근거로 쓴다.
PROCEDURE = re.compile(
    r"\bsubmit\b[^.;\n]*\bfor\s+(?:the\s+)?(?:owner(?:'s)?\s+)?approval\b|"
    r"\b(?:obtain|request)\b[^.;\n]*\bapproval\b[^.;\n]*\b(?:before|prior|within)\b|"
    r"\bapproval\b[^.;\n]*\b(?:before|prior|within)\b",
    re.I,
)
MATERIALS = re.compile(
    r"\b(?:submit|provide|attach|include)\b[^.;\n]*\b(?:data\s*sheets?|drawings?|"
    r"test\s+reports?|certificates?|comparison\s+(?:tables?|sheets?)|technical\s+data)\b",
    re.I,
)
UNCONFIRMED = re.compile(
    r"\b(?:not|never|no|without|pending|undetermined|undefined|unspecified)\b|"
    r"\bto\s+be\s+(?:defined|provided|confirmed|determined)\b",
    re.I,
)
EQUIPMENT_ID = re.compile(
    r"\bunit\b\s*#?\s*([a-z]|\d+[a-z]?|[a-z]+-\d+[a-z0-9-]*)\b|"
    r"\btag\b\s*#?\s*([a-z0-9-]+)\b|"
    r"\b(?:equipment|transformer|switchgear)\s*(?:no\.?|id|#)\s*([a-z0-9-]+)\b|"
    r"\b([a-z]+-\d+[a-z0-9-]*)\b",
    re.I,
)
# 이 질문 함수에서만 쓰는 좁은 환산 목록. RMS 등 수식어는 버리지 않는다.
UNITS: dict[str, tuple[str, str]] = {
    "v": ("V", "1"),
    "kv": ("V", "1000"),
    "a": ("A", "1"),
    "ka": ("A", "1000"),
    "va": ("VA", "1"),
    "kva": ("VA", "1000"),
    "mva": ("VA", "1000000"),
    "w": ("W", "1"),
    "kw": ("W", "1000"),
    "mw": ("W", "1000000"),
    "s": ("s", "1"),
    "ms": ("s", "0.001"),
    "hz": ("Hz", "1"),
    "khz": ("Hz", "1000"),
    "%": ("%", "1"),
    "percent": ("%", "1"),
    "°c": ("°C", "1"),
    "degc": ("°C", "1"),
    "mm": ("m", "0.001"),
    "cm": ("m", "0.01"),
    "m": ("m", "1"),
}
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


class QuestionSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_no: str
    quote: str


class QuestionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: QuestionKind
    sources: list[QuestionSource]
    question: str
    pre_reply_note: str


def _norm(text: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().split()).translate(
        str.maketrans("–—‑‐", "----")
    )


def _quantity(value: str, unit: str | None) -> tuple[str, str]:
    text = _norm(value)
    # 정당한 천 단위 쉼표만 제거한다. 쉼표 소수·범위·복합 수치는 추측하지 않는다.
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    match = re.fullmatch(rf"({NUMBER})\s*([a-z%°]*)", text)
    given_unit = _norm(unit)
    if match:
        suffix = match[2]
        if not suffix or not given_unit or suffix == given_unit:
            effective = suffix or given_unit
            dimension, scale = UNITS.get(effective, (effective, "1"))
            try:
                return str(Decimal(match[1]) * Decimal(scale)), dimension
            except DecimalException:
                # 표현/환산할 수 없는 수치는 원래 문자열로 보수적으로 비교한다.
                return text, given_unit
    return text, given_unit


def _same_quantity(left: tuple[str, str], right: tuple[str, str]) -> bool:
    if left[1] != right[1]:
        return False
    if re.fullmatch(NUMBER, left[0]) and re.fullmatch(NUMBER, right[0]):
        try:
            return Decimal(left[0]) == Decimal(right[0])
        except DecimalException:
            return left[0] == right[0]
    return left[0] == right[0]


def _pieces(clause: Clause) -> list[str]:
    return [
        s
        for s in [clause.title or "", clause.text, *[c for r in clause.table_rows for c in r]]
        if s
    ]


def _sources(clause: Clause) -> list[QuestionSource]:
    return [QuestionSource(clause_no=clause.clause_no, quote=s) for s in _pieces(clause)]


def _conditions_match(left: ExtractedRequirement, right: ExtractedRequirement) -> bool:
    def normalized(req: ExtractedRequirement) -> list[tuple[str, tuple[str, str]]]:
        return sorted((_norm(c.name), _quantity(c.value, c.unit)) for c in req.conditions)

    a, b = normalized(left), normalized(right)
    return len(a) == len(b) and all(
        name_a == name_b and _same_quantity(value_a, value_b)
        for (name_a, value_a), (name_b, value_b) in zip(a, b, strict=True)
    )


def _identifiers(equipment: str | None) -> set[str]:
    return {next(g for g in m.groups() if g) for m in EQUIPMENT_ID.finditer(_norm(equipment))}


def _eligible(req: ExtractedRequirement, clauses: dict[str, Clause]) -> bool:
    clause = clauses.get(req.clause_no)
    if (
        clause is None
        or req.kind != "SPEC"
        or req.equipment_family in {None, "OTHER"}
        or req.attribute_key == "OTHER"
        or not req.value
        or not req.quote.strip()
        or PLACEHOLDER.search(req.quote)
    ):
        return False
    # 이전 추출 결과를 받아도 현행 본문에 없는 값은 사용하지 않는다.
    current = any(req.quote in text for text in _pieces(clause))
    value_present = re.search(
        r"(?<!\w)" + re.escape(_norm(req.value)) + r"(?!\w)", _norm(req.quote)
    )
    return current and value_present is not None


def _confirmed_instruction(source: QuestionSource, pattern: re.Pattern[str]) -> bool:
    # 문장 단위로 분리하여 다른 문장의 부정/미정 표기와 섞지 않는다.
    return any(
        pattern.search(sentence) is not None
        and not UNCONFIRMED.search(sentence)
        and not PLACEHOLDER.search(sentence)
        for sentence in re.split(r"[.\n]+", source.quote)
    )


def _context(clause: Clause, clauses: dict[str, Clause]) -> list[Clause]:
    selected = {clause.clause_no}
    parent = clause.parent_no
    while parent in clauses and parent not in selected:
        selected.add(parent)
        parent = clauses[parent].parent_no
    # 대체 허용 조항의 하위만 포함한다. 상위의 다른 자식은 포함하지 않는다.
    pending = [clause.clause_no]
    while pending:
        current = pending.pop()
        for child in clauses.values():
            if child.parent_no == current and child.clause_no not in selected:
                selected.add(child.clause_no)
                pending.append(child.clause_no)
    # 포함 문맥에서 해결된 직접 참조만 따라간다(참조의 참조는 확장하지 않는다).
    targets = {
        ref.target_block_id
        for no in selected
        for ref in clauses[no].cross_references
        if ref.target_kind == "CLAUSE" and ref.resolved and ref.target_block_id
    }
    selected.update(c.clause_no for c in clauses.values() if targets.intersection(c.block_ids))
    return [c for c in clauses.values() if c.clause_no in selected]


def generate_question_drafts(
    tree: ClauseTree, requirements: Sequence[ExtractedRequirement]
) -> list[QuestionDraft]:
    """현행 원문과 요구에서 결정론적으로 고객 질문 초안을 만든다.

    Args:
        tree: 삭제 값이 본문과 분리된 조항 트리.
        requirements: 현행 조항에서 추출한 요구. 없는 조항·오래된 인용은 제외한다.

    Returns:
        list[QuestionDraft]: 자리표시자, 조항 간 불일치 후보, 대체 승인 절차 질문 순서의 초안.
            미정 항목은 회신 전 판정 제외 메모를 포함한다. 자동 판정·승인은 하지 않는다.
    """
    clauses = {c.clause_no: c for c in tree.clauses}
    drafts: list[QuestionDraft] = []
    for clause in tree.clauses:
        sources = [s for s in _sources(clause) if PLACEHOLDER.search(s.quote)]
        if sources:
            drafts.append(
                QuestionDraft(
                    kind="PLACEHOLDER",
                    sources=sources,
                    question="표시된 미정 항목에 적용할 확정 값과 단위를 알려 주시겠습니까?",
                    pre_reply_note="미정: 고객 회신 전까지 해당 항목은 적합·부적합 판정에서 제외합니다.",
                )
            )

    eligible = [r for r in requirements if _eligible(r, clauses)]
    seen: set[tuple[tuple[str, str], ...]] = set()
    for left, right in combinations(eligible, 2):
        if (
            left.clause_no == right.clause_no
            or left.equipment_family != right.equipment_family
            or left.attribute_key != right.attribute_key
            or not _conditions_match(left, right)
        ):
            continue
        a, b = _identifiers(left.equipment), _identifiers(right.equipment)
        if a and b and a.isdisjoint(b):
            continue
        if (
            left.operator == right.operator
            and _same_quantity(
                _quantity(left.value or "", left.unit), _quantity(right.value or "", right.unit)
            )
            and _norm(left.tolerance) == _norm(right.tolerance)
        ):
            continue
        sources = [QuestionSource(clause_no=r.clause_no, quote=r.quote) for r in (left, right)]
        key = tuple(sorted((s.clause_no, s.quote) for s in sources))
        if key in seen:
            continue
        seen.add(key)
        drafts.append(
            QuestionDraft(
                kind="INCONSISTENCY",
                sources=sources,
                question=f"{left.clause_no}와 {right.clause_no}의 {left.attribute_key} 요구가 다릅니다. "
                "같은 장비에 적용되는지, 적용할 값·형식과 우선 조항을 확인해 주시겠습니까?",
                pre_reply_note="불일치 후보: 장비 동일성·조건·단위와 우선 조항을 확인하기 전까지 "
                "임의로 값을 선택하거나 부적합으로 확정하지 않습니다.",
            )
        )

    for clause in tree.clauses:
        local = [s for s in _sources(clause) if ALTERNATIVE.search(s.quote)]
        if not local:
            continue
        context = [s for c in _context(clause, clauses) for s in _sources(c)]
        procedure = [s for s in context if _confirmed_instruction(s, PROCEDURE)]
        materials = [s for s in context if _confirmed_instruction(s, MATERIALS)]
        missing = []
        if not procedure:
            missing.append("승인 요청 방법·담당자·시점")
        if not materials:
            missing.append("제출 자료 목록")
        if missing:
            evidence = list(
                dict.fromkeys((s.clause_no, s.quote) for s in [*local, *procedure, *materials])
            )
            drafts.append(
                QuestionDraft(
                    kind="APPROVAL_PROCESS",
                    sources=[QuestionSource(clause_no=no, quote=quote) for no, quote in evidence],
                    question="대체품 검토를 위한 " + " 및 ".join(missing) + "을 알려 주시겠습니까?",
                    pre_reply_note="대체 허용은 승인 완료가 아닙니다. 회신 및 고객 승인 전까지 "
                    "승인 여부는 미정으로 유지합니다. 참조 문서의 누락 여부도 확인합니다.",
                )
            )
    return drafts
