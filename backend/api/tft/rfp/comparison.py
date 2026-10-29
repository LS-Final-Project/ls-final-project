"""자사 비교와 네 가지 상태(KAN-186 A05). DB·모델 호출 없이 테스트한다.

후보 찾기(제품 마스터 필터·문서 검색)와 비교 판정을 나눈다. 검색은 비슷한 후보를 넓게 찾고, 판정은
같은 속성·같은 단위·같은 조건끼리만 규칙으로 한다. 예: 15 kV 요구와 자사 12 kV는 검색에서는 비슷한
개폐장치로 같이 나오지만 정격전압 비교에서는 미달이다(기획서 4.2 "같은 숫자라도 다른 속성은 비교하지
않는다"). 값 비교 규칙은 가상 협력사 등급(KAN-187 `partners/grading.py`)과 같은 함수를 쓴다.

상태 규칙(기획서 4.3):
- EVIDENCE_FOUND: 같은 속성 근거가 있고 값·조건이 맞음.
- CONDITIONAL: 값은 맞지만 조건이 다르거나 단위가 달라 비교할 수 없음, 또는 대응 경로(타사 지정 모델의
  조달·대체)가 있고 조건이 남음.
- SPEC_GAP: 같은 속성·단위로 비교해 미달인 근거가 있음.
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

Status = Literal["EVIDENCE_FOUND", "CONDITIONAL", "SPEC_GAP", "INSUFFICIENT_EVIDENCE"]
Result = Literal["MEETS", "DIFFERS", "NOT_COMPARABLE", "NO_DATA"]
Cause = Literal["RATING", "CERTIFICATION", "FORM", "TASK"]
NextOwner = Literal["SALES", "TECH", "PURCHASE", "CUSTOMER"]
# 상태가 나쁜 순서. 후보가 여럿이면 가장 좋은 후보의 상태가 요구의 상태다.
ORDER: tuple[Status, ...] = ("EVIDENCE_FOUND", "CONDITIONAL", "SPEC_GAP", "INSUFFICIENT_EVIDENCE")
SYSTEMS = ("IEC", "IEEE", "UL", "NEMA", "ANSI")
OWN_FACTS = Path(__file__).with_name("own_spec_facts.json")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Requirement(Strict):
    """A04 추출 요구 하나. 인증 요구는 attribute 대신 certification_standard를 쓴다."""

    clause_no: str
    item: str
    attribute: str | None = None
    value: str | None = None
    unit: str | None = None
    operator: Literal["EQ", "GTE", "LTE", "RANGE", "TEXT"] = "EQ"
    conditions: list[Condition] = []
    necessity: Literal["MANDATORY", "PREFERRED", "CONDITION_BOUND", "UNDETERMINED"] = "MANDATORY"
    certification_standard: str | None = None
    designated_model: str | None = None
    task: str | None = None


class Certification(Strict):
    standard: str
    kind: Literal["LISTED", "TEST_REPORT"]
    evidence_id: str


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
        limits="합성 데이터시트 문장을 정규화한 값(DERIVED)",
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
            [f"{requirement.attribute} 자료 확인"],
        )
    judged = []
    for fact in facts:
        verdict, word = grading._value_verdict(_as_grading(requirement), fact)
        gaps = grading._condition_gaps(_as_grading(requirement), fact)
        unit_differs = verdict == "unknown" and word.startswith("단위가 달라")
        if verdict == "passed" and not gaps:
            status, result = "EVIDENCE_FOUND", "MEETS"
        elif verdict == "passed":
            # 값은 맞지만 요구 조건(온도·지속시간 등)에서의 값이라는 근거가 없다. 협력사 등급과 같은 말을 쓴다.
            status, result = "CONDITIONAL", "MEETS"
            word = "값은 충족하나 조건이 달라 확인 필요"
        elif verdict == "failed":
            status, result = "SPEC_GAP", "DIFFERS"
        else:
            # 단위가 다르거나 숫자로 읽을 수 없어 비교하지 못했다. 같다고도 다르다고도 하지 않는다.
            status, result = "CONDITIONAL", "NOT_COMPARABLE"
        judged.append((ORDER.index(status), status, result, word, fact, gaps, unit_differs))
    _, status, result, word, fact, gaps, unit_differs = min(judged, key=lambda j: j[0])
    cited = f"근거 {fact.evidence_id} {fact.section}"
    reason = f"{head}, 자사 {grading._with_unit(fact.value, fact.unit)}({cited}): {word}."
    if gaps:
        reason += f" 조건 차이: {'; '.join(gaps)}."
    unresolved = list(gaps)
    if unit_differs:
        unresolved.append(f"단위 확인: 요구 {requirement.unit}, 자사 {fact.unit}")
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
    held = [c for c in product.certifications if grading._norm(c.standard) == grading._norm(wanted)]
    if held:
        cert = held[0]
        return CandidateMatch(
            product_code=product.product_code,
            lifecycle_status=product.lifecycle_status,
            comparable=True,
            result="MEETS",
            our_value=cert.standard,
            our_unit=None,
            our_conditions=[],
            evidence=[
                Evidence(
                    document=cert.evidence_id, clause=None, applicability=cert.kind, limits=None
                )
            ],
            status="EVIDENCE_FOUND",
            reason=f"원문 {requirement.clause_no} {wanted} 요구, 자사 {cert.standard}({cert.kind}) 보유.",
            unresolved=[],
        )
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
        value=requirement.value or "",
        unit=requirement.unit,
        operator=requirement.operator,
        conditions=requirement.conditions,
        standard_system="NONE",
        necessity=requirement.necessity,
        clause_no=requirement.clause_no,
    )


def _cause(requirement: Requirement) -> Cause:
    if requirement.task:
        return "TASK"
    if requirement.certification_standard:
        return "CERTIFICATION"
    if requirement.attribute == "form":
        return "FORM"
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
            status=status,
            reason=reason,
            unresolved=unresolved,
            next_owner=owner,
            deficiency_cause=cause,
            candidates=candidates,
            search_trace=trace,
        )

    if requirement.designated_model:
        # 타사 지정 모델은 자사 제품과 같다고 보지 않는다. 지정품 조달 또는 대체 제안(고객 승인)으로
        # 나누는 대응 경로가 있어 조건부다(기획서 4.2·4.4).
        return outcome(
            "CONDITIONAL",
            f"원문 {requirement.clause_no} 설계 기준 {requirement.designated_model}: 타사 지정 모델이라 "
            "자사 제품과 같다고 보지 않음. 지정품 조달 또는 동등품 대체(발주처 승인) 확인.",
            ["동등품 허용 여부(발주처 승인)"],
            "CUSTOMER",
            None,
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
    if not requirement.certification_standard and not requirement.value:
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
        return outcome(
            "INSUFFICIENT_EVIDENCE",
            f"검색 범위({scope}, 문서 {trace.documents_searched}건)에서 자사 후보를 찾지 못함. "
            "회사 전체 미보유나 참여 불가를 뜻하지 않음.",
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
    best = min(matches, key=lambda m: ORDER.index(m.status))
    owner: NextOwner | None = {
        "EVIDENCE_FOUND": None,
        "CONDITIONAL": "TECH",
        "SPEC_GAP": "TECH",
        "INSUFFICIENT_EVIDENCE": "TECH",
    }[best.status]
    cause = _cause(requirement) if best.status == "SPEC_GAP" else None
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


def master_filter(products: Sequence[Product], family: str) -> tuple[list[Product], SearchTrace]:
    """제품 마스터 계열 필터. 문서 검색(R10)과 합치는 연결은 A07(KAN-188)에서 한다.

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
