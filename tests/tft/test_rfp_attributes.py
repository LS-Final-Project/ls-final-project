"""추출 속성 → 자사 사양 어휘 정규화(KAN-199). 모델·DB를 부르지 않는다."""

import pytest

from backend.api.tft.rfp.attributes import ALIASES, canonical, own_vocabulary
from backend.api.tft.worker_processor import to_comparison
from backend.engine.rfp.requirements import ExtractedRequirement


def test_every_alias_points_into_the_own_vocabulary():
    assert set(ALIASES.values()) <= own_vocabulary()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Basic Impulse  Insulation Level", "bil"),
        ("rated continuous current", "rated_current"),
        ("Maximum voltage rating", "rated_max_voltage"),
        ("rated short-time withstand current", "short_time_withstand_current"),
        ("forced air cooling capacity increase", "forced_air_capacity_increase"),
        # 어휘 이름 그대로(공백 → 밑줄)
        ("Impedance", "impedance"),
        ("rated current", "rated_current"),
        # 확실하지 않으면 원문 그대로
        ("rated momentary withstand current", "rated momentary withstand current"),
        ("secondary voltage rating", "secondary voltage rating"),
        ("material", "material"),
    ],
)
def test_canonical_maps_only_certain_names(raw, expected):
    assert canonical(raw) == expected


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


def test_an_alias_outside_the_given_vocabulary_keeps_the_original():
    """어휘에서 빠진 이름으로는 바꾸지 않는다(자사 사양 파일이 바뀌어도 없는 속성을 만들지 않게)."""
    assert canonical("basic impulse insulation level", {"impedance"}) == (
        "basic impulse insulation level"
    )


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
