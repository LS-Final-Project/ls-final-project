"""KAN-186 A05 자사 비교·네 상태·보완 경로. 14번 시연 A(2.5 몰드 변압기)와 경계 사례.

시연 A 기대 상태는 backend/api/tft/partners/demo_cases.json(기획서 4.7~4.9 표)에서 읽는다. 요구값은 14번
2.5절 원문(개발 문서)에서 옮겼다. 평가 문서(10·11·12·18)는 쓰지 않는다.
"""

import json
from pathlib import Path

import pytest

from backend.api.tft.partners.schemas import Condition, SpecFact
from backend.api.tft.rfp.comparison import (
    Certification,
    Product,
    Requirement,
    SearchTrace,
    compare,
    confusion,
    load_own_products,
    master_filter,
)
from backend.api.tft.rfp.supplement import options_for

DEMO_CASES = Path("backend/api/tft/partners/demo_cases.json")
EXAMPLE = Path("backend/api/tft/rfp/examples/comparison-demo-a.json")

# 14번 2.5절 요구. 시연 A 표의 항목 순서와 같다.
DEMO_A = [
    Requirement(
        clause_no="2.5.M", item="탭 ±2×2.5%", attribute="tap", value="±2×2.5%", operator="TEXT"
    ),
    Requirement(clause_no="2.5.I.1", item="절연 Class F", attribute="insulation_class", value="F"),
    Requirement(
        clause_no="2.5.N",
        item="임피던스 5.75% ±10%",
        attribute="impedance",
        value="5.175~6.325",
        unit="%",
        operator="RANGE",
        conditions=[Condition(name="reference_temperature", value="IEEE C57.12.01 기준")],
    ),
    Requirement(
        clause_no="2.5.C.1",
        item="강제 공랭 시 용량 증대 33%(115℃ 상승)",
        attribute="forced_air_capacity_increase",
        value="33",
        unit="%",
        operator="GTE",
        conditions=[Condition(name="temperature_rise", value="115", unit="°C")],
    ),
    Requirement(
        clause_no="2.5",
        item="2차 전압 480Y/277V",
        attribute="secondary_voltage",
        value="480",
        unit="V",
    ),
    Requirement(clause_no="2.5", item="1차 전압·용량(도면 참조)", attribute="primary_voltage"),
    Requirement(
        clause_no="2.5.K", item="BIL 95kV", attribute="bil", value="95", unit="kV", operator="GTE"
    ),
    Requirement(clause_no="2.5.I", item="IEEE C57.12.01", certification_standard="IEEE C57.12.01"),
    Requirement(
        clause_no="2.5.A",
        item="설계 기준: 타사 몰드 변압기 모델",
        designated_model="Eaton Resibloc",
    ),
]


def demo_a():
    products, trace = master_filter(load_own_products(), "TRANSFORMER")
    return [compare(requirement, products, trace) for requirement in DEMO_A]


def test_demo_a_matches_the_planned_statuses_with_datasheet_evidence():
    case = json.loads(DEMO_CASES.read_text())["cases"][0]
    assert case["case"] == "A" and case["own_product"] == "TRF-DRY-630"
    expected = [r["expected_status"] for r in case["requirements"]]
    outcomes = demo_a()
    assert [o.status for o in outcomes] == expected
    table = confusion(list(zip(expected, [o.status for o in outcomes], strict=True)))
    # Every case sits on the diagonal: no wrong status, and no unconfirmed item shown as met.
    assert sum(table[s][s] for s in table) == len(DEMO_A)
    tap, insulation, impedance, forced_air, secondary = outcomes[:5]
    assert tap.candidates[0].evidence[0].document == "DS-TRF-DRY-630"
    assert tap.candidates[0].evidence[0].clause == "2.2"
    assert (insulation.candidates[0].result, insulation.next_owner) == ("MEETS", None)
    # In range, but the datasheet value is at 120 °C, not the IEEE basis the RFP names.
    assert impedance.candidates[0].result == "MEETS"
    assert "reference_temperature" in impedance.unresolved[0]
    assert forced_air.next_owner == "TECH"
    assert (secondary.candidates[0].result, secondary.deficiency_cause) == ("DIFFERS", "RATING")
    assert "415 V" in secondary.reason


