"""조항 트리에서 요구를 뽑는다(KAN-185, 기획서 4.2·5.2). 모델 호출은 주입한 함수가 한다.

1. `build_inputs`: 절(2.5 등)마다 하위 조항의 **현행 텍스트**만 모은다. 취소선으로 지운 값은 보내지 않는다.
2. 모델: 구조화 출력(`ExtractionOutput`)으로 요구를 낸다. 요구 하나는 속성 하나다(한 조항에 값이 여럿이면
   여럿으로 나눈다). 값에 붙은 조건(2차 전압별, 115℃ 상승 등)은 `conditions`에 함께 둔다.
3. `validate_output`: 요구마다 근거 인용(`quote`)이 그 조항 현행 텍스트 안에 그대로 있는지, 값이 인용 안에
   있는지 확인한다. 어기면 버리고 이유를 남긴다(`rejected`). 모델이 지운 값(예: 2.5 C.1의 50)이나 없는
   조항을 지어내면 여기서 걸린다. 단, 문장형 요구(`operator=TEXT`)의 값이 인용에 없으면 모델이 바꿔 쓴
   말이므로 값만 비우고 요구는 남긴다(`value_dropped`). 수치 요구는 그대로 버린다.

4. 포괄 확인(`coverage`): 조항을 문장으로 나눠, 요구 문장(`:`로 끝나는 머리글 제외)마다 채택한 요구의 인용이
   겹치는지 본다. 빠진 문장이 있으면 그 문장만 모아 한 번 더 부른다(`gap_fill`). 긴 조항(14번 2.5 D는 한
   조항에 11문장)에서 모델이 문장을 묶거나 건너뛰는 것을 실행 운에 맡기지 않기 위해서다.

명령문("Provide …", "Comply with …")도 요구다. 가격·표준 납기는 비교 대상이 아니라 뽑지 않는다.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.engine.rfp.clauses import ClauseTree
from backend.engine.rfp.vocabulary import (
    ATTRIBUTE_KEYS,
    CONDITION_NAMES,
    EQUIPMENT_FAMILIES,
    OTHER,
    AttributeKeyOrOther,
    ConditionNameOrOther,
    FamilyOrOther,
)
from backend.shared.ai import Generation, Usage

# v2: 지정품 제조사(designated_manufacturer)와 원문 대조(명세 v1.3 §5.1·§11). v1 측정치는 계약 문서 표에 있다.
# v3(KAN-185 의미 구조화): 모델이 내부 속성 키·제품 계열·허용 오차·Listed 여부를 같은 호출 안에서 연결한다.
# v4(KAN-185): 여러 장비를 한 조립품·단일 공급 책임으로 납품하라는 요구를 assembly_clause로 표시한다.
# v5(KAN-185): 대체·동등품을 승인 조건부로 허용하는데 절차가 없는 요구를 approval_procedure_missing으로 표시한다.
# v6(KAN-185): operator 판단 기준과 조건 이름 어휘(conditions.name). 단위 수식어는 비교 쪽 코드가 뗀다.
EXTRACTION_VERSION = "rfp-requirements-v6"
Operator = Literal["EQ", "GTE", "LTE", "RANGE", "TEXT"]
StandardSystem = Literal["IEC", "IEEE", "UL", "NEMA", "NONE"]
Necessity = Literal["MANDATORY", "PREFERRED", "CONDITION_BOUND", "UNDETERMINED"]
Kind = Literal["SPEC", "TASK", "DOCUMENT", "COMPLIANCE", "DESIGNATED_PRODUCT"]
RejectReason = Literal["UNKNOWN_CLAUSE", "QUOTE_NOT_IN_CLAUSE", "VALUE_NOT_IN_QUOTE"]
MAX_SECTION_CHARS = 12000
# 승인된 절별 비용 한도: 최초 12회 + 부분별 보완 최대 12회(공급자 전송 재시도 제외).
MAX_SECTION_PARTS = 12
# 한 번에 부를 절 본문 상한(글자). Gemini는 추론 토큰도 출력 상한에 들어가, 14번 2.5(3972자)가 이미 답 5345 + 추론
# 6609토큰을 썼고 2.6(7560자)은 상한 16384를 넘어 응답이 잘렸다. 이보다 긴 절은 첫 단계 조항 묶음으로 나눠 부른다.
# v1 측정 절(2.4 2510자·2.5 3972자)은 이 아래라 나뉘지 않는다.
PART_CHARS = 4000


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClauseInput(Strict):
    clause_no: str
    path: str = Field(
        description="상위 조항 첫머리를 이은 문맥(예: 'Cooling System shall contain …')."
    )
    text: str
    table_rows: list[list[str]] = []


class ExtractionInput(Strict):
    section_no: str
    section_title: str | None
    clauses: list[ClauseInput]


class Condition(Strict):
    # 어휘는 응답 스키마 enum과 validate_output(_outside_lists)이 지킨다. 이 모델은 다른 입력도 받는다.
    name: str = Field(
        description="조건 이름(vocabulary.ConditionName). 없으면 OTHER이고 value에 원문 문구를 그대로 둔다."
    )
    value: str
    unit: str | None = None


class ExtractedRequirement(Strict):
    clause_no: str
    quote: str = Field(description="근거가 되는 조항 현행 텍스트의 연속 구간(그대로 복사).")
    kind: Kind
    equipment: str | None = Field(description="대상 장비(예: dry-type transformer).")
    task: str | None = Field(description="수행 과업이면 과업(예: field testing). 아니면 null.")
    attribute: str = Field(description="속성(예: BIL, impedance, kVA increase with fans).")
    value: str | None = Field(description="값(숫자·문구). 값이 없는 요구는 null.")
    unit: str | None
    operator: Operator
    conditions: list[Condition] = []
    standard_system: StandardSystem
    necessity: Necessity
    designated_model: str | None = Field(
        description="원문에 적힌 지정 모델명 그대로(예: SEL-751A, PowerLogic ION 9000). 없으면 null."
    )
    designated_manufacturer: str | None = Field(
        default=None,
        description="지정 모델의 제조사가 원문에 적혀 있을 때만 그 이름. 추측하지 않는다. 없으면 null.",
    )
    # v3 의미 칸. 어휘·계열은 vocabulary 단일 원본이고, 목록 밖 값은 validate_output이 OTHER로 바꾼다.
    attribute_key: AttributeKeyOrOther = Field(
        default=OTHER,
        description="같은 물리량인 내부 사양 항목 하나. 확실하지 않으면 OTHER(추측하지 않는다).",
    )
    equipment_family: FamilyOrOther | None = Field(
        default=None,
        description="대상 장비의 제품 계열. 모르면 OTHER, 과업·문서 요구는 null.",
    )
    tolerance: str | None = Field(
        default=None, description="원문의 허용 오차 표기 그대로(예: ±10%, +0/-5%). 없으면 null."
    )
    certification_listed: bool = Field(
        default=False, description="원문이 Listed·labeled 등록을 요구할 때만 true."
    )
    assembly_clause: bool = Field(
        default=False,
        description="여러 장비를 한 조립품·단일 공급 책임으로 납품하라는 요구일 때만 true. 나열만이면 false.",
    )
    approval_procedure_missing: bool = Field(
        default=False,
        description=(
            "대체품·동등품·목록 밖 제조사를 승인 조건부로 허용하는데 받은 본문에 승인 절차·제출 자료가 없을 때만 "
            "true. 대체 불허이거나 절차가 적혀 있으면 false."
        ),
    )
    # 코드가 정한다(모델 응답 스키마에 없다): 허용 오차 표기가 인용과 맞지 않거나 빠졌으면 사람이 본다.
    needs_review: bool = False


# 모델이 채우지 않고 코드가 정하는 칸. 응답 스키마와 필드를 맞출 때 뺀다.
CODE_ONLY_FIELDS = frozenset({"needs_review"})


class DesignatedItem(Strict):
    """고객 지정품(명세 v1.3 §5.1). 조항 ID(clause_id)는 저장(A07)이 clause_no로 tft.clauses에서 찾는다."""

    manufacturer: str | None
    model: str
    clause_no: str


def designated_item(requirement: ExtractedRequirement) -> DesignatedItem | None:
    """요구의 지정품. designated_model이 없으면 None(명세: designated_item도 null)."""
    if requirement.designated_model is None:
        return None
    return DesignatedItem(
        manufacturer=requirement.designated_manufacturer,
        model=requirement.designated_model,
        clause_no=requirement.clause_no,
    )


class ExtractionOutput(Strict):
    requirements: list[ExtractedRequirement]


class _Loose(BaseModel):
    """모델에 주는 응답 스키마. Gemini는 additionalProperties를 받지 않아 extra=forbid 없이 같은 모양으로 둔다.

    받은 응답은 엄격한 ExtractionOutput으로 다시 검사한다(필드 일치는 테스트가 확인).
    """


class ConditionSchema(_Loose):
    name: ConditionNameOrOther
    value: str
    unit: str | None = None


class RequirementSchema(_Loose):
    clause_no: str
    quote: str
    kind: Kind
    equipment: str | None
    task: str | None
    attribute: str
    value: str | None
    unit: str | None
    operator: Operator
    conditions: list[ConditionSchema] = []
    standard_system: StandardSystem
    necessity: Necessity
    designated_model: str | None
    designated_manufacturer: str | None = None
    # 목록은 enum 하나로 준다(Gemini 구조화 출력은 enum 둘의 anyOf를 거절한다). 받은 값은 validate_output이 다시
    # 확인한다.
    attribute_key: AttributeKeyOrOther
    equipment_family: FamilyOrOther | None
    tolerance: str | None
    certification_listed: bool
    assembly_clause: bool
    approval_procedure_missing: bool


class ResponseSchema(_Loose):
    requirements: list[RequirementSchema]


class Rejected(Strict):
    clause_no: str
    attribute: str
    reason: RejectReason


@dataclass(frozen=True)
class SectionResult:
    section_no: str
    outcome: Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE"]
    requirements: list[ExtractedRequirement] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    value_dropped: int = 0
    # 지정 모델·제조사가 조항 원문에 없어 비운 요구 수(요구 자체는 남긴다).
    designated_dropped: int = 0
    sentences: int = 0
    covered_first: int = 0
    covered_final: int = 0
    gap_fill_added: int = 0
    # 나눠 부른 부분 수와 그중 실패한 부분 수(나누지 않았으면 1·0).
    parts: int = 1
    parts_failed: int = 0
    usage: Usage = field(default_factory=Usage)
    model_version: str | None = None
    raw_response: str | None = field(default=None, repr=False)


def _space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'").replace("‘", "'")).strip().casefold()


def _clause_text(clause: ClauseInput) -> str:
    rows = [" | ".join(row) for row in clause.table_rows]
    return "\n".join([clause.text, *rows])


def build_inputs(tree: ClauseTree, section_nos: list[str]) -> list[ExtractionInput]:
    """평가·처리할 절마다 모델 입력을 만든다. 현행 텍스트만 쓴다.

    Args:
        tree: 조항 트리.
        section_nos: 처리할 절 번호 목록(예: ["2.4", "2.5"]).

    Returns:
        list[ExtractionInput]: 요청 절마다 입력 하나. 요청 절 순서와 원문 조항 순서를 유지한다.
            본문이 없는 조항은 문맥으로만 쓴다. 나눔과 결과 합산은 extract_section이 맡는다.
            실제 추출 부분 수는 승인된 비용 한도인 12개 이하다.
            상한 12는 감독 제공 최장 개발 절 본문 23908자에 여유를 둔 정책이다.

    Raises:
        ValueError: 절 없음(RFP_SECTION_NOT_FOUND), 개별 조항 길이 초과
            (RFP_SECTION_TOO_LONG), 절별 호출 부분 수 초과(RFP_SECTION_TOO_MANY_PARTS).
    """
    by_no = {c.clause_no: c for c in tree.clauses}
    out: list[ExtractionInput] = []
    for section_no in section_nos:
        head = by_no.get(section_no)
        if head is None:
            raise ValueError("RFP_SECTION_NOT_FOUND")
        members = [c for c in tree.clauses if c.clause_no.startswith(section_no + ".")]
        clauses = []
        for clause in members:
            chain, parent = [], clause.parent_no
            while parent and parent != section_no and parent in by_no:
                chain.append(by_no[parent].text[:80])
                parent = by_no[parent].parent_no
            if not clause.text.strip() and not clause.table_rows:
                continue
            clauses.append(
                ClauseInput(
                    clause_no=clause.clause_no,
                    path=" > ".join(reversed(chain)),
                    text=clause.text,
                    table_rows=clause.table_rows,
                )
            )
        item = ExtractionInput(section_no=section_no, section_title=head.title, clauses=clauses)
        if any(len(_clause_text(c)) > MAX_SECTION_CHARS for c in clauses):
            raise ValueError("RFP_SECTION_TOO_LONG")
        # 실제 최초 호출 수를 호출 전에 검사한다. 보완은 부분별 최대 한 번이다.
        if len(split_input(item)) > MAX_SECTION_PARTS:
            raise ValueError("RFP_SECTION_TOO_MANY_PARTS")
        out.append(item)
    return out


PROMPT = """You extract requirements from one section of an electrical equipment specification (RFP).
Return JSON matching the schema. Rules:
- One requirement = one attribute of one equipment or one task. Split a clause that states several values.
- quote: copy a contiguous span of the clause text exactly (current text only). value must appear in quote.
- For a requirement without a measurable value (operator TEXT), set value to null or to an exact span of quote.
- Keep conditions attached to a value in conditions. name comes from this list only: {condition_names}.
  value is the number or phrase and unit goes in unit (e.g. duration, value "3", unit "s"). If no name fits,
  use "OTHER" with the whole condition phrase in value.
