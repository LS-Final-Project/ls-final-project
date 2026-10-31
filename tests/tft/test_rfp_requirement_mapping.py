"""추출 요구 → 비교·등급 입력 매핑(KAN-199). 모델·DB를 부르지 않는다.

비교·등급은 추출 v3의 attribute_key(자사 사양 어휘)를 쓰고, 동의어 표는 없다(#324 표는 v3 연결로 대체해 지웠다).
"""

from uuid import UUID

import pytest

from backend.api.tft.partners.loader import (
    PROFILE_FAMILIES,
    load_document_pools,
    load_profiles,
)
from backend.api.tft.rfp.comparison import Requirement, compare_in_family, load_own_products
from backend.api.tft.worker_processor import (
    candidate_pool,
    grading_groups,
    integration_of,
    to_comparison,
    to_grading,
)
from backend.engine.rfp.requirements import ExtractedRequirement
from backend.engine.rfp.vocabulary import EQUIPMENT_FAMILIES


def requirement(kind, attribute, attribute_key="OTHER"):
    return ExtractedRequirement(
        clause_no="2.5.C",
        quote="q",
        kind=kind,
        equipment="transformer",
        task=None,
        attribute=attribute,
        value="95",
        unit="kV",
        operator="GTE",
        conditions=[],
        standard_system="IEEE",
        necessity="MANDATORY",
        designated_model=None,
        attribute_key=attribute_key,
        equipment_family="TRANSFORMER",
    )


def test_comparison_input_uses_the_vocabulary_but_certifications_keep_the_standard():
    """비교 입력 속성은 추출의 attribute_key(자사 어휘, KAN-199)이고, 인증 요구는 추출 value의 규격 원문을 쓴다."""
    spec = to_comparison(requirement("SPEC", "basic impulse insulation level", "bil"))
    assert (spec.attribute, spec.certification_standard) == ("bil", None)
    # 연결하지 못한 속성은 OTHER 그대로다(동의어 표로 추측하지 않는다). compare_in_family가 비교하지 않는다.
    assert to_comparison(requirement("SPEC", "basic impulse insulation level")).attribute == "OTHER"
    # 규격은 추출 value에 있다(attribute는 이름표). value가 없으면 attribute를 쓴다.
    labelled = requirement("COMPLIANCE", "standard compliance").model_copy(
        update={"value": "IEEE C37.121"}
    )
    compliance = to_comparison(labelled)
    assert (compliance.attribute, compliance.certification_standard) == (None, "IEEE C37.121")
    bare = requirement("COMPLIANCE", "UL 1561").model_copy(update={"value": None})
    assert to_comparison(bare).certification_standard == "UL 1561"
    # 요구 종류를 그대로 넘긴다(과업·문서 판단은 comparison._work_kind 한 곳이다).
    assert to_comparison(requirement("TASK", "field testing")).kind == "TASK"
    basis = requirement("SPEC", "relay").model_copy(update={"basis_of_design": True})
    assert to_comparison(basis).basis_of_design
    tap = requirement("SPEC", "taps", "tap").model_copy(
        update={"value": None, "operator": "TEXT", "canonical_value": "±2x2.5%"}
    )
    assert (to_comparison(tap).canonical_value, to_comparison(tap).value) == ("±2x2.5%", "±2x2.5%")
    graded = to_grading(UUID(int=1), tap.model_copy(update={"equipment": "transformer"}))
    assert graded is not None and graded.canonical_value == "±2x2.5%"


def test_grading_input_treats_compliance_as_a_certification_like_the_comparison():
    """협력사 등급 입력도 COMPLIANCE는 인증 요구다(attribute='certification', 규격은 value 우선, 없으면 attribute).

    그래야 grade_requirement가 사양이 아니라 certifications 배열로 판정한다(KAN-199).
    """
    from datetime import date
    from uuid import UUID

    from backend.api.tft.partners.grading import RfpContext, grade_requirement
    from backend.api.tft.partners.loader import load_profiles
    from backend.api.tft.worker_processor import to_grading

    labelled = requirement("COMPLIANCE", "standard compliance").model_copy(
        update={"value": "UL 347", "unit": None, "operator": "TEXT"}
    )
    grade_input = to_grading(UUID(int=1), labelled)
    assert grade_input is not None
    assert (grade_input.attribute, grade_input.value) == ("certification", "UL 347")
    bare = requirement("COMPLIANCE", "UL 1561").model_copy(update={"value": None})
    assert to_grading(UUID(int=2), bare).value == "UL 1561"
    spec = to_grading(UUID(int=3), requirement("SPEC", "basic impulse insulation level", "bil"))
    assert (spec.attribute, spec.value) == ("bil", "95")
    # 속성을 연결하지 못한 사양 요구(OTHER)는 같은 속성 근거가 없어 등급을 매기지 않는다.
    assert to_grading(UUID(int=4), requirement("SPEC", "material")) is None
    [p_a] = [p for p in load_profiles() if p.candidate_code == "P-A"]
    context = RfpContext(
        manufacturer_list=None,
        off_list_permission="UNSTATED",
        supply_region=None,
        requires_integrator=False,
    )
    row = grade_requirement(p_a, grade_input, context, date(2026, 10, 30))
    checked = [
        *row.mandatory_check.passed,
        *row.mandatory_check.failed,
        *row.mandatory_check.unknown,
    ]
    assert "certification:UL 347" in checked


