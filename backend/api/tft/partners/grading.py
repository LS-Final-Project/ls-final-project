"""가상 협력사 후보 × 요구 등급 계산(KAN-187). DB나 요청 없이 테스트한다.

프로필의 사실·근거만 읽는다. notes·display_name 같은 자유 문자열은 판정에 쓰지 않아서 그 안의
명령문이 결과를 바꾸지 못한다(기획서 5.4). 유사도 점수는 만들지 않는다.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

Grade = Literal["MANDATORY_CONFIRMED", "REVIEW", "REFERENCE_ONLY", "EXCLUDED"]
Verdict = Literal["passed", "failed", "unknown"]
# (확인 항목, 결과, 한국어 문장)
Check = tuple[str, Verdict, str]
# EXCLUDED를 뺀 등급의 낮은 순서. 후보 요약은 요구별 등급 중 가장 낮은 단계다.
RANK: tuple[Grade, ...] = ("REFERENCE_ONLY", "REVIEW", "MANDATORY_CONFIRMED")
SYSTEMS = ("IEC", "IEEE", "UL", "NEMA")
INTEGRATOR_LABELS = {"assembly": "조립", "test": "시험", "integration": "연동", "warranty": "보증"}


# ponytail: KAN-184의 schemas.PartnerProfile 병합 전 임시 모델(공유 형식 §1 중 판정에 쓰는 키).
# 병합 후 import로 바꾼다.
class Condition(BaseModel):
    name: str
    value: str
    unit: str | None = None


class Product(BaseModel):
    product_code: str
    model: str
    scope: list[str] = []


class IntegratorCapable(BaseModel):
    assembly: bool | None = None
    test: bool | None = None
    integration: bool | None = None
    warranty: bool | None = None


class SpecFact(BaseModel):
    product_code: str
    attribute: str
    value: str
    unit: str | None = None
    conditions: list[Condition] | None = None
    evidence_id: str
    section: str | None = None


class Certification(BaseModel):
    standard: str
    applicable_models: list[str]
    kind: Literal["LISTED", "TEST_REPORT"]
    as_of: date
    valid_until: date | None = None
    evidence_id: str


class PartnerProfile(BaseModel):
    candidate_code: str
    display_name: str
    manufacturer_name: str
    products: list[Product]
    integrator_capable: IntegratorCapable
    spec_facts: list[SpecFact]
    certifications: list[Certification]
    supply_regions: list[str]
    notes: str | None = None


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


class CandidateSummary(BaseModel):
    candidate_code: str
    on_customer_manufacturer_list: bool | None
    overall_grade: Grade
    conditions_to_confirm: list[str]


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
        item = _spec_check(requirement, profile, codes)
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


def grade_candidate(
    profile: PartnerProfile,
    requirements: Sequence[Requirement],
    context: RfpContext,
    reference_date: date,
) -> tuple[CandidateSummary, list[RequirementGrade]]:
    """후보 하나를 보완 대상 요구 전체에 대해 등급 매기고 후보 요약을 만든다.

    Args:
        profile: 가상 협력사 프로필.
        requirements: 보완 대상 요구 목록. 비어 있으면 안 된다.
        context: RFP 맥락.
        reference_date: 인증 유효기한 기준일.

    Returns:
        tuple[CandidateSummary, list[RequirementGrade]]: 후보 요약(요구별 등급에 EXCLUDED가 있으면
            EXCLUDED, 아니면 가장 낮은 단계)과 요구별 등급.
    """
    if not requirements:
        raise ValueError("At least one requirement is needed to grade a candidate.")
    grades = [grade_requirement(profile, r, context, reference_date) for r in requirements]
    on_list, conditions = _manufacturer(profile, context)
    found = {g.grade for g in grades}
    overall: Grade = (
        "EXCLUDED" if "EXCLUDED" in found else min(found, key=RANK.index)  # type: ignore[arg-type]
    )
    summary = CandidateSummary(
        candidate_code=profile.candidate_code,
        on_customer_manufacturer_list=on_list,
        overall_grade=overall,
        conditions_to_confirm=conditions,
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
        str: '원문 2.4 short_time_withstand_current 요구 38 kA 이상'.
    """
    wanted = _with_unit(requirement.value, requirement.unit)
    wanted = {"GTE": f"{wanted} 이상", "LTE": f"{wanted} 이하", "TEXT": f"'{wanted}'"}.get(
        requirement.operator, wanted
    )
    where = f"원문 {requirement.clause_no} " if requirement.clause_no else ""
    return f"{where}{requirement.attribute} 요구 {wanted}"


