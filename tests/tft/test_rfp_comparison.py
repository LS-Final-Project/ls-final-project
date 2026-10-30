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
    Origin,
    Product,
    Requirement,
    SearchTrace,
    compare,
    compare_in_family,
    confusion,
    load_own_products,
    master_filter,
)
from backend.api.tft.rfp.supplement import options_for

DEMO_CASES = Path("backend/api/tft/partners/demo_cases.json")
EXAMPLE = Path("backend/api/tft/rfp/examples/comparison-demo-a.json")

# 14번 2.5절 요구(취소선 뺀 현행 원문). 시연 A 표의 항목 순서이고, 묶인 두 행(BIL·권선·센서·ModBus,
# IEEE·소음·Listed)은 요구별로 나눴다(2026-10-30 결정).
_DEMO_A = [
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
    Requirement(
        clause_no="2.5.L",
        item="알루미늄 권선",
        attribute="winding_material",
        value="aluminum",
        operator="TEXT",
    ),
    Requirement(
        clause_no="2.5.G.1",
        item="이중 온도센서",
        attribute="winding_temperature_sensor",
        value="dual",
        operator="TEXT",
    ),
    Requirement(
        clause_no="2.5.E",
        item="ModBus RS-485 연동",
        attribute="communication_interface",
        value="RS-485 ModBus",
        operator="TEXT",
    ),
    Requirement(clause_no="2.5.I", item="IEEE C57.12.01", certification_standard="IEEE C57.12.01"),
    Requirement(
        clause_no="2.5.O",
        item="소음 NEMA TR1(측정 ANSI C57.12.90)",
        certification_standard="NEMA TR1",
    ),
    Requirement(
        clause_no="2.2.A.1",
        item="부품 Listed and labeled(NFPA 70)",
        certification_standard="UL",
        certification_listed=True,
    ),
    Requirement(
        clause_no="2.5.A",
        item="설계 기준: 타사 몰드 변압기 모델",
        value="Eaton Resibloc",
        operator="TEXT",
        basis_of_design=True,
    ),
]


# 14번 문서(RFP 데이터셋, Section 261116.11)의 원문 위치. 취소선을 뺀 현행 문장이다. 시연 예시라 문서 판본 ID 대신
# 데이터셋 번호를 쓴다(API는 document_version_id·clause_id, A07).
DOC14 = "RFP-14 261116.11"
_ORIGINS = {
    "탭 ±2×2.5%": (
        "2.5.M",
        "Full-Capacity Voltage Taps: Four nominal 2.5 percent taps, two above and two below rated primary voltage.",
        None,
    ),
    "절연 Class F": (
        "2.5.I.1",
        "The electrical insulation system shall utilize Class F materials in a fully rated 155 degrees C system.",
        None,
    ),
    "임피던스 5.75% ±10%": ("2.5.N", "Impedance: 5.75 percent, +/-10%.", None),
    "강제 공랭 시 용량 증대 33%(115℃ 상승)": (
        "2.5.C.1",
        "480Y/277V Secondary: 33% with fans at 115 degree C rise above 40 degree C and average ambient of 30 degree C.",
        None,
    ),
    "2차 전압 480Y/277V": (
        "2.5.C.1",
        "480Y/277V Secondary: 33% with fans at 115 degree C rise above 40 degree C and average ambient of 30 degree C.",
        "2차 전압은 2.5.C의 2차 전압별 항목에 있다",
    ),
    "1차 전압·용량(도면 참조)": (
        "2.5",
        None,
        "원문에 1차 전압·용량 값이 없다(도면 참조). 인용할 문장 없음",
    ),
    "BIL 95kV": ("2.5.K", "BIL: 95 kV.", None),
    "알루미늄 권선": (
        "2.5.L",
        "Windings: The high and low voltage windings shall be aluminum.",
        None,
    ),
    "이중 온도센서": (
        "2.5.G.1",
        "Dual temperature sensors shall be applied to the winding of each phase of the transformer.",
        None,
    ),
    "ModBus RS-485 연동": (
        "2.5.E",
        "Transformer mounted serial communications module shall render the following data via RS-485 ModBus interface",
        None,
    ),
    "IEEE C57.12.01": ("2.5.I", "Insulation Materials: IEEE C57.12.01.", None),
    "소음 NEMA TR1(측정 ANSI C57.12.90)": (
        "2.5.O",
        "The transformer shall comply with the sound level standards for dry transformers as defined in NEMA TR1. The measurement procedure shall be as specified in ANSI C57.12.90",
        None,
    ),
    "부품 Listed and labeled(NFPA 70)": (
        "2.2.A.1",
        "Electrical Components, Devices, and Accessories: Listed and labeled as defined in NFPA 70, by a qualified testing agency, and marked for intended location and application.",
        None,
    ),
    "설계 기준: 타사 몰드 변압기 모델": (
        "2.5.A",
        "Style: Indoor, ventilated, insulated with epoxy, reinforced with glass fiber. 1. Basis of design: Eaton Resibloc",
        None,
    ),
}
DEMO_A = [
    r.model_copy(
        update={
            "origin": Origin(
                document=DOC14,
                clause_no=_ORIGINS[r.item][0],
                quote=_ORIGINS[r.item][1],
                note=_ORIGINS[r.item][2],
            )
        }
    )
    for r in _DEMO_A
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
    outcomes = demo_a()
    primary, bil, cert = outcomes[5], outcomes[6], outcomes[10]
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
    # The split rows: no datasheet attribute and no listing evidence are missing data too.
    assert [o.status for o in outcomes[7:13]] == ["INSUFFICIENT_EVIDENCE"] * 6


def test_the_eleven_demo_conditions_each_remain_open():
    """기획서 4.7 "시연 A 결과"의 확인 조건 ①~⑪: 근거 있음 두 요구(탭·절연)를 뺀 12개 요구가 하나씩
    남는다(⑨ IEEE 시험·UL 등록이 두 요구). 사양 차이는 상태로, 나머지는 미확인 항목으로 남는다."""
    outcomes = demo_a()
    conditions = {
        "① 2차 전압": [4],
        "② 1차 전압·용량": [5],
        "③ 공랭 증대율 시험 조건": [3],
        "④ 임피던스 기준 온도": [2],
        "⑤ BIL 95kV": [6],
        "⑥ 알루미늄 권선": [7],
        "⑦ 이중 온도센서": [8],
        "⑧ ModBus 연동": [9],
        "⑨ IEEE 시험·UL 등록": [10, 12],
        "⑩ 소음 NEMA TR1": [11],
        "⑪ 기준 모델 동등 인정": [13],
    }
    rows = sorted(i for indexes in conditions.values() for i in indexes)
    assert rows == list(range(2, 14))
    for name, indexes in conditions.items():
        for i in indexes:
            open_item = outcomes[i].status == "SPEC_GAP" or bool(outcomes[i].unresolved)
            assert outcomes[i].status != "EVIDENCE_FOUND" and open_item, name
    assert [o.status for o in outcomes[:2]] == ["EVIDENCE_FOUND", "EVIDENCE_FOUND"]


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
        ("ALLOWED", ["OTHER_OWN_PRODUCT", "OWN_DEVELOPMENT", "EXTERNAL_PRODUCT"], [True] * 3),
        (
            "FORBIDDEN",
            ["OTHER_OWN_PRODUCT", "OWN_DEVELOPMENT", "EXTERNAL_PRODUCT"],
            [True, True, False],
        ),
        ("UNSTATED", ["OTHER_OWN_PRODUCT", "OWN_DEVELOPMENT", "EXTERNAL_PRODUCT"], [True] * 3),
    ],
)
def test_rating_gap_paths_follow_the_cause_and_customer_permission(permission, paths, allowed):
    secondary = demo_a()[4]
    result = options_for(secondary, permission)
    assert [o.path for o in result.options] == paths
    assert [o.allowed for o in result.options] == allowed
    # Own development (R&D) comes before any partner path (proposal V_1 4.4).
    assert result.options[1].required_expertise == ["R&D"]
    external = result.options[2]
    if permission == "FORBIDDEN":
        assert external.blocked_reason == "RFP가 외부 보완을 금지함"
    if permission == "UNSTATED":
        assert any("허용 여부가 없음" in c for c in external.conditions_to_confirm)


