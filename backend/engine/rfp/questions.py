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
from backend.engine.rfp.vocabulary import is_placeholder

QuestionKind = Literal["PLACEHOLDER", "INCONSISTENCY", "APPROVAL_PROCESS"]
EQUIPMENT_ID = re.compile(
    r"\bunit\b\s*#?\s*([a-z]|\d+[a-z]?|[a-z]+-\d+[a-z0-9-]*)\b|"
    r"\btag\b\s*#?\s*([a-z0-9-]+)\b|"
    r"\b(?:equipment|transformer|switchgear)\s*(?:no\.?|id|#)\s*([a-z0-9-]+)\b",
    re.I,
)
# 이 질문 함수에서만 쓰는 좁은 환산 목록. RMS 등 수식어는 버리지 않는다.
UNITS: dict[str, tuple[str, str]] = {
    "V": ("V", "1"),
    "v": ("V", "1"),
    "kV": ("V", "1000"),
    "kv": ("V", "1000"),
    "A": ("A", "1"),
    "a": ("A", "1"),
    "kA": ("A", "1000"),
    "ka": ("A", "1000"),
    "VA": ("VA", "1"),
    "va": ("VA", "1"),
    "kVA": ("VA", "1000"),
    "kva": ("VA", "1000"),
    "MVA": ("VA", "1000000"),
    "W": ("W", "1"),
    "w": ("W", "1"),
    "kW": ("W", "1000"),
    "kw": ("W", "1000"),
    "MW": ("W", "1000000"),
    "s": ("s", "1"),
    "ms": ("s", "0.001"),
    "Hz": ("Hz", "1"),
    "hz": ("Hz", "1"),
    "kHz": ("Hz", "1000"),
    "khz": ("Hz", "1000"),
    "%": ("%", "1"),
    "percent": ("%", "1"),
    "°C": ("°C", "1"),
    "°c": ("°C", "1"),
    "degC": ("°C", "1"),
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


def _norm(text: str | None, *, preserve_case: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    if not preserve_case:
        normalized = normalized.casefold()
    return " ".join(normalized.split()).translate(str.maketrans("–—‑‐−", "-----"))


def _quantity(value: str, unit: str | None) -> tuple[str, str]:
    text = _norm(value, preserve_case=True)
    # 정당한 천 단위 쉼표만 제거한다. 쉼표 소수·범위·복합 수치는 추측하지 않는다.
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    match = re.fullmatch(rf"({NUMBER})\s*([a-zA-Z%°]*)", text)
    given_unit = _norm(unit, preserve_case=True)
    if match:
        suffix = match[2]
        if not suffix or not given_unit or suffix == given_unit:
            effective = suffix or given_unit
            if effective and effective not in UNITS:
                return text, given_unit
            dimension, scale = UNITS.get(effective, ("", "1"))
            try:
                return str(Decimal(match[1]) * Decimal(scale)), dimension
            except DecimalException:
                # 표현/환산할 수 없는 수치는 원래 문자열로 보수적으로 비교한다.
                return text, given_unit
    return (text if re.search(r"\d", text) else _norm(value)), given_unit


def _same_quantity(left: tuple[str, str], right: tuple[str, str]) -> bool:
    if left[1] != right[1]:
        return False
    if left[1] and left[1] not in {dimension for dimension, _ in UNITS.values()}:
        return left[0] == right[0]
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


def _value_frame(req: ExtractedRequirement) -> str | None:
    """인용의 값 한 곳과 인접 단위만 가린다. 같은 속성 키의 다른 주제를 합치지 않는다."""
    value, quote = _norm(req.value), _norm(req.quote)
    if not value:
        return None
    start = r"(?<!\w)(?<!\d[.,])" if value[0] in "+-.0123456789" else r"(?<!\w)"
    end = r"(?!\w)(?![.,]\d)" if value[-1] in ".0123456789" else r"(?!\w)"
    matches = list(re.finditer(start + re.escape(value) + end, quote))
    if len(matches) != 1:
        return None
    match = matches[0]
    tail = quote[match.end() :]
    if req.unit:
        tail = re.sub(r"^\s*" + re.escape(_norm(req.unit)) + r"(?!\w)", "", tail, count=1)
    return _norm(quote[: match.start()] + "<value>" + tail)


def _same_item(
    left: ExtractedRequirement, right: ExtractedRequirement, clauses: dict[str, Clause]
) -> bool:
    """조항 경로·장비·항목·나머지 인용이 동일성을 뒷받침할 때만 비교한다."""

    def parent(req: ExtractedRequirement) -> str:
        clause = clauses[req.clause_no]
        candidate = clause.parent_no or req.clause_no.rpartition(".")[0]
        return candidate if candidate in clauses and clauses[candidate].depth >= 2 else ""

    if not parent(left) or parent(left) != parent(right):
        return False
    equipment_a, equipment_b = _norm(left.equipment), _norm(right.equipment)
    ids_a, ids_b = _identifiers(left.equipment), _identifiers(right.equipment)
    if not equipment_a or not equipment_b:
        return False
    if ids_a or ids_b:
        if not ids_a or ids_a != ids_b:
            return False
        base_a = _norm(EQUIPMENT_ID.sub("", equipment_a))
        base_b = _norm(EQUIPMENT_ID.sub("", equipment_b))
        if equipment_a != equipment_b and (not base_a or base_a != base_b):
            return False
    elif equipment_a != equipment_b:
        return False
    if not _norm(left.attribute) or _norm(left.attribute) != _norm(right.attribute):
        return False
    frame = _value_frame(left)
    return frame is not None and frame == _value_frame(right)


def _eligible(req: ExtractedRequirement, clauses: dict[str, Clause]) -> bool:
    clause = clauses.get(req.clause_no)
    if (
        clause is None
        or req.kind != "SPEC"
        or req.equipment_family in {None, "OTHER"}
        or req.attribute_key == "OTHER"
        or not req.value
        or not req.quote.strip()
        or is_placeholder(req.quote)
    ):
        return False
    # 이전 추출 결과를 받아도 현행 본문에 없는 값은 사용하지 않는다.
    current = any(req.quote in text for text in _pieces(clause))
    value = _norm(req.value)
    if not value:
        return False
    # 쉼표/소수점으로 연결된 숫자 일부를 현행 값으로 사용하지 않는다.
    start = r"(?<!\w)(?<!\d[.,])" if value[0] in "+-.0123456789" else r"(?<!\w)"
    end = r"(?!\w)(?![.,]\d)" if value[-1] in ".0123456789" else r"(?!\w)"
    value_present = re.search(start + re.escape(value) + end, _norm(req.quote))
    return current and value_present is not None


def generate_question_drafts(
    tree: ClauseTree, requirements: Sequence[ExtractedRequirement]
) -> list[QuestionDraft]:
    """현행 원문과 요구에서 결정론적으로 고객 질문 초안을 만든다.

    Args:
        tree: 삭제 값이 본문과 분리된 조항 트리.
        requirements: 현행 조항에서 추출한 요구. 없는 조항·오래된 인용은 제외한다.

    Returns:
        list[QuestionDraft]: 자리표시자, 조항 간 불일치 후보, 승인 절차 질문 순서의 초안.
            미정 항목은 회신 전 판정 제외 메모를 포함한다. 자동 판정·승인은 하지 않는다.
    """
    clauses = {c.clause_no: c for c in tree.clauses}
    drafts: list[QuestionDraft] = []
    for clause in tree.clauses:
        sources = [s for s in _sources(clause) if is_placeholder(s.quote)]
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
            or not _same_item(left, right, clauses)
        ):
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
        attribute = (
            left.attribute
            if left.attribute and not re.fullmatch(r"[a-z]+(?:_[a-z0-9]+)+", left.attribute)
            else "해당 항목"
        )
        drafts.append(
            QuestionDraft(
                kind="INCONSISTENCY",
                sources=sources,
                question=f"{left.clause_no}와 {right.clause_no}의 {attribute} 요구가 다릅니다. "
                "같은 장비에 적용되는지, 적용할 값·형식과 우선 조항을 확인해 주시겠습니까?",
                pre_reply_note="불일치 후보: 장비 동일성·조건·단위와 우선 조항을 확인하기 전까지 "
                "임의로 값을 선택하거나 부적합으로 확정하지 않습니다.",
            )
        )

    approval_seen: set[tuple[str, str]] = set()
    for requirement in requirements:
        approval_clause = clauses.get(requirement.clause_no)
        source = (requirement.clause_no, requirement.quote)
        if (
            not requirement.approval_procedure_missing
            or approval_clause is None
            or not requirement.quote.strip()
            or not any(requirement.quote in text for text in _pieces(approval_clause))
            or source in approval_seen
        ):
            continue
        approval_seen.add(source)
        drafts.append(
            QuestionDraft(
                kind="APPROVAL_PROCESS",
                sources=[QuestionSource(clause_no=source[0], quote=source[1])],
                question="대체품 검토를 위한 승인 요청 방법·담당자·시점과 제출 자료 목록을 "
                "알려 주시겠습니까?",
                pre_reply_note="대체 허용은 승인 완료가 아닙니다. 회신 및 고객 승인 전까지 "
                "승인 여부는 미정으로 유지합니다.",
            )
        )
    return drafts