- operator: GTE for a minimum, either stated ("at least", "not less than", "minimum") or implied because a
  higher rating also satisfies it (withstand current, interrupting or breaking capacity, continuous current).
  LTE for a maximum limit ("not more than", "maximum"). EQ for a specified value with no such sense (a system
  voltage, an insulation level or a nominal rating stated as a fixed value). RANGE for a stated range.
- Imperatives ("Provide ...", "Comply with ...") are requirements too.
- necessity: MANDATORY for shall/must/imperative, PREFERRED for should/preferred, CONDITION_BOUND when the
  requirement applies only under a stated condition, UNDETERMINED when the text does not say.
- kind: SPEC (rating/feature), TASK (work to perform), DOCUMENT (submittal), COMPLIANCE (listed/standard),
  DESIGNATED_PRODUCT (named model or manufacturer).
- designated_model: a specific product model the clause names for supply (e.g. "SEL-751A",
  "PowerLogic ION 9000"), copied exactly as written; else null. A list of acceptable manufacturers is not a
  designated model.
- designated_manufacturer: the maker of that model only when its name is written in the clause; else null.
  Do not infer a maker from the model name.
- standard_system from the cited standard family (IEEE/ANSI -> IEEE, UL, NEMA, IEC), else NONE.
- attribute_key: the internal spec key for the SAME physical quantity, from this list only:
  {attribute_keys}. If the text is not clearly the same quantity, use "OTHER". Do not guess from a similar word
  (e.g. a momentary current is not a short-time withstand current; an enclosure paint color is OTHER).
  Keep attribute as the clause wording; attribute_key is only the link.