def test_task_gap_checks_own_performance_before_a_forbidden_subcontract():
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
        ("OWN_DEVELOPMENT", True),
        ("TASK_SUBCONTRACT", False),
    ]


def test_a_forbidden_external_path_alone_leaves_a_hold(monkeypatch):
    """경로가 모두 막히면 보류 의견(NONE)을 덧붙인다. 지금 표에는 그런 원인이 없어 표를 바꿔 확인한다."""
    from backend.api.tft.rfp import supplement

    monkeypatch.setitem(supplement.FIRST_PATHS, "RATING", ("EXTERNAL_PRODUCT",))
    result = options_for(demo_a()[4], "FORBIDDEN")
    assert [(o.path, o.allowed) for o in result.options] == [
        ("EXTERNAL_PRODUCT", False),
        ("NONE", True),
    ]


def test_a_form_gap_starts_with_own_development():
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    form = Requirement(
        clause_no="2.4",
        item="형식",
        attribute="rated_max_voltage",
        value="15",
        unit="kV",
        operator="GTE",
    )
    gap = compare(form, [switchgear("12")], trace).model_copy(update={"deficiency_cause": "FORM"})
    assert [o.path for o in options_for(gap, "ALLOWED").options] == [
        "OWN_DEVELOPMENT",
        "EXTERNAL_PRODUCT",
        "ALTERNATIVE_SPEC",
    ]


def test_a_commercial_gap_is_reviewed_by_sales_not_solved_by_partners():
    """기획서 4.4: 보증 5년 요구 ↔ 자사 24개월(18번 예). 자사 조건 변경 검토 → 대체 조건 제안."""
    warranty = Requirement(
        clause_no="1.6",
        item="보증 60개월",
        attribute="warranty_months",
        value="60",
        unit="month",
        operator="GTE",
    )
    products, trace = master_filter(load_own_products(), "TRANSFORMER")
    gap = compare(warranty, products, trace)
    assert (gap.status, gap.deficiency_cause, gap.next_owner) == ("SPEC_GAP", "COMMERCIAL", "SALES")
    assert gap.candidates[0].evidence[0].limits == "제품 마스터 표준 조건(합성)"
    paths = options_for(gap, "FORBIDDEN")
    assert [(o.path, o.allowed) for o in paths.options] == [
        ("OWN_TERMS_REVIEW", True),
        ("ALTERNATIVE_SPEC", True),
    ]
    assert paths.options[0].required_expertise == ["영업·보증"]
    met = compare(warranty.model_copy(update={"value": "24"}), products, trace)
    assert met.status == "EVIDENCE_FOUND"


def test_core_equipment_missing_from_the_data_goes_to_joint_proposal_or_hold():
    """기획서 4.4: 154 kV GIS 같은 핵심 기기가 제공 자료에 없으면 단순 외부 조달로 보지 않는다."""
    gis = Requirement(
        clause_no="2.1",
        item="154 kV GIS",
        attribute="rated_max_voltage",
        value="154",
        unit="kV",
        operator="GTE",
        core_equipment=True,
    )
    trace = SearchTrace(filters=["family=GIS"], documents_searched=0, search_failed=False)
    outcome = compare(gis, [], trace)
    # Missing data is not unmet: the state stays insufficient evidence.
    assert (outcome.status, outcome.deficiency_cause, outcome.next_owner) == (
        "INSUFFICIENT_EVIDENCE",
        "CORE_EQUIPMENT_MISSING",
        "SALES",
    )
    assert "자사 자료 보완 요청" in outcome.unresolved[0]
    allowed = options_for(outcome, "UNSTATED")
    assert [o.path for o in allowed.options] == ["JOINT_PROPOSAL", "NONE"]
    assert "EXTERNAL_PRODUCT" not in [o.path for o in allowed.options]
    forbidden = options_for(outcome, "FORBIDDEN")
    assert [(o.path, o.allowed) for o in forbidden.options] == [
        ("JOINT_PROPOSAL", False),
        ("NONE", True),
    ]
    # Not core equipment: the same empty search is only missing data, without a path.
    plain = compare(gis.model_copy(update={"core_equipment": False}), [], trace)
    assert plain.deficiency_cause is None and options_for(plain, "ALLOWED").options == []