def test_unknown_and_missing_values_are_insufficient_not_unmet():
    primary, bil, cert = demo_a()[5:8]
    assert (primary.status, primary.next_owner, primary.candidates) == (
        "INSUFFICIENT_EVIDENCE",
        "CUSTOMER",
        [],
    )
    # No BIL value in the datasheet: not a gap, a missing document.
    assert (bil.status, bil.candidates[0].result) == ("INSUFFICIENT_EVIDENCE", "NO_DATA")
    # An IEC type test is not widened to an IEEE requirement.
    assert cert.status == "INSUFFICIENT_EVIDENCE"
    assert "IEC 체계만" in cert.reason


def test_no_candidate_is_reported_as_the_search_scope_result():
    outcome = compare(
        DEMO_A[0],
        [],
        SearchTrace(filters=["family=GIS"], documents_searched=0, search_failed=False),
    )
    assert outcome.status == "INSUFFICIENT_EVIDENCE"
    assert "검색 범위(family=GIS, 문서 0건)" in outcome.reason
    assert "참여 불가를 뜻하지 않음" in outcome.reason
    failed = compare(
        DEMO_A[0], [], SearchTrace(filters=[], documents_searched=0, search_failed=True)
    )
    assert failed.unresolved == ["후보 검색 재실행"]


def switchgear(voltage, unit="kV"):
    return Product(
        product_code="SWGR-TEST",
        family="SWITCHGEAR",
        lifecycle_status="ACTIVE",
        standards=["IEC 62271-200"],
        certifications=[],
        spec_facts=[
            SpecFact(
                product_code="SWGR-TEST",
                attribute="rated_max_voltage",
                value=voltage,
                unit=unit,
                conditions=None,
                evidence_id="DS-T",
                section="1",
            )
        ],
    )


def test_search_finds_similar_products_but_rules_compare_only_same_unit():
    """기획서 예: 15 kV 요구 ↔ 자사 12 kV. 검색은 후보로 찾고 규칙은 미달로 판정한다."""
    requirement = Requirement(
        clause_no="2.4",
        item="정격 15 kV",
        attribute="rated_max_voltage",
        value="15",
        unit="kV",
        operator="GTE",
    )
    trace = SearchTrace(filters=["family=SWITCHGEAR"], documents_searched=1, search_failed=False)
    gap = compare(requirement, [switchgear("12")], trace)
    assert (gap.status, gap.deficiency_cause) == ("SPEC_GAP", "RATING")
    other_unit = compare(requirement, [switchgear("15000", "V")], trace)
    assert (other_unit.status, other_unit.candidates[0].result) == ("CONDITIONAL", "NOT_COMPARABLE")
    assert other_unit.candidates[0].comparable is False
    # Several candidates: the best one decides, every one is listed.
    both = compare(requirement, [switchgear("12"), switchgear("17.5")], trace)
    assert both.status == "EVIDENCE_FOUND" and len(both.candidates) == 2


@pytest.mark.parametrize(
    ("permission", "paths", "allowed"),
    [
        ("ALLOWED", ["OTHER_OWN_PRODUCT", "EXTERNAL_PRODUCT"], [True, True]),
        ("FORBIDDEN", ["OTHER_OWN_PRODUCT", "EXTERNAL_PRODUCT"], [True, False]),
        ("UNSTATED", ["OTHER_OWN_PRODUCT", "EXTERNAL_PRODUCT"], [True, True]),
    ],
)
def test_rating_gap_paths_follow_the_cause_and_customer_permission(permission, paths, allowed):
    secondary = demo_a()[4]
    result = options_for(secondary, permission)
    assert [o.path for o in result.options] == paths
    assert [o.allowed for o in result.options] == allowed
    external = result.options[1]
    if permission == "FORBIDDEN":
        assert external.blocked_reason == "RFP가 외부 보완을 금지함"
    if permission == "UNSTATED":
        assert any("허용 여부가 없음" in c for c in external.conditions_to_confirm)


def test_task_gap_with_forbidden_subcontract_leaves_no_path_but_a_hold():
    outcome = compare(
        Requirement(
            clause_no="3.1",
            item="현장 시운전",
            attribute="rated_max_voltage",
            value="15",
            unit="kV",
            operator="GTE",
            task="commissioning",
        ),
        [switchgear("12")],
        SearchTrace(filters=[], documents_searched=1, search_failed=False),
    )
    result = options_for(outcome, "FORBIDDEN")
    assert outcome.deficiency_cause == "TASK"
    assert [(o.path, o.allowed) for o in result.options] == [
        ("TASK_SUBCONTRACT", False),
        ("NONE", True),
    ]


