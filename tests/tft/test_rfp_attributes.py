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


def requirement(kind, attribute):
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
    )


def test_comparison_input_uses_the_vocabulary_but_certifications_keep_the_standard():
    """비교 입력 속성은 어휘로 바꾸고, 인증 요구는 추출 value의 규격 원문을 쓴다."""
    spec = to_comparison(requirement("SPEC", "basic impulse insulation level"))
    assert (spec.attribute, spec.certification_standard) == ("bil", None)
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