def designated_outcome():
    """대체 불가 지정 요구의 판정. 시연 A의 설계 기준(참고 기준)과 다르다."""
    wanted = Requirement(clause_no="2.5.A", item="계전기", designated_model="Model X")
    return compare_in_family(wanted, "TRANSFORMER", load_own_products())


def test_designated_model_and_non_gaps_get_the_right_paths():
    outcomes = demo_a()
    named = designated_outcome()
    assert named.deficiency_cause == "DESIGNATED_MODEL"
    designated = options_for(named, "UNSTATED")
    # Spec v1.3 §5.1: only the specified-item procurement and an alternative (customer approval)
    # can be chosen; the other paths are shown as blocked with the reason.
    allowed = {o.path: o for o in designated.options if o.allowed}
    assert set(allowed) == {"CUSTOMER_SPECIFIED_PROCUREMENT", "ALTERNATIVE_SPEC"}
    assert allowed["CUSTOMER_SPECIFIED_PROCUREMENT"].rank == 4
    assert {"고객 승인", "고객 질문 작성 필요"} <= set(
        allowed["ALTERNATIVE_SPEC"].conditions_to_confirm
    )
    blocked = {o.path: o.blocked_reason for o in designated.options if not o.allowed}
    assert blocked == dict.fromkeys(
        [
            "OTHER_OWN_PRODUCT",
            "OWN_DEVELOPMENT",
            "EXTERNAL_PRODUCT",
            "TASK_SUBCONTRACT",
            "JOINT_PROPOSAL",
        ],
        "DESIGNATED_ITEM",
    )
    asked = options_for(named, "UNSTATED", approval_question=True)
    alt = next(o for o in asked.options if o.path == "ALTERNATIVE_SPEC")
    assert "고객 질문 작성 필요" not in alt.conditions_to_confirm
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


def test_a_transformer_requirement_is_not_met_by_breaker_evidence():
    """재현(KAN-199, Codex 검토): 변압기 rated_current 요구가 다른 계열(MCCB)의 같은 이름 근거로 충족되지 않는다.

    전체 제품과 비교하면 MCCB의 600 A가 근거가 되지만, 요구 계열(TRANSFORMER) 후보만 보면 같은 속성 근거가 없다.
    """
    products = load_own_products()
    wanted = Requirement(
        clause_no="2.5.X",
        item="변압기 정격전류 400 A",
        attribute="rated_current",
        value="400",
        unit="A",
        operator="GTE",
    )
    everything = SearchTrace(
        filters=["own_products=all"], documents_searched=0, search_failed=False
    )
    assert compare(wanted, products, everything).status == "EVIDENCE_FOUND"
    scoped = compare_in_family(wanted, "TRANSFORMER", products)
    assert scoped.status == "INSUFFICIENT_EVIDENCE"
    assert scoped.search_trace.filters == ["family=TRANSFORMER"]
    assert {c.product_code for c in scoped.candidates} == {"TRF-DRY-630", "TRF-OIL-1000"}
    assert compare_in_family(wanted, "LV_BREAKER", products).status == "EVIDENCE_FOUND"


@pytest.mark.parametrize("family", [None, "OTHER"])
def test_an_unknown_family_is_not_compared(family):
    """계열을 모르면 비교하지 않고 근거 부족이다(trace에 family가 남는다). 지정 모델 요구는 지정품 판정 그대로다."""
    products = load_own_products()
    wanted = Requirement(
        clause_no="2.1",
        item="정격전류",
        attribute="rated_current",
        value="400",
        unit="A",
        operator="GTE",
    )
    outcome = compare_in_family(wanted, family, products)
    assert (outcome.status, outcome.candidates, outcome.unresolved) == (
        "INSUFFICIENT_EVIDENCE",
        [],
        ["요구 장비 계열 확인"],
    )
    assert outcome.search_trace.filters == [f"family={family or 'NONE'}"]
    designated = wanted.model_copy(update={"designated_model": "SEL-751A"})
    assert compare_in_family(designated, family, products).deficiency_cause == "DESIGNATED_MODEL"


def test_a_shortfall_under_a_different_condition_is_conditional_not_a_gap():
    """KAN-199: 미달이어도 조건이 다르면 사양 차이로 확정하지 않는다. 조건이 같을 때만 SPEC_GAP이다."""
    trace = SearchTrace(filters=["family=TRANSFORMER"], documents_searched=1, search_failed=False)

    def product(temperature):
        fact = SpecFact(
            product_code="X-1",
            attribute="impedance",
            value="5",
            unit="%",
            conditions=[Condition(name="reference_temperature", value=temperature, unit="°C")],
            evidence_id="DS-X",
            section="2",
        )
        return Product(
            product_code="X-1",
            family="TRANSFORMER",
            lifecycle_status="ACTIVE",
            standards=[],
            certifications=[],
            spec_facts=[fact],
        )

    wanted = Requirement(
        clause_no="2.5.N",
        item="임피던스",
        attribute="impedance",
        value="5.75",
        unit="%",
        operator="GTE",
        conditions=[Condition(name="reference_temperature", value="85", unit="°C")],
    )
    other = compare(wanted, [product("75")], trace)
    assert (other.status, other.candidates[0].result, other.deficiency_cause) == (
        "CONDITIONAL",
        "DIFFERS",
        None,
    )
    assert "값이 미달이나 조건이 달라 확인 필요" in other.reason
    assert "reference_temperature" in other.unresolved[0]
    same = compare(wanted, [product("85")], trace)
    assert (same.status, same.deficiency_cause) == ("SPEC_GAP", "RATING")