def test_designated_model_and_non_gaps_get_the_right_paths():
    outcomes = demo_a()
    designated = options_for(outcomes[8], "UNSTATED", designated=True)
    assert [o.path for o in designated.options] == ["EXTERNAL_PRODUCT", "ALTERNATIVE_SPEC"]
    assert "발주처 승인" in designated.options[1].conditions_to_confirm[0]
    # Evidence, conditions and missing data are next actions, not supplement paths.
    for outcome in (outcomes[0], outcomes[2], outcomes[5]):
        assert options_for(outcome, "ALLOWED").options == []


def example():
    """The contract example: demo A inputs and the engine output for each requirement."""
    products, trace = master_filter(load_own_products(), "TRANSFORMER")
    output = []
    for requirement in DEMO_A:
        outcome = compare(requirement, products, trace)
        supplement = options_for(outcome, "UNSTATED", designated=bool(requirement.designated_model))
        output.append(
            {
                "comparison": outcome.model_dump(mode="json"),
                "supplement": supplement.model_dump(mode="json"),
            }
        )
    return {
        "input": {
            "requirements": [r.model_dump(mode="json") for r in DEMO_A],
            "candidate_filter": trace.model_dump(mode="json"),
        },
        "output": output,
    }


def test_the_contract_example_is_the_current_engine_output():
    """backend/api/tft/rfp/examples의 입출력 예시가 지금 엔진 결과와 같다(화면 연결용, 손으로 고치지 않음)."""
    assert json.loads(EXAMPLE.read_text()) == example(), (
        "run: python -m tests.tft.test_rfp_comparison"
    )


if __name__ == "__main__":
    # Regenerates the contract example from the engine.
    EXAMPLE.parent.mkdir(parents=True, exist_ok=True)
    EXAMPLE.write_text(json.dumps(example(), ensure_ascii=False, indent=2) + "\n")


def test_a_product_not_on_sale_is_conditional_and_active_products_go_first():
    requirement = Requirement(
        clause_no="2.4",
        item="정격 15 kV",
        attribute="rated_max_voltage",
        value="15",
        unit="kV",
        operator="GTE",
    )
    trace = SearchTrace(filters=[], documents_searched=2, search_failed=False)
    mature = switchgear("17.5").model_copy(
        update={"product_code": "SWGR-OLD", "lifecycle_status": "MATURE"}
    )
    alone = compare(requirement, [mature], trace)
    assert (alone.status, alone.unresolved) == (
        "CONDITIONAL",
        ["수명주기 MATURE: 공급 지속 여부 확인"],
    )
    both = compare(requirement, [mature, switchgear("17.5")], trace)
    assert (both.status, both.candidates[1].product_code) == ("EVIDENCE_FOUND", "SWGR-TEST")


def test_values_that_cannot_be_compared_always_leave_something_to_check():
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    text = Requirement(
        clause_no="2.4",
        item="형식",
        attribute="rated_max_voltage",
        value="fifteen",
        unit="kV",
        operator="GTE",
    )
    outcome = compare(text, [switchgear("12")], trace)
    assert outcome.candidates[0].result == "NOT_COMPARABLE"
    assert outcome.unresolved == ["값 확인: 요구 fifteen, 자사 12"]


def certified(kind, variant=None):
    return switchgear("15").model_copy(
        update={
            "certifications": [
                Certification(standard="UL 1558", kind=kind, evidence_id="DS-T", variant=variant)
            ]
        }
    )


def test_certification_kind_and_variant_decide_gap_or_condition():
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    listed = Requirement(
        clause_no="2.1",
        item="UL 1558 Listed",
        certification_standard="UL 1558",
        certification_listed=True,
    )
    # A test report does not replace a listing: evidence of not meeting → certification gap.
    gap = compare(listed, [certified("TEST_REPORT")], trace)
    assert (gap.status, gap.deficiency_cause) == ("SPEC_GAP", "CERTIFICATION")
    paths = options_for(gap, "ALLOWED")
    assert [o.path for o in paths.options] == [
        "OTHER_OWN_PRODUCT",
        "EXTERNAL_PRODUCT",
        "ALTERNATIVE_SPEC",
    ]
    assert compare(listed, [certified("LISTED")], trace).status == "EVIDENCE_FOUND"
    # Held only by a variant model: not widened to the base model.
    variant = compare(listed, [certified("LISTED", variant="-U")], trace)
    assert (variant.status, variant.unresolved) == ("CONDITIONAL", ["-U 변형 모델 선택 확인"])
