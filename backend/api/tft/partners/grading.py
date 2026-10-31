"""가상 협력사 후보 × 요구 등급 계산(KAN-187). DB나 요청 없이 테스트한다.

프로필의 사실·근거만 읽는다. notes·display_name 같은 자유 문자열은 판정에 쓰지 않아서 그 안의
명령문이 결과를 바꾸지 못한다(기획서 5.4). 유사도 점수는 만들지 않는다.
"""

import re
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, NamedTuple
from uuid import UUID

from pydantic import BaseModel

from backend.api.tft.partners.schemas import Condition, PartnerProfile, SpecFact
from backend.engine.rfp.vocabulary import (
    CANONICAL_FORMS,
    OTHER,
    is_canonical,
    is_placeholder,
    read_canonical,
)

Grade = Literal["MANDATORY_CONFIRMED", "REVIEW", "REFERENCE_ONLY", "EXCLUDED"]
Verdict = Literal["passed", "failed", "unknown"]
# (확인 항목, 결과, 한국어 문장)
Check = tuple[str, Verdict, str]
# EXCLUDED를 뺀 등급의 낮은 순서. 후보 요약은 요구별 등급 중 가장 낮은 단계다.
RANK: tuple[Grade, ...] = ("REFERENCE_ONLY", "REVIEW", "MANDATORY_CONFIRMED")
SYSTEMS = ("IEC", "IEEE", "UL", "NEMA")
# 같은 장비를 서로 다른 제품군으로 규정해 한쪽 등록이 다른 쪽 미충족을 뜻하는 규격 쌍(_norm 표기).
# UL 891은 배전반(Switchboard), UL 1558은 저압 차단기 배전반(Switchgear)이다.
RIVAL_STANDARDS = (frozenset({"ul 891", "ul 1558"}),)
# 지속시간 추정을 쓰는 상한(초). 공개 근거의 단열 가정 범위이고 이보다 길면 조건 차이로 둔다.
MAX_ESTIMATE_SECONDS = Decimal(3)
# 값의 양을 바꾸지 않는 단위 수식어. 'r.m.s.'는 점을 뗀 뒤 비교하므로 r.m.s로 남는다.
UNIT_QUALIFIERS = frozenset({"sym", "symmetrical", "rms", "r.m.s", "ac"})
# 긴 초 표기는 대소문자를 가리지 않는다(Sec, SEC, Seconds). 한 글자 s는 여기 없다: 대문자 S는 지멘스다.
SECONDS = frozenset({"sec", "secs", "second", "seconds"})
# 요구에 비교할 숫자나 단위가 없을 때의 말. 단위가 다른 것과 구분한다.
NO_NUMBER = "요구 값에 숫자·단위 없음"
SHORTER_SHORTFALL = "짧은 시간 값의 추정이 미달이나 실제 정격이 더 높을 수 있어 확인 필요"
INTEGRATOR_LABELS = {"assembly": "조립", "test": "시험", "integration": "연동", "warranty": "보증"}


class Requirement(BaseModel):
    """명세 §4.4 spec 이름 그대로인 요구 입력. attribute 어휘는 공유 형식 §5다."""

    requirement_id: UUID
    equipment: str
    task: str | None = None
    attribute: str
    value: str
    unit: str | None = None
    operator: Literal["EQ", "GTE", "LTE", "RANGE", "TEXT"]
    conditions: list[Condition] = []
    standard_system: Literal["IEC", "IEEE", "UL", "NEMA", "NONE"]
    necessity: Literal["MANDATORY", "PREFERRED", "CONDITION_BOUND", "UNDETERMINED"]
    designated_model: str | None = None
    clause_no: str | None = None
    # 원문 속성 표현. 사유 문장에 쓴다. attribute는 비교 어휘 키다.
    attribute_label: str | None = None
    # 문장형 값의 비교용 표기(추출 canonical_value). 있으면 value 대신 이것으로 견준다.
    canonical_value: str | None = None


class RfpContext(BaseModel):
    manufacturer_list: list[str] | None
    off_list_permission: Literal["ALLOWED", "FORBIDDEN", "UNSTATED"]
    supply_region: str | None
    requires_integrator: bool


class MandatoryCheck(BaseModel):
    passed: list[str] = []
    failed: list[str] = []
    unknown: list[str] = []


