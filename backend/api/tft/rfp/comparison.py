"""자사 비교와 네 가지 상태(KAN-186 A05). DB·모델 호출 없이 테스트한다.

후보 찾기(제품 마스터 필터·문서 검색)와 비교 판정을 나눈다. 검색은 비슷한 후보를 넓게 찾고, 판정은
같은 속성·같은 단위·같은 조건끼리만 규칙으로 한다. 예: 15 kV 요구와 자사 12 kV는 검색에서는 비슷한
개폐장치로 같이 나오지만 정격전압 비교에서는 미달이다(기획서 4.2 "같은 숫자라도 다른 속성은 비교하지
않는다"). 값 비교 규칙은 가상 협력사 등급(KAN-187 `partners/grading.py`)과 같은 함수를 쓴다.

상태 규칙(기획서 4.3):
- EVIDENCE_FOUND: 같은 속성 근거가 있고 값·조건이 맞음.
- CONDITIONAL: 값은 맞지만 조건이 다르거나 단위가 달라 비교할 수 없음, 또는 대응 경로(타사 지정 모델의
  조달·대체)가 있고 조건이 남음.
- SPEC_GAP: 같은 속성·단위·조건으로 비교해 미달인 근거가 있음. 조건이 다르면 미달이어도 조건부다(KAN-199).
- INSUFFICIENT_EVIDENCE: 요구값을 모름, 같은 속성 근거가 없음, 검색 범위에 후보가 없음. 미기재는
  미충족이 아니며 "제품 없음"은 검색한 범위의 결과로만 말한다.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from backend.api.tft.partners import grading
from backend.api.tft.partners.schemas import Condition, SpecFact
from backend.engine.rfp.requirements import Kind
from backend.engine.rfp.vocabulary import (
    DERIVED_SOURCE_NOTE,
    MASTER_SOURCE_NOTE,
    form_axes,
    is_placeholder,
)

Status = Literal["EVIDENCE_FOUND", "CONDITIONAL", "SPEC_GAP", "INSUFFICIENT_EVIDENCE"]
Result = Literal["MEETS", "DIFFERS", "NOT_COMPARABLE", "NO_DATA"]
# 명세 v1.3 §2. DESIGNATED_MODEL: 타사 모델 지정(지정품). COMMERCIAL: 상업 조건(보증 기간 등).
# CORE_EQUIPMENT_MISSING: 사업의 핵심 기기가 제공 자료에 없음(기획서 4.4 마지막 두 줄).
Cause = Literal[
    "RATING",
    "CERTIFICATION",
    "FORM",
    "TASK",
    "DESIGNATED_MODEL",
    "COMMERCIAL",
    "CORE_EQUIPMENT_MISSING",
]
# 제품 성능이 아니라 계약 조건인 속성. 부족하면 협력사로 풀지 않고 자사 조건 변경 검토로 간다.
COMMERCIAL_ATTRIBUTES = frozenset({"warranty_months"})
# RND: R&D 자문 질문이 열린 요구(명세 v1.3, 질문 생성은 보완 경로 선택 API가 한다).
NextOwner = Literal["SALES", "TECH", "PURCHASE", "CUSTOMER", "RND"]
# 상태가 나쁜 순서. 후보가 여럿이면 가장 좋은 후보의 상태가 요구의 상태다.
ORDER: tuple[Status, ...] = ("EVIDENCE_FOUND", "CONDITIONAL", "SPEC_GAP", "INSUFFICIENT_EVIDENCE")
# 값 자체를 바꾸는 기준 조건. 요구에 없어도 자사 값이 여기 기대면 충족을 확정하지 않는다(기획서: 임피던스 기준 온도).
BASIS_CONDITIONS = frozenset({"reference_temperature"})
SYSTEMS = ("IEC", "IEEE", "UL", "NEMA", "ANSI")
OWN_FACTS = Path(__file__).with_name("own_spec_facts.json")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Origin(Strict):
    """요구의 원문 위치(기획서 4.2 "원문" 칸). 판정마다 그대로 돌려줘 원문으로 돌아갈 수 있게 한다.

    API에서는 RequirementOrigin(document_version_id·clause_id·quote)으로 옮긴다(A07). 엔진은 문서 판본이 아직 없는
    입력(시연 예시)도 받도록 식별자를 문자열로 둔다.
    """

    document: str
    document_version_id: str | None = None
    clause_id: str | None = None
    clause_no: str | None = None
    # 취소선을 뺀 현행 원문. 원문에 값이 없으면 None이고 note에 이유를 적는다.
    quote: str | None = None
    note: str | None = None


class Requirement(Strict):
    """A04 추출 요구 하나. 인증 요구는 attribute 대신 certification_standard를 쓴다."""

    clause_no: str
    item: str
    attribute: str | None = None
    # 원문 속성 표현. 사유 문장에 쓴다. attribute는 비교 어휘 키다.
    attribute_label: str | None = None
    value: str | None = None
    unit: str | None = None
    operator: Literal["EQ", "GTE", "LTE", "RANGE", "TEXT"] = "EQ"
    conditions: list[Condition] = []
    necessity: Literal["MANDATORY", "PREFERRED", "CONDITION_BOUND", "UNDETERMINED"] = "MANDATORY"
    certification_standard: str | None = None
    # 등록(Listed)을 요구하면 시험 성적은 대신하지 못한다(기획서 4.2 "IEC 형식시험 ↔ UL 등록 요구").
    certification_listed: bool = False
    # 요구가 기대는 규격 체계(IEC·IEEE·UL·NEMA, 없으면 NONE). 근거 제품의 체계와 다르면 충족을 확정하지 않는다.
    standard_system: str = "NONE"
    designated_model: str | None = None
    # 설계 기준 제품(동등 이상 허용). 지정이 아니라 참고 기준이라 지정품 조달로 보내지 않는다.
    basis_of_design: bool = False
    # 문장형 값의 비교용 표기(탭·보호 기능). 있으면 value 대신 이것으로 견준다(grading._canonical_verdict).
    canonical_value: str | None = None
    task: str | None = None
    # 추출 요구 종류. 과업·문서 요구는 장비 사양이 아니라 계열 검사를 거치지 않는다(`_work_kind`).
    kind: Kind = "SPEC"
    # 사업의 중심 기기(예: 154 kV GIS)인지. 사람이 정한다. 검색 범위에 후보가 없으면 단순 외부 조달이 아니라
    # 자료 보완 요청 → 공동 제안 → 참여 보류 검토로 간다(기획서 4.4).
    core_equipment: bool = False
    origin: Origin | None = None


class Certification(Strict):
    standard: str
    # MARK: 제품 마스터에 인증 이름만 있고 등록·시험 구분이 없음(UL·CE·KC 등). 충족을 확정하지 않는다(기획서 2.3).
    kind: Literal["LISTED", "TEST_REPORT", "MARK"]
    evidence_id: str
    # 인증이 변형 모델(예: -U)에만 있으면 기본 모델의 인증으로 넓히지 않는다(기획서 4.2).
    variant: str | None = None


class Product(Strict):
    """자사 후보 제품. spec_facts는 협력사 프로필과 같은 열이다."""

    product_code: str
    family: str
    lifecycle_status: str | None
    standards: list[str]
    certifications: list[Certification]
    spec_facts: list[SpecFact]


class Evidence(Strict):
    document: str
    clause: str | None
    applicability: str | None
    limits: str | None


class CandidateMatch(Strict):
    product_code: str
    lifecycle_status: str | None
    comparable: bool
    result: Result
    our_value: str | None
    our_unit: str | None
    our_conditions: list[Condition]
    evidence: list[Evidence]
    status: Status
    reason: str
    unresolved: list[str]


class SearchTrace(Strict):
    filters: list[str]
    documents_searched: int
    search_failed: bool


class Outcome(Strict):
    """요구 하나의 비교 결과. API `Comparison`·`Judgement`로 옮기는 값이다."""

    clause_no: str
    item: str
    # 요구 쪽 원문 근거(제품 쪽 근거는 candidates[].evidence). 입력에 없으면 None이다.
    origin: Origin | None
    status: Status
    reason: str
    unresolved: list[str]
    next_owner: NextOwner | None
    deficiency_cause: Cause | None
    candidates: list[CandidateMatch]
    search_trace: SearchTrace


def system_of(standard: str) -> str:
    head = standard.split()[0].upper() if standard.split() else ""
    return head if head in SYSTEMS else "NONE"


def _evidence(fact: SpecFact) -> Evidence:
    # 데이터시트 문장을 정규화한 값이라는 한계를 근거에 같이 남긴다.
    return Evidence(
        document=fact.evidence_id,
        clause=fact.section,
        applicability=grading._conditions_text(fact.conditions or []) or None,
        limits=MASTER_SOURCE_NOTE if fact.evidence_id == "product_master" else DERIVED_SOURCE_NOTE,
    )


def _no_data(
    product: Product, status: Status, reason: str, unresolved: list[str]
) -> CandidateMatch:
    return CandidateMatch(
        product_code=product.product_code,
        lifecycle_status=product.lifecycle_status,
        comparable=False,
        result="NO_DATA",
        our_value=None,
        our_unit=None,
        our_conditions=[],
        evidence=[],
        status=status,
        reason=reason,
        unresolved=unresolved,
    )


def _evidence_systems(product: Product) -> tuple[set[str], set[str]]:
    """근거 제품의 규격 체계와, 변형 모델에만 있는 체계를 나눈다(KAN-199).

    체계는 Product.standards의 앞 낱말(system_of)이다. 변형 판별은 문자열이 아니라 certifications 구조로 한다: 그 체계의
    인증이 variant가 있는 것뿐이면(예: MCCB-ABS-403c의 UL -U) 기본 모델 값이 그 체계로 시험됐다는 근거가 없어
    근거 체계에서 뺀다.

    Args:
        product: 자사 후보.

    Returns:
        tuple[set[str], set[str]]: (근거 체계, 변형 모델에만 있는 체계). NONE은 넣지 않는다.
    """
    with_variant = {system_of(c.standard) for c in product.certifications if c.variant}
    without_variant = {system_of(c.standard) for c in product.certifications if not c.variant}
    variant_only = (with_variant - without_variant) - {"NONE"}
    systems = {system_of(s) for s in product.standards} - {"NONE"}
    return systems - variant_only, variant_only


def _system_mismatch(requirement: Requirement, product: Product) -> list[str]:
    """충족 판정을 확정하지 못하게 하는 규격 체계 차이를 찾는다(KAN-199).

    잘못된 충족을 막는 규칙이라 충족(EVIDENCE_FOUND)에만 쓴다. 미달(SPEC_GAP)은 낮추지 않는다: 정격 전압처럼 시험
    규격과 무관한 물리 정격의 미달(예: B-1 15 kV 요구 vs 12 kV, 요구 IEEE·근거 IEC)은 체계가 달라도 사양 차이다.
    조건 차이(주위 온도·지속시간 등)는 값 자체를 바꾸므로 미달도 조건부로 두는 것과 성격이 다르다.

    Args:
        requirement: 비교 입력.
        product: 자사 후보.

    Returns:
        list[str]: 확인할 항목. 요구 체계가 NONE이거나, 근거 체계를 모르거나(standards 비어 있음), 같으면 빈 목록.
    """
    wanted = requirement.standard_system
    if wanted == "NONE":
        return []
    systems, variant_only = _evidence_systems(product)
    found = []
    if systems and wanted not in systems:
        basis = ", ".join(s for s in product.standards if system_of(s) in systems)
        found.append(f"규격 체계 다름: 요구 {wanted}, 근거 {basis}, 시험 규격 확인")
    if wanted in variant_only:
        found.append(f"{wanted} 체계는 변형 모델에만 있음: 변형 모델 값 확인")
    return found


def _unstated_basis(requirement: Requirement, fact: SpecFact) -> list[str]:
    """요구에 없는데 자사 값이 기대는 기준 조건. 충족을 확정하지 못하는 이유로 쓴다(미달을 낮추지는 않는다).

    Args:
        requirement: 요구.
        fact: 자사 사양 근거.

    Returns:
        list[str]: 'reference temperature 요구 미기재, 자사 근거 120 °C' 같은 차이 목록.
    """
    stated = {c.name for c in requirement.conditions}
    return [
        f"{c.name.replace('_', ' ')} 요구 미기재, 자사 근거 {grading._with_unit(c.value, c.unit)}"
        for c in fact.conditions or []
        if c.name in BASIS_CONDITIONS and c.name not in stated
    ]


def _attribute_match(requirement: Requirement, product: Product) -> CandidateMatch:
    """같은 속성의 사양 근거로 판정한다. 근거가 여럿이면 가장 좋은 것을 쓴다."""
    head = grading._head(_as_grading(requirement))
    facts = [f for f in product.spec_facts if f.attribute == requirement.attribute]
    if not facts:
        # 미기재는 미충족이 아니다. 자료를 더 받아야 판단할 수 있다.
        return _no_data(
            product,
            "INSUFFICIENT_EVIDENCE",
            f"{head}: {product.product_code} 자료에 같은 속성 근거가 없음.",
            [f"{requirement.attribute_label or requirement.attribute} 자료 확인"],
        )
    judged = []
    for fact in grading._at_required_duration(_as_grading(requirement), facts):
        # 단시간 내전류는 지속시간을 요구 값으로 추정해 견준다(열 등가 추정, grading._duration_converted).
        converted = grading._duration_converted(_as_grading(requirement), fact)
        compared, note, shorter = converted if converted else (fact, None, False)
        verdict, word = grading._value_verdict(_as_grading(requirement), compared)
        gaps = grading._condition_gaps(_as_grading(requirement), compared, verdict)
        if verdict == "passed":
            gaps += _unstated_basis(requirement, compared)
        # 단위 차이는 사유 문구가 아니라 값으로 판단한다(grading 문구가 바뀌어도 그대로).
        unit_differs = (
            not requirement.canonical_value
            and requirement.operator != "TEXT"
            and grading._unit_key(requirement.unit) != grading._unit_key(fact.unit)
        )
        if verdict == "passed" and not gaps and note:
            # 환산값은 시험값이 아니라 충족으로 확정하지 않는다.
            status, result = "CONDITIONAL", "MEETS"
            word = "환산 충족(시험값 아님), 정격 피크 확인 필요"
        elif verdict == "passed" and not gaps:
            status, result = "EVIDENCE_FOUND", "MEETS"
        elif verdict == "passed":
            # 값은 맞지만 요구 조건(온도·지속시간 등)에서의 값이라는 근거가 없다. 협력사 등급과 같은 말을 쓴다.
            status, result = "CONDITIONAL", "MEETS"
            word = "값은 충족하나 조건이 달라 확인 필요"
        elif verdict == "failed" and gaps:
            # 미달이어도 요구와 다른 조건에서의 값이다. 같은 조건에서 미달이라는 근거가 없어 사양 차이로 확정하지 않는다.
            status, result = "CONDITIONAL", "DIFFERS"
            word = grading.differing_word(_as_grading(requirement))
        elif verdict == "failed" and shorter:
            # 짧은 시간 값을 내린 추정은 실제 정격보다 낮게 나온다. 미달 근거가 아니다.
            status, result = "CONDITIONAL", "DIFFERS"
            word = grading.SHORTER_SHORTFALL
        elif verdict == "failed":
            status, result = "SPEC_GAP", "DIFFERS"
        else:
            # 단위가 다르거나 숫자로 읽을 수 없어 비교하지 못했다. 같다고도 다르다고도 하지 않는다.
            status, result = "CONDITIONAL", "NOT_COMPARABLE"
            if requirement.attribute == "form" and not form_axes(requirement.value or ""):
                # 닫힌 축이 없는 형식 문장은 읽은 것이 없어 확인할 조건도 없다. 조건부로 올리지 않는다.
                status, word = "INSUFFICIENT_EVIDENCE", "형식 축을 읽지 못해 비교하지 않음"
        judged.append(
            (ORDER.index(status), status, result, word, fact, gaps, unit_differs, note, shorter)
        )
    _, status, result, word, fact, gaps, unit_differs, note, shorter = min(
        judged, key=lambda j: j[0]
    )
    cited = f"근거 {fact.evidence_id} {fact.section}"
    reason = f"{head}, 자사 {grading._with_unit(fact.value, fact.unit)}({cited}): {word}."
    if note:
        reason += f" {note}."
    if gaps:
        reason += f" 조건 차이: {'; '.join(gaps)}."
    unresolved = list(gaps)
    if note and result == "MEETS":
        unresolved += ["요구 지속시간의 시험값 확인(환산 충족)", "정격 피크 확인"]
    elif note and shorter:
        unresolved.append("요구 지속시간의 시험값 확인(짧은 시간 값의 추정)")
    mismatch = _system_mismatch(requirement, product) if status == "EVIDENCE_FOUND" else []
    if mismatch:
        status = "CONDITIONAL"
        reason += f" {'; '.join(mismatch)}."
        unresolved += mismatch
    if unit_differs:
        if requirement.unit:
            unresolved.append(
                f"단위 확인: 요구 {requirement.unit}, 자사 {fact.unit or '단위 없음'}"
            )
        else:
            ours = grading._with_unit(fact.value, fact.unit)
            word = grading.missing_word(_as_grading(requirement))
            unresolved.append(f"{word}: 원문 확인, 자사 {ours}")
    elif result == "NOT_COMPARABLE":
        # 숫자로 읽지 못했거나 문자열 값이 달라 비교하지 못한 경우도 확인 항목을 남긴다.
        wanted = requirement.value or grading.NO_NUMBER
        unresolved.append(f"값 확인: 요구 {wanted}, 자사 {fact.value}")
    return CandidateMatch(
        product_code=product.product_code,
        lifecycle_status=product.lifecycle_status,
        comparable=result in ("MEETS", "DIFFERS"),
        result=result,
        our_value=fact.value,
        our_unit=fact.unit,
        our_conditions=list(fact.conditions or []),
        evidence=[_evidence(fact)],
        status=status,
        reason=reason,
        unresolved=unresolved,
    )


def _certification_match(requirement: Requirement, product: Product) -> CandidateMatch:
    """인증 요구. 규격 체계가 다른 시험 성적을 충족으로 넓히지 않는다(기획서 4.2)."""
    wanted = requirement.certification_standard or ""
    key = grading.certification_key(wanted)
    held = [c for c in product.certifications if grading.certification_key(c.standard) == key]
    if held:
        head = f"원문 {requirement.clause_no} {wanted}"

        def judge(cert: Certification) -> CandidateMatch:
            def match(
                status: Status, result: Result, reason: str, unresolved: list[str]
            ) -> CandidateMatch:
                return CandidateMatch(
                    product_code=product.product_code,
                    lifecycle_status=product.lifecycle_status,
                    comparable=True,
                    result=result,
                    our_value=cert.standard,
                    our_unit=None,
                    our_conditions=[],
                    evidence=[
                        Evidence(
                            document=cert.evidence_id,
                            clause=None,
                            applicability=cert.kind,
                            limits=None,
                        )
                    ],
                    status=status,
                    reason=reason,
                    unresolved=unresolved,
                )

            if cert.kind == "MARK":
                # 사양표에 인증명이 있어도 해당 프로젝트 인증 충족으로 확정하지 않는다: 종류(등록/시험)와 적용
                # 모델·유효성 확인이 남는다(기획서 2.3 "사양표에 인증명이 있음").
                return match(
                    "CONDITIONAL",
                    "MEETS",
                    f"{head} 요구, 자사 자료에 {cert.standard} 표시만 있음(등록·시험 구분 미기재).",
                    [
                        f"{cert.standard} 인증 종류(등록/시험 성적)·적용 모델·유효성 확인",
                        *([f"{cert.variant} 변형 모델 선택 확인"] if cert.variant else []),
                    ],
                )
            if requirement.certification_listed and cert.kind != "LISTED":
                # 시험 성적은 등록을 대신하지 않는다: 미충족 근거가 있는 인증 부족(사양 차이).
                return match(
                    "SPEC_GAP",
                    "DIFFERS",
                    f"{head} 등록(Listed) 요구, 자사는 {cert.standard} 시험 성적만 있음.",
                    [],
                )
            if cert.variant:
                # 변형 모델에만 있는 인증: 대응 경로는 있지만 모델 선택이 남는다.
                return match(
                    "CONDITIONAL",
                    "MEETS",
                    f"{head} 요구, 자사 {cert.standard}({cert.kind})는 {cert.variant} 변형 모델에만 있음.",
                    [f"{cert.variant} 변형 모델 선택 확인"],
                )
            return match(
                "EVIDENCE_FOUND",
                "MEETS",
                f"{head} 요구, 자사 {cert.standard}({cert.kind}) 보유.",
                [],
            )

        # 같은 규격의 인증이 여럿이면(시험 성적·등록, 기본·변형) 모두 판정하고 가장 좋은 것을 쓴다.
        return min((judge(c) for c in held), key=lambda m: ORDER.index(m.status))
    systems = sorted({system_of(c.standard) for c in product.certifications} - {"NONE"})
    # 보유 여부를 확인할 자료가 없으면 근거 부족이다. 다른 체계 성적이 있다는 사실만 사유에 둔다.
    other = f" 자사 자료는 {'·'.join(systems)} 체계만 있음." if systems else ""
    return _no_data(
        product,
        "INSUFFICIENT_EVIDENCE",
        f"원문 {requirement.clause_no} {wanted} 요구: {product.product_code}의 보유 자료 없음.{other}",
        [f"{wanted} 보유 여부 확인"],
    )


def _as_grading(requirement: Requirement) -> grading.Requirement:
    return grading.Requirement(
        requirement_id=UUID(int=0),
        equipment="",
        attribute=requirement.attribute or "",
        attribute_label=requirement.attribute_label,
        value=requirement.value or "",
        unit=requirement.unit,
        operator=requirement.operator,
        conditions=requirement.conditions,
        standard_system="NONE",
        necessity=requirement.necessity,
        clause_no=requirement.clause_no,
        canonical_value=requirement.canonical_value,
    )


def _work_kind(requirement: Requirement) -> Literal["TASK", "DOCUMENT"] | None:
    """장비 사양이 아닌 요구의 종류. 판단은 이 함수 한 곳이다.

    Args:
        requirement: 비교 입력.

    Returns:
        Literal | None: 문서 요구면 DOCUMENT, 과업이면 TASK(kind가 TASK이거나 task 칸이 있음), 장비 사양이면 None.
    """
    if requirement.kind == "DOCUMENT":
        return "DOCUMENT"
    return "TASK" if requirement.kind == "TASK" or requirement.task else None


def _cause(requirement: Requirement) -> Cause:
    if _work_kind(requirement) == "TASK":
        return "TASK"
    if requirement.certification_standard:
        return "CERTIFICATION"
    if requirement.attribute == "form":
        return "FORM"
    if requirement.attribute in COMMERCIAL_ATTRIBUTES:
        return "COMMERCIAL"
    return "RATING"


def compare(requirement: Requirement, products: Sequence[Product], trace: SearchTrace) -> Outcome:
    """요구 하나를 검색 범위의 자사 후보 전부와 비교해 상태·사유·다음 담당을 정한다.

    Args:
        requirement: A04 추출 요구.
        products: 후보 찾기 결과(제품 마스터 필터·문서 검색). 비어 있으면 "검색 범위에 후보 없음".
        trace: 후보를 찾은 범위. "제품 없음"을 이 범위의 결과로만 말하기 위해 같이 돌려준다.

    Returns:
        Outcome: 상태와 후보별 비교. 점수는 만들지 않는다.
    """

    def outcome(
        status: Status,
        reason: str,
        unresolved: list[str],
        owner: NextOwner | None,
        cause: Cause | None,
        candidates: list[CandidateMatch],
    ) -> Outcome:
        return Outcome(
            clause_no=requirement.clause_no,
            item=requirement.item,
            origin=requirement.origin,
            status=status,
            reason=reason,
            unresolved=unresolved,
            next_owner=owner,
            deficiency_cause=cause,
            candidates=candidates,
            search_trace=trace,
        )

    if requirement.basis_of_design:
        return outcome(
            "CONDITIONAL",
            f"원문 {requirement.clause_no} 설계 기준 {requirement.value or requirement.item}: "
            "지정이 아니라 참고 기준이라 동등 이상 제안이 가능한지 고객 확인 필요.",
            ["동등 이상 제안 가능 여부(고객 확인)"],
            "CUSTOMER",
            None,
            [],
        )
    if requirement.designated_model:
        # 타사 지정 모델은 자사 제품과 같다고 보지 않는다. 지정품 조달 또는 대체 제안(고객 승인)으로
        # 나누는 대응 경로가 있어 조건부다(기획서 4.2·4.4).
        return outcome(
            "CONDITIONAL",
            f"원문 {requirement.clause_no} 지정 모델 {requirement.designated_model}: 타사 지정 모델이라 "
            "자사 제품과 같다고 보지 않음. 지정품 조달 또는 동등품 대체(발주처 승인) 확인.",
            ["동등품 허용 여부(발주처 승인)"],
            "CUSTOMER",
            # 지정품은 협력사 보완과 섞지 않는다(명세 v1.3 §5.1): 원인을 남겨 보완 경로가 지정품 조달로 간다.
            "DESIGNATED_MODEL",
            [],
        )
    if trace.search_failed:
        return outcome(
            "INSUFFICIENT_EVIDENCE",
            "후보 검색이 실패해 비교하지 못함. 다시 실행 필요.",
            ["후보 검색 재실행"],
            "TECH",
            None,
            [],
        )
    if is_placeholder(requirement.certification_standard or requirement.value):
        # 미정 표시(<Insert value> 등)는 값이 아니다. 판정에서 빼고 고객 회신을 받는다(기획서 4.9).
        return outcome(
            "INSUFFICIENT_EVIDENCE",
            f"원문 {requirement.clause_no} {requirement.item}: 요구값 미정(자리표시자). 충족 판정에서 제외.",
            ["요구값 확정(고객 회신)"],
            "CUSTOMER",
            None,
            [],
        )
    work = None if requirement.certification_standard else _work_kind(requirement)
    if work and (not requirement.value or requirement.attribute in (None, "OTHER") or not products):
        # 과업·문서 요구에 견줄 값이나 후보(계열 미정 포함)가 없으면 고객에게 물을 일이 아니라 내부에서 정한다
        # (기획서 4.9). '자사 후보 없음'도 수행 가능 여부와 무관해 쓰지 않는다. 미기재 ≠
        # 미충족이라 상태는 근거 부족이다. 과업만 원인을 남겨 자사 수행 확인 → 과업 위탁 경로를 낸다.
        check = "수행 주체 확인(자사/협력사)" if work == "TASK" else "제출 자료 준비 확인"
        kind_word = "수행 과업 요구" if work == "TASK" else "제출 문서 요구"
        return outcome(
            "INSUFFICIENT_EVIDENCE",
            f"원문 {requirement.clause_no} {requirement.item}: {kind_word}. {check} 필요.",
            [check],
            "TECH",
            "TASK" if work == "TASK" else None,
            [],
        )
    if not (requirement.certification_standard or requirement.value or requirement.canonical_value):
        # 요구값을 모르면(도면 참조 등) 비교하지 않는다. 고객 자료를 받아야 한다.
        return outcome(
            "INSUFFICIENT_EVIDENCE",
            f"원문 {requirement.clause_no} {requirement.item}: 요구값이 원문에 없음. 자료 요청 후 비교.",
            ["요구값(도면·자료) 확보"],
            "CUSTOMER",
            None,
            [],
        )
    if not products:
        scope = ", ".join(trace.filters) or "필터 없음"
        reason = (
            f"검색 범위({scope}, 문서 {trace.documents_searched}건)에서 자사 후보를 찾지 못함. "
            "회사 전체 미보유나 참여 불가를 뜻하지 않음."
        )
        if requirement.core_equipment:
            # 미기재 ≠ 미충족이라 상태는 근거 부족 그대로다. 원인만 남겨 보완 경로(자료 보완 → 공동 제안 →
            # 보류 의견)를 고를 수 있게 한다.
            return outcome(
                "INSUFFICIENT_EVIDENCE",
                reason + " 사업의 핵심 기기라 단순 외부 조달로 보지 않음.",
                [
                    "자사 자료 보완 요청(다른 사업부·제품 자료)",
                    "다른 자료 범위 검색 또는 담당 부서 확인",
                ],
                "SALES",
                "CORE_EQUIPMENT_MISSING",
                [],
            )
        return outcome(
            "INSUFFICIENT_EVIDENCE",
            reason,
            ["다른 자료 범위 검색 또는 담당 부서 확인"],
            "TECH",
            None,
            [],
        )
    matches = [
        _certification_match(requirement, p)
        if requirement.certification_standard
        else _attribute_match(requirement, p)
        for p in products
    ]
    # 판매 중(ACTIVE)이 아닌 제품(MATURE 등)은 근거가 맞아도 공급 지속 여부가 남아 조건부다. 후보별 비교에도
    # 같은 결과가 보이도록 모든 후보에 먼저 적용한 뒤 가장 좋은 후보를 고른다.
    matches = [
        m.model_copy(
            update={
                "status": "CONDITIONAL",
                "unresolved": [
                    *m.unresolved,
                    f"수명주기 {m.lifecycle_status}: 공급 지속 여부 확인",
                ],
            }
        )
        if m.status == "EVIDENCE_FOUND" and m.lifecycle_status not in (None, "ACTIVE")
        else m
        for m in matches
    ]
    # 같은 상태면 판매 중 후보를 먼저 본다.
    best = min(matches, key=lambda m: (ORDER.index(m.status), m.lifecycle_status != "ACTIVE"))
    owner: NextOwner | None = {
        "EVIDENCE_FOUND": None,
        "CONDITIONAL": "TECH",
        "SPEC_GAP": "TECH",
        "INSUFFICIENT_EVIDENCE": "TECH",
    }[best.status]
    cause = _cause(requirement) if best.status == "SPEC_GAP" else None
    if cause == "COMMERCIAL":
        # 계약 조건 차이는 기술이 아니라 영업·보증 담당이 본다.
        owner = "SALES"
    return outcome(best.status, best.reason, best.unresolved, owner, cause, matches)


def confusion(pairs: Sequence[tuple[Status, Status]]) -> dict[Status, dict[Status, int]]:
    """(정답, 예측) 쌍으로 상태별 혼동표를 만든다. 행은 정답, 열은 예측이다.

    Args:
        pairs: (기대 상태, 판정 상태) 목록.

    Returns:
        dict: 네 상태 × 네 상태 건수. 빈 칸은 0이다.
    """
    table: dict[Status, dict[Status, int]] = {s: dict.fromkeys(ORDER, 0) for s in ORDER}
    for expected, predicted in pairs:
        table[expected][predicted] += 1
    return table


def load_own_products(path: Path = OWN_FACTS) -> list[Product]:
    """자사 사양 파일을 읽는다. 원래 문장(source_value)·변환 메모(note)는 파일에만 두고 비교에 쓰지 않는다.

    Args:
        path: own_spec_facts.json 위치.

    Returns:
        list[Product]: 협력사 프로필과 같은 SpecFact 열을 가진 자사 후보.
    """
    data = json.loads(path.read_text())
    products = []
    for raw in data["products"]:
        facts = [
            SpecFact(
                product_code=raw["product_code"],
                **{k: v for k, v in fact.items() if k not in ("source_value", "note")},
            )
            for fact in raw["spec_facts"]
        ]
        products.append(
            Product(
                product_code=raw["product_code"],
                family=raw["family"],
                lifecycle_status=raw["lifecycle_status"],
                standards=raw["standards"],
                certifications=[Certification(**c) for c in raw["certifications"]],
                spec_facts=facts,
            )
        )
    return products


def compare_in_family(
    requirement: Requirement, family: str | None, products: Sequence[Product]
) -> Outcome:
    """요구 장비 계열의 자사 후보로만 비교한다(KAN-199). 다른 계열 제품의 같은 이름 속성은 근거로 쓰지 않는다.

    계열을 모르면(None·"OTHER") 비교하지 않고 근거 부족이다. 속성을 자사 사양 항목에 연결하지 못한 요구
    (attribute "OTHER", 인증 요구 제외)도 비교하지 않는다. 지정 모델 요구는 계열과 무관하게 지정품 판정을 쓴다.
    과업·문서 요구(`_work_kind`)는 장비 사양이 아니라 이 두 검사 없이 `compare`로 간다(인증 요구는 인증 경로).

    Args:
        requirement: 비교 입력.
        family: 요구 장비의 제품 계열(own_spec_facts family 값) 또는 "OTHER"·None.
        products: 자사 후보 전체.

    Returns:
        Outcome: 판정. search_trace.filters에 family를 남긴다.
    """

    def not_compared(reason: str, unresolved: str) -> Outcome:
        return Outcome(
            clause_no=requirement.clause_no,
            item=requirement.item,
            origin=requirement.origin,
            status="INSUFFICIENT_EVIDENCE",
            reason=f"원문 {requirement.clause_no} {requirement.item}: {reason}",
            unresolved=[unresolved],
            next_owner="TECH",
            deficiency_cause=None,
            candidates=[],
            search_trace=SearchTrace(
                filters=[f"family={family or 'NONE'}"], documents_searched=0, search_failed=False
            ),
        )

    not_equipment = requirement.certification_standard is None and _work_kind(requirement)
    named = requirement.designated_model is not None or requirement.basis_of_design
    if not named and not not_equipment:
        if family in (None, "OTHER"):
            return not_compared(
                "요구 장비 계열을 정하지 못해 비교하지 않음.", "요구 장비 계열 확인"
            )
        if requirement.certification_standard is None and requirement.attribute in (None, "OTHER"):
            return not_compared(
                "요구 속성을 자사 사양 항목에 연결하지 못해 비교하지 않음.",
                "요구 속성(사양 항목) 확인",
            )
    found, trace = master_filter(products, family or "OTHER")
    return compare(requirement, found, trace)


def master_filter(products: Sequence[Product], family: str) -> tuple[list[Product], SearchTrace]:
    """제품 마스터 계열 필터(compare_in_family가 쓴다). 문서 검색(R10)과 합치는 연결은 A07(KAN-188)에서 한다.

    Args:
        products: 자사 후보 전체.
        family: 요구 장비의 제품 계열(예: TRANSFORMER).

    Returns:
        tuple: 계열이 같은 후보와 그 검색 범위.
    """
    found = [p for p in products if p.family == family]
    return found, SearchTrace(
        filters=[f"family={family}"], documents_searched=len(found), search_failed=False
    )