@pytest.mark.parametrize(
    ("current", "have", "want", "need", "status", "result", "peak", "note"),
    [
        # i2t-basis.md 표 5행. 짧은 시간 값의 추정 미달은 확정하지 않는다(실제 정격이 I²t보다 높다).
        ("85", "1", "65", "3", "CONDITIONAL", "DIFFERS", False, "49.1 kA"),
        ("63", "1", "40", "3", "CONDITIONAL", "DIFFERS", False, "36.4 kA"),
        # 3 s를 넘으면 추정하지 않고 조건 차이다.
        ("25", "1", "10", "5", "CONDITIONAL", "MEETS", False, None),
        # 추정 충족은 조건부이고 정격 피크 확인이 남는다.
        ("25", "3", "30", "2", "CONDITIONAL", "MEETS", True, "30.6 kA"),
        # B-1(기획서 기대 SPEC_GAP): 긴 시간 값을 올려도 모자라면 미달이다.
        ("25", "3", "38", "2", "SPEC_GAP", "DIFFERS", False, "30.6 kA"),
        # 경계: 53.68 kA·1 s → 3 s 30.99… kA < 31은 짧은 시간 값이라 확정하지 않는다(문구만 반올림).
        ("53.68", "1", "31", "3", "CONDITIONAL", "DIFFERS", False, "31.0 kA"),
    ],
)
def test_short_time_current_estimates_follow_the_direction(
    current, have, want, need, status, result, peak, note
):
    """지속시간 추정은 3 s까지만 쓰고, 방향에 따라 판정이 다르다(열 등가 추정, 시험값 아님)."""
    fact = SpecFact(
        product_code="X-1",
        attribute="short_time_withstand_current",
        value=current,
        unit="kA",
        conditions=[Condition(name="duration", value=have, unit="s")],
        evidence_id="DS-X",
        section="2",
    )
    product = Product(
        product_code="X-1",
        family="MV_EQUIP",
        lifecycle_status="ACTIVE",
        standards=[],
        certifications=[],
        spec_facts=[fact],
    )
    wanted = Requirement(
        clause_no="2.4",
        item="단시간 내전류",
        attribute="short_time_withstand_current",
        value=want,
        unit="kA",
        operator="GTE",
        conditions=[Condition(name="duration", value=need, unit="s")],
    )
    trace = SearchTrace(filters=["family=MV_EQUIP"], documents_searched=1, search_failed=False)
    outcome = compare(wanted, [product], trace)
    assert (outcome.status, outcome.candidates[0].result) == (status, result)
    if note:
        assert (
            f"열 등가 추정(I²t), 시험값 아님: {current} kA·{have} s → {need} s {note}"
            in outcome.reason
        )
    else:
        assert "추정" not in outcome.reason and "duration" in outcome.unresolved[0]
    assert ("정격 피크 확인" in outcome.unresolved) is peak
    # 후보 원래 값·조건은 그대로 보인다(추정값은 사유에만).
    assert (outcome.candidates[0].our_value, outcome.candidates[0].our_conditions) == (
        current,
        fact.conditions,
    )


def test_a_rating_at_the_required_duration_is_not_overridden_by_an_estimate():
    """요구 지속시간의 실제 값이 미달이면 다른 시간 값의 추정으로 덮지 않는다."""

    def rated(value, seconds):
        return SpecFact(
            product_code="X-1",
            attribute="short_time_withstand_current",
            value=value,
            unit="kA",
            conditions=[Condition(name="duration", value=seconds, unit="s")],
            evidence_id="DS-X",
            section="2",
        )

    product = Product(
        product_code="X-1",
        family="MV_EQUIP",
        lifecycle_status="ACTIVE",
        standards=[],
        certifications=[],
        spec_facts=[rated("85", "1"), rated("42", "3")],
    )
    wanted = Requirement(
        clause_no="2.4",
        item="단시간 내전류",
        attribute="short_time_withstand_current",
        value="65",
        unit="kA",
        operator="GTE",
        conditions=[Condition(name="duration", value="3", unit="s")],
    )
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    assert compare(wanted, [product], trace).status == "SPEC_GAP"


@pytest.mark.parametrize(
    ("kwargs", "check", "cause"),
    [
        ({"task": "field testing"}, "수행 주체 확인(자사/협력사)", "TASK"),
        # task 칸이 비어도 kind가 TASK면 같은 규칙이다.
        ({"kind": "TASK"}, "수행 주체 확인(자사/협력사)", "TASK"),
        ({"kind": "DOCUMENT"}, "제출 자료 준비 확인", None),
        # 문서 요구가 task 칸을 채워도 문서 요구다(kind가 먼저다).
        ({"kind": "DOCUMENT", "task": "submit drawings"}, "제출 자료 준비 확인", None),
    ],
)
@pytest.mark.parametrize("family", [None, "OTHER"])
def test_task_and_document_requirements_are_decided_internally(kwargs, check, cause, family):
    """과업·문서 요구는 계열 검사 없이 내부 확인으로 간다. 고객에게 묻지 않고 과업만 보완 경로가 있다."""
    wanted = Requirement(clause_no="3.2", item="현장 시험", attribute="OTHER", **kwargs)
    outcome = compare_in_family(wanted, family, load_own_products())
    assert "계열" not in outcome.reason and "연결하지 못해" not in outcome.reason
    assert (outcome.status, outcome.next_owner, outcome.deficiency_cause) == (
        "INSUFFICIENT_EVIDENCE",
        "TECH",
        cause,
    )
    assert outcome.unresolved == [check] and check in outcome.reason
    paths = [o.path for o in options_for(outcome, "ALLOWED").options]
    assert paths == (["OWN_DEVELOPMENT", "TASK_SUBCONTRACT"] if cause else [])


@pytest.mark.parametrize("kind", ["TASK", "DOCUMENT"])
@pytest.mark.parametrize("family", [None, "OTHER"])
def test_a_valued_task_or_document_without_a_family_is_still_an_internal_check(kind, family):
    """값과 속성이 있어도 계열이 없으면 견줄 후보가 없다. '자사 후보 없음'이 아니라 내부 확인이다."""
    wanted = Requirement(
        clause_no="3.2",
        item="시험 전압",
        attribute="power_frequency_withstand",
        value="28",
        unit="kV",
        operator="GTE",
        kind=kind,
    )
    outcome = compare_in_family(wanted, family, load_own_products())
    assert (outcome.status, outcome.next_owner) == ("INSUFFICIENT_EVIDENCE", "TECH")
    assert "후보를 찾지 못함" not in outcome.reason
    assert outcome.deficiency_cause == ("TASK" if kind == "TASK" else None)
    # 계열이 정해지면 같은 값으로 실제 비교한다(후보가 있으면 내부 확인으로 가지 않는다).
    known = compare_in_family(wanted, "MV_EQUIP", load_own_products())
    assert known.candidates and "수행 주체" not in known.reason


