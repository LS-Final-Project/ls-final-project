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
        designated_model="Eaton Resibloc",
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


def test_designated_model_and_non_gaps_get_the_right_paths():
    outcomes = demo_a()
    assert outcomes[13].deficiency_cause == "DESIGNATED_MODEL"
    designated = options_for(outcomes[13], "UNSTATED")
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
    asked = options_for(outcomes[13], "UNSTATED", approval_question=True)
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
    designated = options_for(demo_a()[13], "ALLOWED", rnd_check="CONFIRMED_FEASIBLE")
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