- equipment_family: the product family of the equipment, from: {families}; "OTHER" if none fits or you are not
  sure; null for a task or document requirement. Instrument transformers (voltage/potential or current
  transformers) feeding meters or relays are not TRANSFORMER: use OTHER.
- tolerance: a tolerance printed in the quote, copied exactly (e.g. "±10%", "+0/-5%"); else null. Keep value
  as the nominal value only. Never compute a range.
- certification_listed: true only when the text requires a listing or label (e.g. "listed", "labeled",
  "bearing the mark"); a test report or "in accordance with" a standard is false.
- assembly_clause: true only when the requirement makes several pieces of equipment be delivered as one
  assembled unit or under a single supplier's responsibility (e.g. "factory-assembled as one unit", "furnished
  by one manufacturer with unit responsibility"). Equipment that is merely listed together is false.
- approval_procedure_missing: true only when the requirement allows a substitute, an equal product, or a
  manufacturer outside a list subject to approval, and the clauses you received give no approval procedure or
  required submittals for it. False when substitution is not allowed, or when the procedure or submittals are
  stated.
- Do not extract prices or standard lead times. Do not invent values. Placeholders like <Insert value> stay as
  value "<Insert value>" so they become customer questions.

Section {section_no} {section_title}
Clauses (JSON):
{clauses}
"""


def prompt(item: ExtractionInput) -> str:
    """모델에 보낼 지시문. 조항은 JSON으로 넣어 지시문과 섞이지 않게 한다."""
    clauses = json.dumps([c.model_dump() for c in item.clauses], ensure_ascii=False, indent=1)
    return PROMPT.format(
        section_no=item.section_no,
        section_title=item.section_title or "",
        clauses=clauses,
        attribute_keys=", ".join(sorted(ATTRIBUTE_KEYS)),
        condition_names=", ".join(sorted(CONDITION_NAMES)),
        families=", ".join(sorted(EQUIPMENT_FAMILIES)),
    )


def _grounded_designation(
    req: ExtractedRequirement, clause_text: str
) -> tuple[ExtractedRequirement, bool]:
    """지정 모델·제조사가 조항 현행 텍스트에 그대로 있을 때만 남긴다. 비웠으면 (요구, True).

    모델이 모델명에서 제조사를 짐작해 쓰거나(SEL → Schweitzer) 없는 모델을 쓰는 것을 막는다. 지운 값(취소선)은
    현행 텍스트에 없으므로 여기서도 걸린다.
    """
    model, maker = req.designated_model, req.designated_manufacturer
    if model is not None and _space(model) not in clause_text:
        model = None
    if model is None or (maker is not None and _space(maker) not in clause_text):
        maker = None
    changed = (model, maker) != (req.designated_model, req.designated_manufacturer)
    if not changed:
        return req, False
    return req.model_copy(
        update={"designated_model": model, "designated_manufacturer": maker}
    ), True


# 원문에 허용 오차가 적혀 있다는 표시(±, +/-, +0/-5, tolerance, plus or minus).
TOLERANCE_MARK = re.compile(
    r"±|\+\s*/\s*-|\+\s*\d[\d.]*\s*/\s*-|tolerance|plus or minus", re.IGNORECASE
)


def _outside_lists(data: dict[str, object]) -> set[int]:
    """목록 밖 attribute_key·equipment_family·조건 이름을 OTHER로 바꾼다(제자리). 바꾼 요구 번호를 돌려준다.

    Args:
        data: 모델 응답 JSON(엄격 검사 전).

    Returns:
        set[int]: 값을 바꾼 요구의 순번.
    """
    changed: set[int] = set()
    requirements = data.get("requirements")
    if not isinstance(requirements, list):
        return changed
    for index, req in enumerate(requirements):
        if not isinstance(req, dict):
            continue
        key = req.get("attribute_key", OTHER)
        if key != OTHER and key not in ATTRIBUTE_KEYS:
            req["attribute_key"] = OTHER
            changed.add(index)
        family = req.get("equipment_family")
        if family not in (None, OTHER) and family not in EQUIPMENT_FAMILIES:
            req["equipment_family"] = OTHER
            changed.add(index)
        conditions = req.get("conditions")
        for condition in conditions if isinstance(conditions, list) else []:
            if not isinstance(condition, dict):
                continue
            name = condition.get("name")
            if name != OTHER and name not in CONDITION_NAMES:
                condition["name"] = OTHER
                changed.add(index)
    return changed


def _reviewed(req: ExtractedRequirement, distrusted: bool) -> ExtractedRequirement:
    """허용 오차를 원문과 맞추고 사람이 볼지 정한다. 모델이 보낸 needs_review는 쓰지 않는다.

    - tolerance가 인용 안에 그대로 없으면 지우고 검토로 둔다(지어낸 허용 오차).
    - 값이 숫자인데 인용에 허용 오차 표기가 있고 tolerance가 비었으면 검토로 둔다(빠진 허용 오차).
    - 목록 밖 키·계열을 OTHER로 바꿨으면 검토로 둔다.

    Args:
        req: 인용·값 검사를 통과한 요구.
        distrusted: _outside_lists가 바꾼 요구인지.

    Returns:
        ExtractedRequirement: tolerance·needs_review를 정한 요구.
    """
    tolerance = req.tolerance
    review = distrusted
    if tolerance is not None and _space(tolerance) not in _space(req.quote):
        tolerance, review = None, True
    numeric = req.value is not None and re.search(r"\d", req.value) is not None
    if tolerance is None and numeric and TOLERANCE_MARK.search(req.quote):
        review = True
    return req.model_copy(update={"tolerance": tolerance, "needs_review": review})


def validate_output(
    raw: str, item: ExtractionInput
) -> tuple[list[ExtractedRequirement], list[Rejected], int, int]:
    """모델 출력을 검사한다. 근거가 조항 현행 텍스트에 없는 요구는 버린다.

    Args:
        raw: 모델 응답 JSON.
        item: 그 응답을 만든 입력.

    Returns:
        (채택한 요구, 버린 요구와 이유, 값만 비운 문장형 요구 수, 지정 모델·제조사를 비운 요구 수).

    Raises:
        ValueError: JSON·스키마 위반(RFP_INVALID_OUTPUT).
    """
    try:
        data = json.loads(raw)
        distrusted = _outside_lists(data)
        output = ExtractionOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError, AttributeError):
        raise ValueError("RFP_INVALID_OUTPUT") from None
    texts = {c.clause_no: _space(_clause_text(c)) for c in item.clauses}
    kept: list[ExtractedRequirement] = []
    rejected: list[Rejected] = []
    dropped = 0
    undesignated = 0
    for index, req in enumerate(output.requirements):
        reason: RejectReason | None = None
        if req.clause_no not in texts:
            reason = "UNKNOWN_CLAUSE"
        elif _space(req.quote) not in texts[req.clause_no]:
            reason = "QUOTE_NOT_IN_CLAUSE"
        elif req.value is not None and _space(req.value) not in _space(req.quote):
            if req.operator == "TEXT":
                req = req.model_copy(update={"value": None})
                dropped += 1
            else:
                reason = "VALUE_NOT_IN_QUOTE"
        if reason is None:
            req, cleared = _grounded_designation(req, texts[req.clause_no])
            undesignated += cleared
            kept.append(_reviewed(req, index in distrusted))
        else:
            rejected.append(
                Rejected(clause_no=req.clause_no, attribute=req.attribute, reason=reason)
            )
    return kept, rejected, dropped, undesignated


def _sum_usage(a: Usage, b: Usage) -> Usage:
    def add(x: int | None, y: int | None) -> int | None:
        return None if x is None or y is None else x + y

    return Usage(
        add(a.input_tokens, b.input_tokens),
        add(a.output_tokens, b.output_tokens),
        add(a.thought_tokens, b.thought_tokens),
        add(a.cached_input_tokens, b.cached_input_tokens),
        add(a.total_tokens, b.total_tokens),
    )


SENTENCE = re.compile(r"(?<=[.;])\s+(?=[A-Z(<0-9])")


def requirement_sentences(item: ExtractionInput) -> list[tuple[str, str]]:
    """요구 문장 후보(조항 번호, 문장). `:`로 끝나는 머리글과 빈 문장은 뺀다."""
    out: list[tuple[str, str]] = []
    for clause in item.clauses:
        for line in _clause_text(clause).split("\n"):
            for sentence in SENTENCE.split(line.strip()):
                text = sentence.strip()
                if len(text.split()) >= 2 and not text.endswith(":"):
                    out.append((clause.clause_no, text))
    return out


def _covers(sentence: str, quote: str) -> bool:
    a, b = _space(sentence), _space(quote)
    if not a or not b:
        return False
    if b in a or a in b:
        return True
    words = set(re.findall(r"[a-z0-9]+", a))
    return bool(words) and len(words & set(re.findall(r"[a-z0-9]+", b))) * 5 >= len(words) * 3


def coverage(
    item: ExtractionInput, requirements: list[ExtractedRequirement]
) -> tuple[int, list[tuple[str, str]]]:
    """요구 문장 수와, 어떤 요구 인용과도 겹치지 않는 문장 목록."""
    sentences = requirement_sentences(item)
    missing = [
        (no, text)
        for no, text in sentences
        if not any(r.clause_no == no and _covers(text, r.quote) for r in requirements)
    ]
    return len(sentences), missing


def _call(
    item: ExtractionInput, check: ExtractionInput, provider: Callable[[str], Generation]
) -> tuple[Generation, list[ExtractedRequirement], list[Rejected], int, int]:
    response = provider(prompt(item))
    return response, *validate_output(response.text, check)


def split_input(item: ExtractionInput, limit: int = PART_CHARS) -> list[ExtractionInput]:
    """긴 절을 첫 단계 조항(2.6.A, 2.6.B …) 묶음 단위로 순서대로 나눈다.

    목표 길이를 넘는 묶음은 개별 조항 경계로 재묶는다. 조항 텍스트는 자르지 않는다.
    build_inputs의 개별 조항 길이·부분 수 사전 검사를 거친 입력을 전제한다.

    Args:
        item: 절 입력.
        limit: 부분 하나의 목표 본문 길이(글자). 이를 넘는 개별 조항은 단독 부분으로 유지한다.

    Returns:
        list[ExtractionInput]: 부분 입력. 절이 상한 안이면 [item] 그대로다.
    """
    if sum(len(_clause_text(c)) for c in item.clauses) <= limit:
        return [item]
    depth = item.section_no.count(".") + 2
    groups: list[list[ClauseInput]] = []
    for clause in item.clauses:
        key = ".".join(clause.clause_no.split(".")[:depth])
        if groups and ".".join(groups[-1][0].clause_no.split(".")[:depth]) == key:
            groups[-1].append(clause)
        else:
            groups.append([clause])
    expanded: list[list[ClauseInput]] = []
    for group in groups:
        if sum(len(_clause_text(c)) for c in group) <= limit:
            expanded.append(group)
            continue
        batch: list[ClauseInput] = []
        size = 0
        for clause in group:
            clause_size = len(_clause_text(clause))
            if clause_size > MAX_SECTION_CHARS:
                raise ValueError("RFP_SECTION_TOO_LONG")
            if batch and size + clause_size > limit:
                expanded.append(batch)
                batch, size = [], 0
            batch.append(clause)
            size += clause_size
        if batch:
            expanded.append(batch)
    parts: list[list[ClauseInput]] = [[]]
    for group in expanded:
        size = sum(len(_clause_text(c)) for c in parts[-1] + group)
        if parts[-1] and size > limit:
            parts.append([])
        parts[-1].extend(group)
    return [item.model_copy(update={"clauses": clauses}) for clauses in parts]


def extract_section(item: ExtractionInput, provider: Callable[[str], Generation]) -> SectionResult:
    """절 하나를 뽑는다. 긴 절은 `split_input`으로 나눠 부르고 결과를 합친다.

    Args:
        item: 절 입력.
        provider: 지시문을 받아 공통 Generation을 돌려주는 호출.

    Returns:
        SectionResult: 모든 부분이 VALID일 때만 VALID다. 실패한 부분이 있으면 그 결과 코드이고, 요구는 성공한
            부분 것만 담기며 parts_failed로 드러난다(성공한 부분을 버리지 않는다). 나눴으면 raw_response는 없다.
    """
    pieces = split_input(item)
    if len(pieces) == 1:
        return _extract_part(item, provider)
    results = [_extract_part(piece, provider) for piece in pieces]
    failed = [r for r in results if r.outcome != "VALID"]
    # 실패한 부분은 공급자 응답이 없어 사용량을 알 수 없으므로 합계에서 빠진다.
    usage = Usage(0, 0, 0, 0, 0)
    for r in results:
        if r.outcome == "VALID":
            usage = _sum_usage(usage, r.usage)
    valid = [r for r in results if r.outcome == "VALID"]
    return SectionResult(
        section_no=item.section_no,
        outcome=failed[0].outcome if failed else "VALID",
        requirements=[req for r in valid for req in r.requirements],
        rejected=[rej for r in valid for rej in r.rejected],
        value_dropped=sum(r.value_dropped for r in valid),
        designated_dropped=sum(r.designated_dropped for r in valid),
        # 분모는 실패한 부분 문장까지 센다(부분이 절을 나눠 가지므로 절 전체 문장 수와 같다).
        sentences=len(requirement_sentences(item)),
        covered_first=sum(r.covered_first for r in valid),
        covered_final=sum(r.covered_final for r in valid),
        gap_fill_added=sum(r.gap_fill_added for r in valid),
        parts=len(results),
        parts_failed=len(failed),
        usage=usage if valid else Usage(),
        model_version=valid[0].model_version if valid else None,
    )


def _extract_part(item: ExtractionInput, provider: Callable[[str], Generation]) -> SectionResult:
    """절(또는 그 부분) 하나를 부르고, 빠진 요구 문장이 있으면 그 문장만 한 번 더 부른다. 그 밖의 재시도는 없다.

    Args:
        item: 절 입력.
        provider: 지시문을 받아 공통 Generation을 돌려주는 호출(실제는 Gemini 구조화 출력).

    Returns:
        SectionResult: 채택·버린 요구, 문장 포괄, 사용량(두 호출 합). 실패·스키마 위반은 outcome으로 구분한다.
    """
    try:
        response, kept, rejected, dropped, undesignated = _call(item, item, provider)
    except ValueError:
        return SectionResult(section_no=item.section_no, outcome="SCHEMA_VIOLATION")
    except Exception:
        # 공급자 오류 원문은 남기지 않는다.
        return SectionResult(section_no=item.section_no, outcome="TRANSPORT_FAILURE")
    total, missing = coverage(item, kept)
    first = total - len(missing)
    usage = response.usage
    added = 0
    if missing:
        texts: dict[str, list[str]] = {}
        for no, text in missing:
            texts.setdefault(no, []).append(text)
        gap = item.model_copy(
            update={
                "clauses": [
                    ClauseInput(
                        clause_no=c.clause_no, path=c.path, text=" ".join(texts[c.clause_no])
                    )
                    for c in item.clauses
                    if c.clause_no in texts
                ]
            }
        )
        try:
            extra_response, more, more_rejected, more_dropped, more_undesignated = _call(
                gap, item, provider
            )
        except Exception:
            # 보완 호출 실패는 1차 결과를 지우지 않는다. 빠진 문장 수로 드러난다.
            more, more_rejected, more_dropped, more_undesignated, extra_response = (
                [],
                [],
                0,
                0,
                None,
            )
        kept, rejected, dropped = kept + more, rejected + more_rejected, dropped + more_dropped
        undesignated += more_undesignated
        added = len(more)
        if extra_response is not None:
            usage = _sum_usage(usage, extra_response.usage)
    final = total - len(coverage(item, kept)[1])
    return SectionResult(
        section_no=item.section_no,
        outcome="VALID",
        requirements=kept,
        rejected=rejected,
        value_dropped=dropped,
        designated_dropped=undesignated,
        sentences=total,
        covered_first=first,
        covered_final=final,
        gap_fill_added=added,
        usage=usage,
        model_version=response.model_version,
        raw_response=response.text,
    )