@pytest.mark.parametrize(
    ("attribute", "value"), [("OTHER", None), (None, None), ("OTHER", "3 days"), ("bil", None)]
)
def test_a_task_without_a_comparable_value_asks_who_performs_it(attribute, value):
    """비교할 값이 없는 수행 과업은 고객 질문이 아니라 내부 수행 주체 확인이고, 자사 수행 → 위탁 경로가 나온다."""
    wanted = Requirement(
        clause_no="3.2", item="현장 시험", attribute=attribute, value=value, task="field testing"
    )
    outcome = compare_in_family(wanted, None, load_own_products())
    assert (outcome.status, outcome.next_owner, outcome.deficiency_cause) == (
        "INSUFFICIENT_EVIDENCE",
        "TECH",
        "TASK",
    )
    assert outcome.unresolved == ["수행 주체 확인(자사/협력사)"] and outcome.candidates == []
    allowed = options_for(outcome, "ALLOWED")
    assert [(o.path, o.rank, o.gate.requires) for o in allowed.options] == [
        ("OWN_DEVELOPMENT", 2, "NONE"),
        ("TASK_SUBCONTRACT", 3, "OWN_DEVELOPMENT_CHECK"),
    ]
    forbidden = options_for(outcome, "FORBIDDEN")
    assert [(o.path, o.allowed) for o in forbidden.options] == [
        ("OWN_DEVELOPMENT", True),
        ("TASK_SUBCONTRACT", False),
    ]
    # R&D가 자사 수행 가능이라고 답하면 위탁은 막힌다(다른 원인과 같은 게이트).
    feasible = options_for(outcome, "ALLOWED", rnd_check="CONFIRMED_FEASIBLE")
    assert [o.allowed for o in feasible.options] == [True, False]


def test_a_task_with_a_real_comparison_and_other_insufficient_results_keep_their_paths():
    """비교할 값이 있는 과업은 기존 판정(사양 차이 → TASK), 원인 없는 근거 부족은 경로가 없다."""
    linked = Requirement(
        clause_no="3.1",
        item="현장 시운전",
        attribute="rated_max_voltage",
        value="15",
        unit="kV",
        operator="GTE",
        task="commissioning",
    )
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    assert compare(linked, [switchgear("12")], trace).status == "SPEC_GAP"
    # 원인도 같은 판단을 쓴다: task 칸이 비어도 kind TASK의 사양 차이는 TASK다.
    by_kind = linked.model_copy(update={"task": None, "kind": "TASK"})
    assert compare(by_kind, [switchgear("12")], trace).deficiency_cause == "TASK"
    no_value = Requirement(clause_no="2.1", item="정격", attribute="bil")
    outcome = compare(no_value, [switchgear("12")], trace)
    assert (outcome.next_owner, outcome.deficiency_cause) == ("CUSTOMER", None)
    assert options_for(outcome, "ALLOWED").options == []
    # 미정 표시는 과업이어도 고객 회신이 먼저다.
    placeholder = no_value.model_copy(update={"task": "testing", "value": "<Insert value>"})
    assert compare(placeholder, [switchgear("12")], trace).next_owner == "CUSTOMER"
    # 인증 요구는 과업 칸이 있어도 인증 경로다.
    certification = no_value.model_copy(
        update={"task": "testing", "certification_standard": "UL 1558", "attribute": None}
    )
    assert "수행 주체" not in compare(certification, [switchgear("12")], trace).reason


def test_certifications_and_designated_models_keep_their_order_for_task_requirements():
    """인증 요구는 과업 칸이 있어도 계열 검사를 지나고, 지정 모델은 그대로 지정품 판정이다."""
    products = load_own_products()
    certification = Requirement(
        clause_no="2.1", item="등록", certification_standard="UL 1558", task="testing"
    )
    assert "계열" in compare_in_family(certification, None, products).reason
    designated = Requirement(clause_no="2.2", item="계전기", designated_model="X-100", kind="TASK")
    outcome = compare_in_family(designated, None, products)
    assert outcome.deficiency_cause == "DESIGNATED_MODEL"


@pytest.mark.parametrize("value", ["<Insert value>", "< insert kV rating >", "TBD", "[ ]", "____"])
def test_a_placeholder_value_is_left_out_of_the_judgement(value):
    """미정 표시는 값이 아니다. 비교하지 않고 고객 회신을 받는 근거 부족으로 둔다."""
    wanted = Requirement(
        clause_no="2.4",
        item="단시간 내전류",
        attribute="rated_max_voltage",
        value=value,
        unit="kV",
        operator="EQ",
    )
    outcome = compare_in_family(wanted, "MV_EQUIP", load_own_products())
    assert (outcome.status, outcome.next_owner, outcome.candidates) == (
        "INSUFFICIENT_EVIDENCE",
        "CUSTOMER",
        [],
    )
    assert "미정" in outcome.reason and outcome.unresolved == ["요구값 확정(고객 회신)"]


@pytest.mark.parametrize("family", ["TRANSFORMER", "OTHER", None])
def test_a_basis_of_design_product_asks_the_customer_about_equals_without_designation(family):
    """결함 13: 설계 기준은 지정이 아니다. 계열·속성이 없어도 조건부이고 지정품 경로로 가지 않는다."""
    wanted = Requirement(
        clause_no="2.5",
        item="변압기",
        attribute="OTHER",
        value="Model X",
        operator="TEXT",
        basis_of_design=True,
    )
    outcome = compare_in_family(wanted, family, load_own_products())
    assert (outcome.status, outcome.next_owner) == ("CONDITIONAL", "CUSTOMER")
    assert outcome.deficiency_cause is None and "지정 모델" not in outcome.reason
    assert "Model X" in outcome.reason
    assert options_for(outcome, "ALLOWED").options == []


def test_a_designated_model_keeps_its_procurement_path():
    """대체 불가 지정은 그대로 지정품이다. 설계 기준과 섞이지 않는다."""
    wanted = Requirement(clause_no="2.5", item="계전기", designated_model="Model X")
    outcome = compare_in_family(wanted, "OTHER", load_own_products())
    assert (outcome.status, outcome.deficiency_cause) == ("CONDITIONAL", "DESIGNATED_MODEL")
    assert "지정 모델 Model X" in outcome.reason