class RequirementGrade(BaseModel):
    requirement_id: UUID
    grade: Grade
    reasons: list[str]
    exclusion_reasons: list[str]
    mandatory_check: MandatoryCheck


class DesignatedRequirement(BaseModel):
    """고객 지정 모델 요구. 협력사 후보 채점에 넣지 않고 지정품 조달로 따로 보인다(기획서 V_1 ⑧)."""

    requirement_id: UUID
    designated_model: str
    clause_no: str | None
    note: str = "고객 지정 조달"


class CandidateSummary(BaseModel):
    candidate_code: str
    on_customer_manufacturer_list: bool | None
    overall_grade: Grade
    conditions_to_confirm: list[str]
    designated_requirements: list[DesignatedRequirement] = []


def grade_requirement(
    profile: PartnerProfile, requirement: Requirement, context: RfpContext, reference_date: date
) -> RequirementGrade:
    """후보 하나가 요구 하나에 대해 받는 등급과 사유를 계산한다.

    필수(MANDATORY)·조건부(CONDITION_BOUND) 요구는 비교 결과를, 성격 미정(UNDETERMINED)은
    unknown을 mandatory_check에 넣는다. 선호(PREFERRED)는 사유에만 남겨 미달이어도 제외하지 않는다.
    공급 지역·통합 책임·목록 외 제조사 금지는 후보 단위 제약이라 모든 요구 행에 함께 들어간다.

    Args:
        profile: 가상 협력사 프로필.
        requirement: 보완 대상 요구.
        context: RFP의 제조사 목록·허용 조건·공급 지역·조립품 공급 여부.
        reference_date: 인증 유효기한을 견줄 기준일(RFP 제출 기한 등).

    Returns:
        RequirementGrade: 미달 근거가 하나라도 있으면 EXCLUDED, 관련 근거가 없으면 REFERENCE_ONLY,
            미확인이 남거나 요구 자체가 충족이 아니면 REVIEW, 그 밖에는 MANDATORY_CONFIRMED.
    """
    codes = {p.product_code for p in profile.products}
    models = {p.model for p in profile.products}
    if requirement.attribute == "certification":
        item = _certification_check(requirement, profile, models, reference_date)
    else:
        item = _spec_check(requirement, profile, codes, models)
    checks = _constraints(profile, context)
    reasons: list[str] = []
    if requirement.necessity in ("MANDATORY", "CONDITION_BOUND"):
        checks.append(item)
        if requirement.necessity == "CONDITION_BOUND":
            stated = _conditions_text(requirement.conditions) or "조건 미기재"
            reasons.append(f"조건부 요구라 '{stated}' 조건에서 필수로 보고 확인한다.")
    elif requirement.necessity == "UNDETERMINED":
        checks.append((item[0], "unknown", f"{item[2]} 요구 성격이 정해지지 않아 확인 필요."))
    else:
        reasons.append(f"선호 요구라 제외 판단에 쓰지 않음: {item[2]}")
    check = MandatoryCheck()
    for name, verdict, _ in checks:
        getattr(check, verdict).append(name)
    reasons += [sentence for _, verdict, sentence in checks if verdict != "failed"]
    exclusion = [sentence for _, verdict, sentence in checks if verdict == "failed"]
    evidence = any(f.product_code in codes for f in profile.spec_facts) or profile.certifications
    grade: Grade
    if exclusion:
        grade = "EXCLUDED"
    elif not evidence:
        grade = "REFERENCE_ONLY"
        reasons.append("제안 모델의 사양·인증 근거가 없어 확인용 후보로만 둔다.")
    elif check.unknown or item[1] != "passed":
        # 후보 단위 제약만 통과한 선호 요구가 확정으로 올라가지 않게, 요구 자체가 충족일 때만 확정한다.
        grade = "REVIEW"
    else:
        grade = "MANDATORY_CONFIRMED"
    return RequirementGrade(
        requirement_id=requirement.requirement_id,
        grade=grade,
        reasons=reasons,
        exclusion_reasons=exclusion,
        mandatory_check=check,
    )


