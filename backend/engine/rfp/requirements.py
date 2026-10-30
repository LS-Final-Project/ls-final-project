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
from backend.shared.ai import Generation, Usage

# v2: 지정품 제조사(designated_manufacturer)와 원문 대조(명세 v1.3 §5.1·§11). v1 측정치는 계약 문서 표에 있다.
EXTRACTION_VERSION = "rfp-requirements-v2"
Operator = Literal["EQ", "GTE", "LTE", "RANGE", "TEXT"]
StandardSystem = Literal["IEC", "IEEE", "UL", "NEMA", "NONE"]
Necessity = Literal["MANDATORY", "PREFERRED", "CONDITION_BOUND", "UNDETERMINED"]
Kind = Literal["SPEC", "TASK", "DOCUMENT", "COMPLIANCE", "DESIGNATED_PRODUCT"]
RejectReason = Literal["UNKNOWN_CLAUSE", "QUOTE_NOT_IN_CLAUSE", "VALUE_NOT_IN_QUOTE"]
MAX_SECTION_CHARS = 12000


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
    name: str = Field(description="조건 이름(예: secondary voltage, temperature rise, ambient).")
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
    name: str
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
        section_nos: 절 번호 목록(예: ["2.4", "2.5"]). 미리 고정한 평가 절이다.

    Returns:
        list[ExtractionInput]: 절마다 하나. 본문이 없는 조항은 문맥으로만 쓴다.

    Raises:
        ValueError: 절이 없거나 너무 길면(RFP_SECTION_NOT_FOUND·RFP_SECTION_TOO_LONG).
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
        if sum(len(_clause_text(c)) for c in clauses) > MAX_SECTION_CHARS:
            raise ValueError("RFP_SECTION_TOO_LONG")
        out.append(item)
    return out


PROMPT = """You extract requirements from one section of an electrical equipment specification (RFP).
Return JSON matching the schema. Rules:
- One requirement = one attribute of one equipment or one task. Split a clause that states several values.
- quote: copy a contiguous span of the clause text exactly (current text only). value must appear in quote.
- For a requirement without a measurable value (operator TEXT), set value to null or to an exact span of quote.
- Keep conditions attached to a value (e.g. secondary voltage, temperature rise, ambient) in conditions.
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
        section_no=item.section_no, section_title=item.section_title or "", clauses=clauses
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
        output = ExtractionOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, TypeError):
        raise ValueError("RFP_INVALID_OUTPUT") from None
    texts = {c.clause_no: _space(_clause_text(c)) for c in item.clauses}
    kept: list[ExtractedRequirement] = []
    rejected: list[Rejected] = []
    dropped = 0
    undesignated = 0
    for req in output.requirements:
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
            kept.append(req)
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


def extract_section(item: ExtractionInput, provider: Callable[[str], Generation]) -> SectionResult:
    """절 하나를 부르고, 빠진 요구 문장이 있으면 그 문장만 한 번 더 부른다. 그 밖의 재시도는 없다.

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