def short_time(unit="kA", operator="GTE", duration_unit="s"):
    return Requirement(
        clause_no="2.4",
        item="단시간 내전류",
        attribute="short_time_withstand_current",
        value="38",
        unit=unit,
        operator=operator,
        conditions=[Condition(name="duration", value="2", unit=duration_unit)],
    )


def rated_product(attribute, value, unit, duration=None):
    conditions = [Condition(name="duration", value=duration, unit="s")] if duration else None
    return Product(
        product_code="X-1",
        family="MV_EQUIP",
        lifecycle_status="ACTIVE",
        standards=[],
        certifications=[],
        spec_facts=[
            SpecFact(
                product_code="X-1",
                attribute=attribute,
                value=value,
                unit=unit,
                conditions=conditions,
                evidence_id="DS-X",
                section="2",
            )
        ],
    )


@pytest.mark.parametrize(
    ("unit", "duration_unit"),
    [
        ("kA", "s"),
        ("kA SYM RMS", "s"),
        ("kA rms, symmetrical", "seconds"),
        ("kA (sym)", "sec"),
        ("kA r.m.s.", "s"),
    ],
)
def test_unit_qualifiers_do_not_stop_the_duration_estimate(unit, duration_unit):
    """결함 11: 양을 바꾸지 않는 수식어나 초 표기 때문에 비교·추정이 멈추지 않는다. B-1은 어느 표기든 사양 차이다."""
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    outcome = compare(
        short_time(unit, duration_unit=duration_unit),
        [rated_product("short_time_withstand_current", "25", "kA", "3")],
        trace,
    )
    assert outcome.status == "SPEC_GAP" and "30.6 kA" in outcome.reason
    assert not any("단위" in item for item in outcome.unresolved)


@pytest.mark.parametrize("unit", ["kA peak", "kA asym", "kA crest", "A"])
def test_units_that_change_the_quantity_are_not_stripped(unit):
    """peak·asym·crest와 다른 크기는 다른 양이다. 같다고도 다르다고도 하지 않는다(단위 확인)."""
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    outcome = compare(
        short_time(unit), [rated_product("short_time_withstand_current", "25", "kA", "2")], trace
    )
    assert (outcome.status, outcome.candidates[0].result) == ("CONDITIONAL", "NOT_COMPARABLE")
    assert any("단위 확인" in item for item in outcome.unresolved)


def test_condition_units_ignore_case_after_the_unit_key():
    """조건 단위는 대소문자로 크기가 갈리지 않는다(#347 리뷰). 'HZ'와 'Hz'는 조건 차이가 아니다."""
    wanted = Requirement(
        clause_no="2.4",
        item="BIL",
        attribute="bil",
        value="95",
        unit="kV",
        operator="GTE",
        conditions=[Condition(name="frequency", value="50", unit="HZ")],
    )
    plain = rated_product("bil", "95", "kV")
    fact = plain.spec_facts[0].model_copy(
        update={"conditions": [Condition(name="frequency", value="50", unit="Hz")]}
    )
    product = plain.model_copy(update={"spec_facts": [fact]})
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    assert compare(wanted, [product], trace).status == "EVIDENCE_FOUND"
    # 'S'는 지멘스다. 초로 맞추지 않는다(#347 리뷰).
    conductance = wanted.model_copy(
        update={"attribute": "rated_power", "unit": "S", "conditions": []}
    )
    result = compare(conductance, [rated_product("rated_power", "95", "s")], trace)
    assert result.candidates[0].result == "NOT_COMPARABLE"
    # 값 단위는 대소문자가 크기를 가른다(mW·MW). 같은 양으로 보지 않는다.
    power = wanted.model_copy(update={"attribute": "rated_power", "unit": "mW", "conditions": []})
    result = compare(power, [rated_product("rated_power", "95", "MW")], trace)
    assert result.candidates[0].result == "NOT_COMPARABLE"


def test_the_operator_decides_a_fixed_rating_and_a_minimum():
    """결함 9: 정해진 값(EQ)은 더 높은 정격으로 충족되지 않고, 최소 요구(GTE)는 충족된다."""
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    product = rated_product("bil", "125", "kV")

    def wanted(operator):
        return Requirement(
            clause_no="2.4",
            item="BIL",
            attribute="bil",
            value="95",
            unit="kV",
            operator=operator,
        )

    assert compare(wanted("EQ"), [product], trace).status == "SPEC_GAP"
    assert compare(wanted("GTE"), [product], trace).status == "EVIDENCE_FOUND"


def test_an_unlinked_attribute_is_not_compared_but_certifications_are():
    """attribute_key가 OTHER(사양 항목에 연결 못 함)면 계열이 맞아도 비교하지 않는다. 인증 요구는 규격으로 비교한다."""
    products = load_own_products()
    unlinked = Requirement(
        clause_no="2.5", item="권선 재질", attribute="OTHER", value="copper", operator="TEXT"
    )
    outcome = compare_in_family(unlinked, "TRANSFORMER", products)
    assert (outcome.status, outcome.candidates, outcome.unresolved) == (
        "INSUFFICIENT_EVIDENCE",
        [],
        ["요구 속성(사양 항목) 확인"],
    )
    certification = Requirement(
        clause_no="2.2", item="UL 1561", certification_standard="UL 1561", operator="TEXT"
    )
    assert compare_in_family(certification, "TRANSFORMER", products).candidates


@pytest.mark.parametrize("operator", ["LTE", "EQ"])
def test_short_time_current_is_converted_only_for_a_minimum(operator):
    """I²t 환산은 최소 요구(GTE)에만 쓴다. 다른 연산자는 환산 없이 기존 판정(조건 차이)이다."""
    fact = SpecFact(
        product_code="X-1",
        attribute="short_time_withstand_current",
        value="25",
        unit="kA",
        conditions=[Condition(name="duration", value="3", unit="s")],
        evidence_id="DS-X",
        section="2",
    )
    product = Product(
        product_code="X-1",
        family="MV_EQUIP",
        lifecycle_status="ACTIVE",
        standards=[],
        certifications=[],
        spec_facts=[fact],
    )
    wanted = Requirement(
        clause_no="2.4",
        item="단시간 내전류",
        attribute="short_time_withstand_current",
        value="38",
        unit="kA",
        operator=operator,
        conditions=[Condition(name="duration", value="2", unit="s")],
    )
    trace = SearchTrace(filters=["family=MV_EQUIP"], documents_searched=1, search_failed=False)
    outcome = compare(wanted, [product], trace)
    assert "환산" not in outcome.reason and "duration" in outcome.unresolved[0]