def overall_grade(grades: Iterable[Grade]) -> Grade:
    """요구별 등급을 후보 등급으로 모은다(처리 시 요약과 저장 행 읽기가 같은 규칙을 쓴다).

    Args:
        grades: 채점한 요구별 등급.

    Returns:
        Grade: 하나라도 EXCLUDED면 EXCLUDED, 아니면 가장 낮은 단계, 없으면 REVIEW.
    """
    found = set(grades)
    if not found:
        return "REVIEW"
    if "EXCLUDED" in found:
        return "EXCLUDED"
    return min(found, key=RANK.index)


def grade_candidate(
    profile: PartnerProfile,
    requirements: Sequence[Requirement],
    context: RfpContext,
    reference_date: date,
) -> tuple[CandidateSummary, list[RequirementGrade]]:
    """후보 하나를 보완 대상 요구 전체에 대해 등급 매기고 후보 요약을 만든다.

    고객 지정 모델(designated_model)이 있는 요구는 자사·협력사 DB에 없는 경우가 많아 협력사 후보 추천과 섞지 않는다
    (기획서 V_1 ⑧). 그 요구는 채점하지 않고 요약의 designated_requirements에만 남긴다. 남은 요구가 없으면 채점할
    대상이 없으므로 REVIEW와 그 사유를 낸다.

    Args:
        profile: 가상 협력사 프로필.
        requirements: 보완 대상 요구 목록. 비어 있으면 안 된다.
        context: RFP 맥락.
        reference_date: 인증 유효기한 기준일.

    Returns:
        tuple[CandidateSummary, list[RequirementGrade]]: 후보 요약(채점한 요구별 등급에 EXCLUDED가 있으면
            EXCLUDED, 아니면 가장 낮은 단계, 채점한 요구가 없으면 REVIEW)과 지정 모델 요구를 뺀 요구별 등급.
    """
    if not requirements:
        raise ValueError("At least one requirement is needed to grade a candidate.")
    designated = [
        DesignatedRequirement(
            requirement_id=r.requirement_id,
            designated_model=r.designated_model,
            clause_no=r.clause_no,
        )
        for r in requirements
        if r.designated_model
    ]
    graded = [r for r in requirements if not r.designated_model]
    grades = [grade_requirement(profile, r, context, reference_date) for r in graded]
    on_list, conditions = _manufacturer(profile, context)
    if not grades:
        conditions = [*conditions, "모든 요구가 고객 지정 모델이라 협력사 채점 대상이 없음."]
    summary = CandidateSummary(
        candidate_code=profile.candidate_code,
        on_customer_manufacturer_list=on_list,
        overall_grade=overall_grade(g.grade for g in grades),
        conditions_to_confirm=conditions,
        designated_requirements=designated,
    )
    return summary, grades


def _norm(text: str | None) -> str:
    """공백을 하나로 줄이고 대소문자를 무시한 비교용 문자열.

    Args:
        text: 원래 문자열.

    Returns:
        str: 정규화한 문자열. None은 빈 문자열.
    """
    return " ".join((text or "").split()).casefold()