@pytest.mark.parametrize(
    ("value", "unit", "tolerance", "expected"),
    [
        # '%' 오차는 명목값에 대한 비율이다(값 단위가 %여도): 시연 A 임피던스 5.75% ±10%.
        ("5.75", "%", "±10%", ("RANGE", "5.175~6.325")),
        ("100", "kVA", "+0/-5%", ("RANGE", "95~100")),
        ("100", "kVA", "+10%/-5%", ("RANGE", "95~110")),
        # '%'가 없으면 같은 단위의 절대값이다.
        ("13.8", "kV", "±0.5", ("RANGE", "13.3~14.3")),
        # 읽지 못하면 RANGE에 원래 값: 비교가 '범위를 숫자로 읽을 수 없어 확인 필요'가 된다.
        ("5", "%", "about 10%", ("RANGE", "5")),
        ("5.75", "%", None, ("EQ", "5.75")),
    ],
)
def test_a_nominal_value_with_a_tolerance_becomes_a_range(value, unit, tolerance, expected):
    """KAN-199: 허용 오차는 원문 그대로 받고, 범위 계산은 코드가 한다(EQ 명목값에만)."""
    from backend.api.tft.worker_processor import to_grading, tolerance_range

    item = requirement("SPEC", "impedance", "impedance").model_copy(
        update={"value": value, "unit": unit, "operator": "EQ", "tolerance": tolerance}
    )
    assert tolerance_range(item) == expected
    compared = to_comparison(item)
    assert (compared.operator, compared.value) == expected
    from uuid import UUID

    graded = to_grading(UUID(int=1), item)
    assert (graded.operator, graded.value) == expected


def test_a_tolerance_on_a_minimum_is_not_a_range_and_an_unreadable_one_is_conditional():
    """GTE·LTE에 붙은 허용 오차는 쓰지 않는다. 읽지 못한 허용 오차는 같다고도 다르다고도 하지 않는다(조건부)."""
    from backend.api.tft.rfp.comparison import compare_in_family, load_own_products
    from backend.api.tft.worker_processor import tolerance_range

    minimum = requirement("SPEC", "impedance", "impedance").model_copy(update={"tolerance": "±10%"})
    assert tolerance_range(minimum) == ("GTE", "95")
    unreadable = requirement("SPEC", "impedance", "impedance").model_copy(
        update={"value": "5.75", "unit": "%", "operator": "EQ", "tolerance": "about 10%"}
    )
    outcome = compare_in_family(to_comparison(unreadable), "TRANSFORMER", load_own_products())
    assert {c.result for c in outcome.candidates} == {"NOT_COMPARABLE"}
    assert outcome.status == "CONDITIONAL"
    within = unreadable.model_copy(update={"tolerance": "±10%", "standard_system": "NONE"})
    # 5.175~6.325 % 안의 TRF-DRY-630 6 %. 값은 충족하지만 기준 온도가 요구에 없어 확정하지 않는다.
    met = compare_in_family(to_comparison(within), "TRANSFORMER", load_own_products())
    assert (met.status, met.candidates[0].result) == ("CONDITIONAL", "MEETS")


def test_a_listing_requirement_reaches_both_comparison_and_grading():
    """certification_listed는 자사 비교 입력으로, 협력사 등급에는 grading이 읽는 listing=listed 조건으로 간다."""
    from uuid import UUID

    from backend.api.tft.worker_processor import to_grading

    listed = requirement("COMPLIANCE", "standard compliance").model_copy(
        update={"value": "UL 347", "unit": None, "operator": "TEXT", "certification_listed": True}
    )
    assert to_comparison(listed).certification_listed is True
    graded = to_grading(UUID(int=1), listed)
    assert [(c.name, c.value) for c in graded.conditions] == [("listing", "listed")]
    assert (
        to_comparison(
            listed.model_copy(update={"certification_listed": False})
        ).certification_listed
        is False
    )
    assert (
        to_grading(
            UUID(int=2), listed.model_copy(update={"certification_listed": False})
        ).conditions
        == []
    )