def test_demo_b1_short_time_current_stays_a_spec_gap_for_the_own_mv_products():
    """기획서 B-1(2.4 단시간 내전류 38 kA·2 s, 기대 SPEC_GAP): 자사 MV 후보 25 kA·3 s는 환산해도 미달이다."""
    wanted = Requirement(
        clause_no="2.4",
        item="단시간 내전류 38kA, 2s",
        attribute="short_time_withstand_current",
        value="38",
        unit="kA",
        operator="GTE",
        conditions=[Condition(name="duration", value="2", unit="s")],
    )
    outcome = compare_in_family(wanted, "MV_EQUIP", load_own_products())
    [b1] = [
        r
        for c in json.loads(DEMO_CASES.read_text())["cases"]
        if c["case"] == "B-1"
        for r in c["requirements"]
        if r["attribute"] == "short_time_withstand_current"
    ]
    assert outcome.status == b1["expected_status"] == "SPEC_GAP"


def own(code):
    """자사 제품 하나.

    Args:
        code: 제품 코드.

    Returns:
        Product: own_spec_facts의 그 제품.
    """
    [product] = [p for p in load_own_products() if p.product_code == code]
    return product


def test_a_met_value_under_another_standard_system_is_conditional():
    """KAN-199: 요구 UL interrupting capacity를 IEC Icu 근거로 충족 판정하지 않는다(규격 체계 다름 → 조건부)."""
    trace = SearchTrace(filters=["family=LV_BREAKER"], documents_searched=1, search_failed=False)
    wanted = Requirement(
        clause_no="2.6",
        item="차단 용량",
        attribute="breaking_capacity_icu",
        value="10",
        unit="kA",
        operator="GTE",
        standard_system="UL",
    )
    iec_only = compare(wanted, [own("MCCB-ABS-103c")], trace)
    assert iec_only.status == "CONDITIONAL"
    assert "규격 체계 다름: 요구 UL, 근거 IEC 60947-2, 시험 규격 확인" in iec_only.unresolved
    # 체계 표시가 없는 요구는 지금처럼 충족이다.
    plain = wanted.model_copy(update={"standard_system": "NONE"})
    assert compare(plain, [own("MCCB-ABS-103c")], trace).status == "EVIDENCE_FOUND"
    # UL이 -U 변형 모델 인증에만 있으면(certifications.variant) 근거 체계에 넣지 않는다.
    variant = compare(wanted, [own("MCCB-ABS-403c")], trace)
    assert variant.status == "CONDITIONAL"
    assert "UL 체계는 변형 모델에만 있음: 변형 모델 값 확인" in variant.unresolved
    assert any(
        u.startswith("규격 체계 다름: 요구 UL, 근거 IEC 60947-2,") for u in variant.unresolved
    )


def test_a_same_system_product_is_not_downgraded():
    """변형 표시 없이 UL 인증이 있는 제품(CONT-MC-32a: IEC·UL 60947-4-1)은 UL 요구에 체계 차이로 낮추지 않는다."""
    product = own("CONT-MC-32a")
    fact = product.spec_facts[0]
    wanted = Requirement(
        clause_no="2.7",
        item="접촉기",
        attribute=fact.attribute,
        value=fact.value,
        unit=fact.unit,
        operator="TEXT" if fact.unit is None else "EQ",
        conditions=list(fact.conditions or []),
        standard_system="UL",
    )
    trace = SearchTrace(filters=["family=CONTROL"], documents_searched=1, search_failed=False)
    assert not any("규격 체계" in u for u in compare(wanted, [product], trace).unresolved)


def test_demo_b1_voltage_gap_stays_a_gap_under_another_standard_system():
    """기획서 B-1(15 kV 요구, 요구 IEEE·근거 IEC 62271-200, 자사 SWGR-MV-12kV 12 kV): 물리 정격 미달은 체계가 달라도
    사양 차이다(규격 체계 규칙은 충족만 낮춘다)."""
    wanted = Requirement(
        clause_no="2.4",
        item="정격 최대전압 15kV",
        attribute="rated_max_voltage",
        value="15",
        unit="kV",
        operator="GTE",
        standard_system="IEEE",
    )
    trace = SearchTrace(filters=["family=MV_EQUIP"], documents_searched=1, search_failed=False)
    outcome = compare(wanted, [own("SWGR-MV-12kV")], trace)
    [b1] = [
        r
        for c in json.loads(DEMO_CASES.read_text())["cases"]
        if c["case"] == "B-1"
        for r in c["requirements"]
        if r["attribute"] == "rated_max_voltage"
    ]
    assert outcome.status == b1["expected_status"] == "SPEC_GAP"
    assert not any("규격 체계" in u for u in outcome.unresolved)


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
    # The listed mature candidate shows the same downgrade as when it stood alone.
    assert (both.candidates[0].status, both.candidates[0].unresolved) == (
        "CONDITIONAL",
        ["수명주기 MATURE: 공급 지속 여부 확인"],
    )


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


def test_every_held_certificate_of_the_standard_is_judged_and_the_best_counts():
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    listed = Requirement(
        clause_no="2.1",
        item="UL 1558 Listed",
        certification_standard="UL 1558",
        certification_listed=True,
    )

    def holding(*certs):
        return switchgear("15").model_copy(update={"certifications": list(certs)})

    report = Certification(standard="UL 1558", kind="TEST_REPORT", evidence_id="DS-1")
    variant = Certification(standard="UL 1558", kind="LISTED", evidence_id="DS-2", variant="-U")
    base = Certification(standard="UL 1558", kind="LISTED", evidence_id="DS-3")
    assert compare(listed, [holding(report, base)], trace).status == "EVIDENCE_FOUND"
    assert compare(listed, [holding(variant, base)], trace).status == "EVIDENCE_FOUND"
    assert compare(listed, [holding(report, variant)], trace).status == "CONDITIONAL"