def _number(text: str) -> Decimal | None:
    """문자열 값을 숫자로 읽는다.

    Args:
        text: 값 문자열.

    Returns:
        Decimal | None: 숫자가 아니거나 NaN·Infinity면 None.
    """
    try:
        number = Decimal(text.strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _system(standard: str) -> str:
    """규격명 첫 단어로 규격 체계를 정한다.

    Args:
        standard: 'UL 1558'·'IEC 62271-200' 같은 규격명.

    Returns:
        str: IEC·IEEE·UL·NEMA 중 하나, 알 수 없으면 NONE.
    """
    head = standard.split()[0].upper() if standard.split() else ""
    return head if head in SYSTEMS else "NONE"


def _with_unit(value: str, unit: str | None) -> str:
    """값과 단위를 사유 문장용으로 붙인다.

    Args:
        value: 값.
        unit: 단위.

    Returns:
        str: '38 kA'처럼 붙인 문자열.
    """
    return f"{value} {unit}" if unit else value


def _conditions_text(conditions: Sequence[Condition]) -> str:
    """조건 목록을 사유 문장용으로 바꾼다.

    Args:
        conditions: 조건 목록.

    Returns:
        str: 'duration 2 s, ...'. 비어 있으면 빈 문자열.
    """
    return ", ".join(f"{c.name} {_with_unit(c.value, c.unit)}" for c in conditions)


def _head(requirement: Requirement) -> str:
    """사유 문장 머리: 원문 조항·속성·요구값.

    Args:
        requirement: 요구.

    Returns:
        str: '원문 2.4 short-time withstand current 요구 38 kA 이상'.
    """
    wanted = requirement.canonical_value or _with_unit(requirement.value, requirement.unit)
    where = f"원문 {requirement.clause_no} " if requirement.clause_no else ""
    name = requirement.attribute_label or requirement.attribute
    if not wanted:
        return f"{where}{name} 요구"
    # 숫자가 없는 값에는 이상·이하를 붙이지 않는다.
    operator = requirement.operator
    if operator in ("GTE", "LTE") and not requirement.canonical_value:
        operator = operator if _number(requirement.value) is not None else "TEXT"
    wanted = {"GTE": f"{wanted} 이상", "LTE": f"{wanted} 이하", "TEXT": f"'{wanted}'"}.get(
        operator, wanted
    )
    return f"{where}{name} 요구 {wanted}"


def shortfall_word(requirement: Requirement) -> str:
    """요구를 못 맞춘 방향을 말로 쓴다. 같아야 하는 값이 더 크면 미달이 아니다.

    Args:
        requirement: 요구.

    Returns:
        str: 판정 말.
    """
    return {"GTE": "미달", "LTE": "초과", "RANGE": "범위 밖"}.get(requirement.operator, "값 다름")


def differing_word(requirement: Requirement) -> str:
    """못 맞췄지만 조건이 달라 확정하지 않을 때의 말.

    Args:
        requirement: 요구.

    Returns:
        str: 판정 말.
    """
    if requirement.operator == "GTE":
        return "값이 미달이나 조건이 달라 확인 필요"
    return "값이 요구와 다르나 조건이 달라 확인 필요"


def _unit_key(unit: str | None) -> str:
    """비교용 단위. 원문 단위는 그대로 두고 비교할 때만 뜻이 같은 표기를 맞춘다.

    양을 바꾸지 않는 수식어(SYM, RMS, AC)를 떼고 초 표기를 s로 맞춘다. peak·crest·asym은 다른 양이라 떼지 않는다.
    값 단위의 대소문자는 크기를 가르므로(mV·MV) 그대로 둔다. 조건 단위(Hz·°C)는 호출자가 _norm으로 함께 접는다.

    Args:
        unit: 원문 단위.

    Returns:
        str: 비교용 단위. 없으면 빈 문자열.
    """
    words = [w for w in re.split(r"[\s,()]+", unit or "") if w]
    kept = [w for w in words if w.casefold().strip(".") not in UNIT_QUALIFIERS]
    key = " ".join(kept)
    return "s" if key.casefold() in SECONDS else key


def _canonical_verdict(attribute: str, wanted: str, value: str) -> tuple[Verdict, str]:
    """비교용 표기로 견준다. 탭은 위·아래 단 수와 간격이 모두 같아야 하고, 보호 기능은 요구가 자사의 부분집합이면 된다.

    Args:
        attribute: 속성 어휘.
        wanted: 요구의 비교용 표기.
        value: 사양 근거의 값.

    Returns:
        tuple[Verdict, str]: 결과와 판정 말.
    """
    got = read_canonical(attribute, value)
    if got is None or not is_canonical(attribute, wanted):
        return "unknown", "값을 비교 표기로 읽을 수 없어 확인 필요"
    if attribute == "tap":
        return ("passed", "충족") if _tap(wanted) == _tap(got) else ("failed", "탭 구성 다름")
    missing = "".join(c for c in wanted if c not in got)
    return ("failed", f"보호 기능 없음: {missing}") if missing else ("passed", "충족")


def _tap(canonical: str) -> tuple[int, int, Decimal]:
    """탭 표기를 (위 단 수, 아래 단 수, 간격)으로 나눈다. is_canonical을 통과한 값만 받는다."""
    match = CANONICAL_FORMS["tap"].fullmatch(canonical)
    assert match is not None
    both, up, down, step = match.groups()
    return int(both or up), int(both or down), Decimal(step)


def _plain_value(attribute: str, value: str) -> str:
    """문자열 비교용 값. 등급 속성은 'Class F'의 머리말을 뺀다(요구와 사양의 표기가 다르다).

    Args:
        attribute: 속성 어휘.
        value: 값.

    Returns:
        str: 정규화한 값.
    """
    text = _norm(value)
    return text.removeprefix("class ").strip() if attribute.endswith("_class") else text


def _value_verdict(requirement: Requirement, fact: SpecFact) -> tuple[Verdict, str]:
    """같은 속성의 후보 값 하나를 요구와 비교한다.

    숫자 연산자는 단위가 같고 두 값이 모두 숫자일 때만 비교한다. 숫자가 아닌 EQ와 TEXT는 정규화한
    문자열이 같을 때만 충족이다. 문장 값은 표현만 달라도 글자가 어긋나므로 다르면 미달이 아니라
    미확인이다. 형식(form)도 같다.

    Args:
        requirement: 요구.
        fact: 같은 속성의 후보 사양 근거.

    Returns:
        tuple[Verdict, str]: 결과와 판정 말.
    """
    if is_placeholder(requirement.value):
        return "unknown", "요구값 미정(자리표시자), 판정 제외"
    if requirement.canonical_value:
        return _canonical_verdict(requirement.attribute, requirement.canonical_value, fact.value)
    if requirement.operator != "TEXT":
        # 단위는 대소문자가 크기를 가른다(mV·MV). 공백만 정리한다.
        if _unit_key(requirement.unit) != _unit_key(fact.unit):
            if not requirement.unit:
                return "unknown", NO_NUMBER
            return "unknown", "단위가 달라 비교하지 않음, 확인 필요"
        got = _number(fact.value)
        if requirement.operator == "RANGE":
            low, _, high = requirement.value.partition("~")
            bounds = (_number(low), _number(high)) if high else (None, None)
            if got is None or bounds[0] is None or bounds[1] is None:
                return "unknown", "범위를 숫자로 읽을 수 없어 확인 필요"
            met = bounds[0] <= got <= bounds[1]
            return ("passed", "충족") if met else ("failed", shortfall_word(requirement))
        want = _number(requirement.value)
        if got is not None and want is not None:
            met = {"EQ": got == want, "GTE": got >= want, "LTE": got <= want}[requirement.operator]
            return ("passed", "충족") if met else ("failed", shortfall_word(requirement))
        if requirement.operator != "EQ":
            return "unknown", "값을 숫자로 읽을 수 없어 확인 필요"
    attribute = requirement.attribute
    if _plain_value(attribute, requirement.value) == _plain_value(attribute, fact.value):
        return "passed", "충족"
    return "unknown", "값이 달라 확인 필요"


class Converted(NamedTuple):
    """지속시간을 요구 값으로 바꾼 사양 근거."""

    fact: SpecFact
    note: str
    # 후보 지속시간이 요구보다 짧은지. 이때 환산값은 실제 정격보다 낮게 나오는 쪽이라 미달 근거가 되지 못한다.
    shorter: bool


def _at_required_duration(requirement: Requirement, facts: Sequence[SpecFact]) -> list[SpecFact]:
    """요구 지속시간의 실측 값이 있으면 그것만 본다(같은 후보의 다른 지속시간 값으로 환산해 덮지 않는다).

    Args:
        requirement: 요구.
        facts: 같은 속성의 후보 사양 근거.

    Returns:
        list[SpecFact]: 지속시간이 요구와 같은 근거. 없으면 전부다.
    """
    want = next((c for c in requirement.conditions if c.name == "duration"), None)
    if want is None:
        return list(facts)
    same = [
        f
        for f in facts
        if any(
            c.name == "duration"
            and (_number(c.value), _norm(_unit_key(c.unit)))
            == (_number(want.value), _norm(_unit_key(want.unit)))
            for c in f.conditions or []
        )
    ]
    return same or list(facts)


def _duration_converted(requirement: Requirement, fact: SpecFact) -> Converted | None:
    """단시간 내전류의 지속시간이 요구와 다르면 후보 값을 요구 지속시간 값으로 추정한다(KAN-199).

    I₂ = I₁·√(t₁/t₂)의 열 등가 추정이며 시험값이 아니다. 공개 근거로 확인한 것은 IEC 61439 저압 조립품(3 s까지, 정격
    피크 이하 조건)뿐이고, 고압·IEEE 장비에는 추정으로만 쓴다. 정격 피크 값은 자료에 없어 검사하지 못하므로 호출자가
    확인 항목으로 남긴다. 최소 요구(GTE)에만 쓴다. 호출자의 판정은 방향에 따라 다르다.

    - 충족: 어느 방향이든 확정하지 않고 조건부로 둔다.
    - 미달, 후보가 더 긴 시간 값: 미달이다(올린 값으로도 모자라다).
    - 미달, 후보가 더 짧은 시간 값(`shorter`): 미달로 확정하지 않는다. 짧은 시간 값을 내리면 실제 정격보다 낮게 나온다.

    Args:
        requirement: 요구.
        fact: 같은 속성의 후보 사양 근거.

    Returns:
        Converted | None: 지속시간을 요구 값으로 바꾸고 값을 추정한 근거. 단시간 내전류가 아니거나 GTE가 아니거나, 두
            쪽 지속시간이 같거나 하나라도 없거나, 초 단위가 아니거나, 하나라도 3 s를 넘거나, 숫자로 읽을 수 없으면 None.
    """
    if requirement.attribute != "short_time_withstand_current" or requirement.operator != "GTE":
        return None
    want = next((c for c in requirement.conditions if c.name == "duration"), None)
    got = next((c for c in fact.conditions or [] if c.name == "duration"), None)
    if want is None or got is None or _unit_key(want.unit) != "s" or _unit_key(got.unit) != "s":
        return None
    wanted, given, current = _number(want.value), _number(got.value), _number(fact.value)
    if wanted is None or given is None or current is None or wanted <= 0 or given <= 0:
        return None
    if wanted == given or max(wanted, given) > MAX_ESTIMATE_SECONDS:
        return None
    # 판정은 반올림하지 않은 값으로 한다(경계에서 미달이 충족으로 바뀌지 않게). 사유 문구에만 0.1로 반올림한다.
    value = current * (given / wanted).sqrt()
    conditions = [want if c.name == "duration" else c for c in fact.conditions or []]
    converted = fact.model_copy(update={"value": str(value), "conditions": conditions})
    shown = str(value.quantize(Decimal("0.1")))
    note = (
        f"열 등가 추정(I²t), 시험값 아님: {_with_unit(fact.value, fact.unit)}·"
        f"{_with_unit(got.value, got.unit)} → {_with_unit(want.value, want.unit)} "
        f"{_with_unit(shown, fact.unit)}"
    )
    return Converted(converted, note, given < wanted)


def _condition_gaps(requirement: Requirement, fact: SpecFact, verdict: Verdict) -> list[str]:
    """요구 조건과 후보 근거 조건의 차이를 찾는다.

    어휘 밖 조건(OTHER)은 값이 아니라 문구라 근거와 맞춰 볼 수 없다. 충족은 그대로 막지만(거짓 충족 방지)
    미달을 조건부로 약하게 만들지는 않는다.

    Args:
        requirement: 요구.
        fact: 후보 사양 근거.
        verdict: 값 비교 결과.

    Returns:
        list[str]: 'duration 요구 2 s, 후보 3 s' 같은 차이 목록.
    """
    given = {c.name: c for c in fact.conditions or []}
    gaps = []
    for want in requirement.conditions:
        if want.name == OTHER and verdict == "failed":
            continue
        got = given.get(want.name)
        if got is None:
            name = want.name.replace("_", " ")
            gaps.append(f"{name} 요구 {_with_unit(want.value, want.unit)}, 후보 근거에 없음")
        elif (_norm(got.value), _norm(_unit_key(got.unit))) != (
            _norm(want.value),
            _norm(_unit_key(want.unit)),
        ):
            gaps.append(
                f"{want.name.replace('_', ' ')} 요구 {_with_unit(want.value, want.unit)}, "
                f"후보 {_with_unit(got.value, got.unit)}"
            )
    return gaps


def _spec_check(
    requirement: Requirement, profile: PartnerProfile, codes: set[str], models: set[str]
) -> Check:
    """제안 모델의 같은 속성 사양 근거로 요구 하나를 판정한다.

    값이 충족이어도, 미달이어도 조건(시간·온도 등)이 다르면 확약하지 않고 미확인으로 둔다(KAN-199). 조건이 같을
    때만 미달이다. 여러 제품 중 하나라도 충족하면 충족이다.

    Args:
        requirement: 요구.
        profile: 프로필.
        codes: 제안 모델의 제품 코드.
        models: 제안 모델명(규격 체계 판단용).

    Returns:
        Check: (속성, 결과, 사유 문장). 충족이어도 요구 규격 체계가 근거 체계와 다르면 미확인이다(자사 비교와 같은 규칙,
            미달은 낮추지 않는다). 프로필에는 제품별 규격이 없어 제안 모델에 적용되는 certifications의 체계를 근거로 본다.
    """
    facts = [
        f
        for f in profile.spec_facts
        if f.product_code in codes and f.attribute == requirement.attribute
    ]
    if not facts:
        return (
            requirement.attribute,
            "unknown",
            f"{_head(requirement)}: 제안 모델에 같은 속성 근거가 없어 확인 필요.",
        )
    judged = []
    for fact in _at_required_duration(requirement, facts):
        converted = _duration_converted(requirement, fact)
        compared, note, shorter = converted if converted else (fact, None, False)
        verdict, word = _value_verdict(requirement, compared)
        gaps = _condition_gaps(requirement, compared, verdict)
        if verdict == "passed" and gaps:
            verdict, word = "unknown", "값은 충족하나 조건이 달라 확인 필요"
        elif verdict == "failed" and gaps:
            verdict, word = "unknown", differing_word(requirement)
        elif verdict == "failed" and shorter:
            verdict, word = "unknown", SHORTER_SHORTFALL
        elif verdict == "passed" and note:
            verdict, word = "unknown", "환산 충족(시험값 아님), 정격 피크 확인 필요"
        rank = ("passed", "unknown", "failed").index(verdict)
        judged.append((rank, verdict, word, fact, gaps, note))
    _, verdict, word, fact, gaps, note = min(judged, key=lambda j: j[0])
    systems = {
        _system(c.standard) for c in profile.certifications if models & set(c.applicable_models)
    } - {"NONE"}
    wanted = requirement.standard_system
    if verdict == "passed" and wanted != "NONE" and systems and wanted not in systems:
        basis = ", ".join(
            c.standard
            for c in profile.certifications
            if models & set(c.applicable_models) and _system(c.standard) in systems
        )
        verdict, word = "unknown", f"규격 체계 다름: 요구 {wanted}, 근거 {basis}, 시험 규격 확인"
    cited = " ".join(filter(None, ("근거", fact.evidence_id, fact.section)))
    sentence = f"{_head(requirement)}, 후보 {_with_unit(fact.value, fact.unit)}({cited}): {word}."
    if note:
        sentence += f" {note}."
    if gaps:
        sentence += f" 조건 차이: {'; '.join(gaps)}."
    return requirement.attribute, verdict, sentence


def _certification_check(
    requirement: Requirement, profile: PartnerProfile, models: set[str], reference_date: date
) -> Check:
    """certifications 배열로 인증 요구 하나를 판정한다.

    다른 규격의 근거는 요구의 충족으로 넓히지 않고 확인 필요로 둔다. 체계가 같아도 그렇다. 후보가
    요구 규격을 갖고 있지 않다는 것은 자료에 없다는 뜻이지 미달이 아니다(미기재는 미충족이 아니다).
    다만 같은 장비를 다른 제품군으로 규정하는 규격(RIVAL_STANDARDS)만 가졌으면 미달이다.

    Args:
        requirement: attribute가 certification이고 value가 규격명인 요구.
        profile: 프로필.
        models: 제안 모델명.
        reference_date: 유효기한 기준일.

    Returns:
        Check: (certification:규격명, 결과, 사유 문장).
    """
    name = f"certification:{requirement.value}"
    head = _head(requirement)
    listing = any(
        c.name == "listing" and _norm(c.value) == "listed" for c in requirement.conditions
    )
    same = [c for c in profile.certifications if _norm(c.standard) == _norm(requirement.value)]
    if not same:
        rival = [
            c
            for c in profile.certifications
            if frozenset({_norm(c.standard), _norm(requirement.value)}) in RIVAL_STANDARDS
        ]
        if rival:
            held = ", ".join(f"{c.standard}(근거 {c.evidence_id})" for c in rival)
            return name, "failed", f"{head}, 후보는 다른 제품군 규격 {held}만 있음: 미달."
        foreign = ", ".join(f"{c.standard}(근거 {c.evidence_id})" for c in profile.certifications)
        tail = f" 다른 규격 {foreign}는 충족 근거로 쓰지 않는다." if foreign else ""
        return name, "unknown", f"{head}: 후보에 같은 규격 근거가 없어 확인 필요.{tail}"
    covering = [c for c in same if models & set(c.applicable_models)]
    if not covering:
        held = ", ".join(f"{'·'.join(c.applicable_models)}(근거 {c.evidence_id})" for c in same)
        return (
            name,
            "failed",
            f"{head}, 인증 적용 모델 {held}에 제안 모델 {'·'.join(sorted(models))}이 없음: "
            "인증 적용 모델 다름.",
        )
    valid = [c for c in covering if c.valid_until is None or c.valid_until >= reference_date]
    if not valid:
        held = ", ".join(f"{c.valid_until}(근거 {c.evidence_id})" for c in covering)
        return (
            name,
            "failed",
            f"{head}, 인증 유효기한 {held}이 기준일 {reference_date}보다 앞섬: 인증 기준일 지남.",
        )
    listed = [c for c in valid if c.kind == "LISTED"]
    if listing and not listed:
        held = ", ".join(c.evidence_id for c in valid)
        return (
            name,
            "unknown",
            f"{head} 등록(Listed), 후보는 시험 성적(근거 {held})만 있어 확인 필요.",
        )
    best = (listed or valid)[0]
    kind = "등록" if best.kind == "LISTED" else "시험 성적"
    return name, "passed", f"{head}, 후보 {best.standard} {kind}(근거 {best.evidence_id}): 충족."


def _manufacturer(profile: PartnerProfile, context: RfpContext) -> tuple[bool | None, list[str]]:
    """고객 제조사 목록 해당 여부와 그에 따른 확인 조건.

    Args:
        profile: 프로필.
        context: RFP 맥락.

    Returns:
        tuple[bool | None, list[str]]: 목록이 없으면 None, 목록 안 True, 밖 False와 허용 조건별
            확인 문구(ALLOWED '발주처 승인 필요', UNSTATED 허용 여부 확인). FORBIDDEN은 제외라 문구 없음.
    """
    if context.manufacturer_list is None:
        return None, []
    if _norm(profile.manufacturer_name) in {_norm(m) for m in context.manufacturer_list}:
        return True, []
    return False, {
        "ALLOWED": ["발주처 승인 필요"],
        "UNSTATED": ["목록 밖 제조사 허용 여부 확인 필요"],
        "FORBIDDEN": [],
    }[context.off_list_permission]


def _constraints(profile: PartnerProfile, context: RfpContext) -> list[Check]:
    """요구와 무관한 후보 단위 필수 제약: 공급 지역, 통합 책임, 목록 외 제조사 금지.

    Args:
        profile: 프로필.
        context: RFP 맥락.

    Returns:
        list[Check]: 제약별 (항목, 결과, 사유 문장).
    """
    checks: list[Check] = []
    region = context.supply_region
    if region:
        regions = ", ".join(profile.supply_regions)
        if _norm(region) in {_norm(r) for r in profile.supply_regions}:
            checks.append(("supply_region", "passed", f"공급 지역 {region} 가능(후보 {regions})."))
        else:
            # 프로필 형식이 공급 지역을 1개 이상 요구하므로 '자료 없음' 경우는 없다.
            checks.append(
                ("supply_region", "failed", f"공급 지역 불일치: 요구 {region}, 후보 {regions}.")
            )
    if context.requires_integrator:
        for key, label in INTEGRATOR_LABELS.items():
            able = getattr(profile.integrator_capable, key)
            head = f"조립품 공급이라 통합 책임({label})이 필요"
            if able is True:
                checks.append((f"integrator_capable.{key}", "passed", f"{head}: 후보 가능."))
            elif able is False:
                checks.append((f"integrator_capable.{key}", "failed", f"{head}: 후보 불가."))
            else:
                checks.append((f"integrator_capable.{key}", "unknown", f"{head}: 후보 미확인."))
    on_list, _ = _manufacturer(profile, context)
    if on_list is False and context.off_list_permission == "FORBIDDEN":
        checks.append(
            (
                "manufacturer_list",
                "failed",
                f"제조사 {profile.manufacturer_name}는 고객 제조사 목록 밖이고 RFP가 목록 외 "
                "제조사를 금지함.",
            )
        )
    return checks