@pytest.mark.parametrize(
    ("titles", "numbers"),
    [
        (["RFP-14 261116.11"], ["14"]),
        (["14 전 RFP-14 시연본"], ["14"]),
        (["RFP-14", "RFP-10 부록"], ["14", "10"]),
        (["RFP-140"], None),
        (["RFP-14a"], None),
        (["XRFP-14"], None),
        (["RFP-99"], None),
        (["합성 규격서"], None),
        (["RFP-14", "합성 규격서"], None),
        ([], None),
    ],
)
def test_the_candidate_pool_reads_the_whole_number_from_document_titles(titles, numbers):
    """번호는 숫자 경계로 읽는다(RFP-140은 14가 아니다). 모르는 문서가 있으면 후보를 줄이지 않는다."""
    pools = load_document_pools()
    expected = None if numbers is None else frozenset().union(*(pools[n] for n in numbers))
    assert candidate_pool(titles) == expected


def test_every_shipped_profile_has_an_equipment_family():
    """표에 없는 후보는 조용히 채점에서 빠지므로 시드 프로필은 모두 표에 있어야 한다."""
    assert {p.product_family for p in load_profiles()} <= set(PROFILE_FAMILIES)
    assert frozenset().union(*PROFILE_FAMILIES.values()) <= EQUIPMENT_FAMILIES


def test_a_document_with_two_families_grades_each_candidate_on_its_own_family():
    """14번 B-1·B-2가 함께 있어도 후보는 기획서 기대 등급이다. 풀 밖·계열 없는 후보는 채점하지 않는다."""
    from backend.api.tft.partners import grading
    from tests.tft.test_partner_grading import B1, B2, CONTEXT, DAY

    profiles = load_profiles()
    pool = frozenset(load_document_pools()["14"])
    targets = [(r, "MV_EQUIP") for r in B1] + [(r, "LV_BREAKER") for r in B2]
    groups = grading_groups(targets, profiles, pool)
    grades = {
        p.candidate_code: grading.grade_candidate(p, mine, CONTEXT, DAY)[0].overall_grade
        for p, mine in groups
    }
    assert grades == {
        "P-A": "REVIEW",
        "P-B": "EXCLUDED",
        "P-C": "REFERENCE_ONLY",
        "L-A": "REVIEW",
        "L-B": "EXCLUDED",
    }
    assert {p.candidate_code for p, _ in grading_groups(targets, profiles, None)} >= {"M-A", "L-C"}


def test_lv_candidates_take_assembly_and_breaker_requirements():
    """저압 배전반 후보는 조립품(UL 1558) 요구로 채점돼 L-B가 제외된다. 고압 후보는 받지 않는다."""
    from backend.api.tft.partners import grading
    from tests.tft.test_partner_grading import B2, DAY, LV_CONTEXT

    profiles = load_profiles()
    pool = frozenset(load_document_pools()["14"])
    assemblies = [r for r in B2 if r.value == "UL 1558"]
    assert assemblies
    targets = [(r, "LV_SWITCHGEAR" if r in assemblies else "LV_BREAKER") for r in B2]
    groups = {p.candidate_code: (p, mine) for p, mine in grading_groups(targets, profiles, pool)}
    assert set(groups) == {"L-A", "L-B"}
    summary, _ = grading.grade_candidate(*groups["L-B"], LV_CONTEXT, DAY)
    assert summary.overall_grade == "EXCLUDED"
    only = grading_groups([(assemblies[0], "LV_SWITCHGEAR")], profiles, pool)
    assert {p.candidate_code for p, _ in only} == {"L-A", "L-B"}


def test_own_products_have_no_lv_switchgear_family():
    """자사 제품이 없는 계열은 후보 없음으로 정직하게 나온다."""
    outcome = compare_in_family(
        Requirement(
            clause_no="2.6.A",
            item="UL 1558",
            attribute="certification",
            value="UL 1558",
            certification_standard="UL 1558",
        ),
        "LV_SWITCHGEAR",
        load_own_products(),
    )
    assert outcome.status == "INSUFFICIENT_EVIDENCE"
    assert outcome.candidates == []


@pytest.mark.parametrize("family", [None, "OTHER", "PLC"])
def test_a_requirement_without_a_candidate_family_is_not_graded(family):
    """계열을 모르거나 맞는 후보가 없는 요구는 채점하지 않는다. 표에 없는 후보도 null 요구에 맞지 않는다."""
    from tests.tft.test_partner_grading import B1

    unknown = load_profiles()[0].model_copy(update={"product_family": "표에 없는 제품군"})
    assert grading_groups([(B1[0], family)], [*load_profiles(), unknown], None) == []


def test_integration_is_decided_from_the_extracted_requirements():
    """조립품 조항이 있으면 통합 책임이 필요하다."""
    plain = requirement("SPEC", "rating", "rated_power")
    marked = plain.model_copy(update={"assembly_clause": True})
    assert not integration_of([plain]).required
    assert integration_of([plain, marked]).required