def test_options_come_in_path_rank_order_with_a_gate_on_partner_paths():
    """명세 v1.3 §5.2: 자사(1) → 자사 개발(2) → 협력사 계열(3) → 지정품(4) → 없음(9)."""
    result = options_for(demo_a()[4], "ALLOWED", rnd_check="PENDING")
    assert [(o.path, o.rank) for o in result.options] == [
        ("OTHER_OWN_PRODUCT", 1),
        ("OWN_DEVELOPMENT", 2),
        ("EXTERNAL_PRODUCT", 3),
    ]
    gates = {o.path: (o.gate.requires, o.gate.status) for o in result.options}
    assert gates == {
        "OTHER_OWN_PRODUCT": ("NONE", "PENDING"),
        "OWN_DEVELOPMENT": ("NONE", "PENDING"),
        "EXTERNAL_PRODUCT": ("OWN_DEVELOPMENT_CHECK", "PENDING"),
    }


@pytest.mark.parametrize(
    "rnd_check",
    ["NOT_REQUIRED", "PENDING", "CONFIRMED_NOT_FEASIBLE", "CONFIRMED_SCHEDULE_MISMATCH", "WAIVED"],
)
def test_only_a_confirmed_own_development_blocks_partner_paths(rnd_check):
    result = options_for(demo_a()[4], "ALLOWED", rnd_check=rnd_check)
    assert all(o.allowed for o in result.options)


def test_partner_paths_are_blocked_once_rnd_confirms_own_development():
    """자사 개발이 되는데 협력사로 가지 않는다(OWN_DEVELOPMENT_FEASIBLE)."""
    result = options_for(demo_a()[4], "ALLOWED", rnd_check="CONFIRMED_FEASIBLE")
    assert [(o.path, o.allowed, o.blocked_reason) for o in result.options] == [
        ("OTHER_OWN_PRODUCT", True, None),
        ("OWN_DEVELOPMENT", True, None),
        ("EXTERNAL_PRODUCT", False, "OWN_DEVELOPMENT_FEASIBLE"),
    ]
    # Every partner-family path is gated, the alternative proposal included.
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    form = compare(
        Requirement(
            clause_no="2.4",
            item="형식",
            attribute="rated_max_voltage",
            value="15",
            unit="kV",
            operator="GTE",
        ),
        [switchgear("12")],
        trace,
    ).model_copy(update={"deficiency_cause": "FORM"})
    gated = options_for(form, "ALLOWED", rnd_check="CONFIRMED_FEASIBLE")
    assert [(o.path, o.allowed) for o in gated.options] == [
        ("OWN_DEVELOPMENT", True),
        ("EXTERNAL_PRODUCT", False),
        ("ALTERNATIVE_SPEC", False),
    ]


def test_commercial_and_designated_alternatives_are_not_gated_by_rnd():
    """#299 리뷰 P3: 보증 같은 계약 조건과 지정품의 대체 제안은 자사 개발과 무관하다."""
    warranty = Requirement(
        clause_no="1.6",
        item="보증 60개월",
        attribute="warranty_months",
        value="60",
        unit="month",
        operator="GTE",
    )
    products, trace = master_filter(load_own_products(), "TRANSFORMER")
    gap = compare(warranty, products, trace)
    result = options_for(gap, "ALLOWED", rnd_check="CONFIRMED_FEASIBLE")
    assert [(o.path, o.allowed, o.gate.requires) for o in result.options] == [
        ("OWN_TERMS_REVIEW", True, "NONE"),
        ("ALTERNATIVE_SPEC", True, "NONE"),
    ]
    designated = options_for(designated_outcome(), "ALLOWED", rnd_check="CONFIRMED_FEASIBLE")
    alternative = next(o for o in designated.options if o.path == "ALTERNATIVE_SPEC")
    assert (alternative.allowed, alternative.gate.requires) == (True, "NONE")


def test_every_demo_judgement_links_back_to_the_rfp_clause():
    """완료 조건 "원문·제품 근거 링크": 판정마다 요구의 원문 조항·인용(제품 쪽은 후보 근거)을 돌려준다."""
    outcomes = demo_a()
    assert all(o.origin is not None and o.origin.document == DOC14 for o in outcomes)
    quoted = [o for o in outcomes if o.origin.quote]
    # 1차 전압·용량은 원문에 값이 없어(도면 참조) 인용 대신 이유를 남긴다.
    assert len(quoted) == 13
    missing = outcomes[5].origin
    assert (missing.quote, "도면 참조" in missing.note) == (None, True)
    assert "aluminum" in outcomes[7].origin.quote and outcomes[7].origin.clause_no == "2.5.L"
    # Product-side evidence stays on the candidates.
    assert outcomes[0].candidates[0].evidence[0].document == "DS-TRF-DRY-630"


def test_all_23_own_products_load_with_their_family_and_warranty():
    """KAN-186 자사 사양 23종(scripts/rfp_own_spec_facts.py가 만든 파일)."""
    products = load_own_products()
    assert len(products) == 23 and len({p.product_code for p in products}) == 23
    assert all(any(f.attribute == "warranty_months" for f in p.spec_facts) for p in products)
    switchgear_12 = next(p for p in products if p.product_code == "SWGR-MV-12kV")
    attributes = {f.attribute for f in switchgear_12.spec_facts}
    assert {
        "rated_max_voltage",
        "bil",
        "short_time_withstand_current",
        "internal_arc_classification",
    } <= attributes


def test_a_certification_mark_alone_is_conditional_not_met():
    """기획서 2.3: 사양표에 인증명만 있으면 해당 프로젝트 인증 충족으로 확정하지 않는다."""
    trace = SearchTrace(filters=[], documents_searched=1, search_failed=False)
    marked = switchgear("15").model_copy(
        update={
            "certifications": [
                Certification(standard="UL", kind="MARK", evidence_id="product_master")
            ]
        }
    )
    for listed in (False, True):
        outcome = compare(
            Requirement(
                clause_no="2.2", item="UL", certification_standard="UL", certification_listed=listed
            ),
            [marked],
            trace,
        )
        assert outcome.status == "CONDITIONAL"
        assert "등록·시험 구분 미기재" in outcome.reason
        assert outcome.unresolved[0].startswith("UL 인증 종류")