def _value_verdict(requirement: Requirement, fact: SpecFact) -> tuple[Verdict, str]:
    """같은 속성의 후보 값 하나를 요구와 비교한다.

    숫자 연산자는 단위가 같고 두 값이 모두 숫자일 때만 비교한다. 숫자가 아닌 EQ와 TEXT는 정규화한
    문자열이 같을 때만 충족이다. 형식(form)은 정해진 어휘라 다르면 미달, 그 밖의 문장 값은 다르면 미확인.

    Args:
        requirement: 요구.
        fact: 같은 속성의 후보 사양 근거.

    Returns:
        tuple[Verdict, str]: 결과와 판정 말.
    """
    if requirement.operator != "TEXT":
        if _norm(requirement.unit) != _norm(fact.unit):
            return "unknown", "단위가 달라 비교하지 않음, 확인 필요"
        got = _number(fact.value)
        if requirement.operator == "RANGE":
            low, _, high = requirement.value.partition("~")
            bounds = (_number(low), _number(high)) if high else (None, None)
            if got is None or bounds[0] is None or bounds[1] is None:
                return "unknown", "범위를 숫자로 읽을 수 없어 확인 필요"
            return ("passed", "충족") if bounds[0] <= got <= bounds[1] else ("failed", "미달")
        want = _number(requirement.value)
        if got is not None and want is not None:
            met = {"EQ": got == want, "GTE": got >= want, "LTE": got <= want}[requirement.operator]
            return ("passed", "충족") if met else ("failed", "미달")
        if requirement.operator != "EQ":
            return "unknown", "값을 숫자로 읽을 수 없어 확인 필요"
    if _norm(requirement.value) == _norm(fact.value):
        return "passed", "충족"
    if requirement.attribute == "form":
        return "failed", "형식 다름"
    return "unknown", "값이 달라 확인 필요"


def _condition_gaps(requirement: Requirement, fact: SpecFact) -> list[str]:
    """요구 조건과 후보 근거 조건의 차이를 찾는다.

    Args:
        requirement: 요구.
        fact: 후보 사양 근거.

    Returns:
        list[str]: 'duration 요구 2 s, 후보 3 s' 같은 차이 목록.
    """
    given = {c.name: c for c in fact.conditions or []}
    gaps = []
    for want in requirement.conditions:
        got = given.get(want.name)
        if got is None:
            gaps.append(f"{want.name} 요구 {_with_unit(want.value, want.unit)}, 후보 근거에 없음")
        elif (_norm(got.value), _norm(got.unit)) != (_norm(want.value), _norm(want.unit)):
            gaps.append(
                f"{want.name} 요구 {_with_unit(want.value, want.unit)}, "
                f"후보 {_with_unit(got.value, got.unit)}"
            )
    return gaps


def _spec_check(requirement: Requirement, profile: PartnerProfile, codes: set[str]) -> Check:
    """제안 모델의 같은 속성 사양 근거로 요구 하나를 판정한다.

    값이 충족이어도 조건(시간·온도 등)이 다르면 확약하지 않고 미확인으로 둔다. 미달이면 조건 차이를
    사유에 함께 남긴다. 여러 제품 중 하나라도 충족하면 충족이다.

    Args:
        requirement: 요구.
        profile: 프로필.
        codes: 제안 모델의 제품 코드.

    Returns:
        Check: (속성, 결과, 사유 문장).
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
    for fact in facts:
        verdict, word = _value_verdict(requirement, fact)
        gaps = _condition_gaps(requirement, fact)
        if verdict == "passed" and gaps:
            verdict, word = "unknown", "값은 충족하나 조건이 달라 확인 필요"
        judged.append((("passed", "unknown", "failed").index(verdict), verdict, word, fact, gaps))
    _, verdict, word, fact, gaps = min(judged, key=lambda j: j[0])
    cited = " ".join(filter(None, ("근거", fact.evidence_id, fact.section)))
    sentence = f"{_head(requirement)}, 후보 {_with_unit(fact.value, fact.unit)}({cited}): {word}."
    if gaps:
        sentence += f" 조건 차이: {'; '.join(gaps)}."
    return requirement.attribute, verdict, sentence


def _certification_check(
    requirement: Requirement, profile: PartnerProfile, models: set[str], reference_date: date
) -> Check:
    """certifications 배열로 인증 요구 하나를 판정한다.

    다른 규격 체계의 근거(IEC 시험 성적 등)는 UL·IEEE 요구의 충족으로 넓히지 않고 미확인으로 둔다.
    같은 체계의 다른 규격만 있으면(UL 891 vs UL 1558) 미달이다.

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
        system = _system(requirement.value)
        others = [
            c for c in profile.certifications if system != "NONE" and _system(c.standard) == system
        ]
        if others:
            held = ", ".join(f"{c.standard}(근거 {c.evidence_id})" for c in others)
            return name, "failed", f"{head}, 후보는 같은 체계의 다른 규격 {held}만 있음: 미달."
        foreign = ", ".join(f"{c.standard}(근거 {c.evidence_id})" for c in profile.certifications)
        tail = f" 다른 규격 체계 {foreign}는 충족 근거로 쓰지 않는다." if foreign else ""
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
        if region in profile.supply_regions:
            checks.append(("supply_region", "passed", f"공급 지역 {region} 가능(후보 {regions})."))
        elif not profile.supply_regions:
            checks.append(
                ("supply_region", "unknown", f"공급 지역 {region}: 후보 자료 없음, 확인 필요.")
            )
        else:
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
