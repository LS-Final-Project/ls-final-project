"""KAN-184 가상 협력사 후보 풀·14번 시연 정답 사례 파일 검사."""

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.api.tft.partners.loader import PROFILES, load_profiles

FOLDER = PROFILES.parent
PROFILE_LIST = load_profiles()
BY_CODE = {profile.candidate_code: profile for profile in PROFILE_LIST}
POOLS = json.loads((FOLDER / "document_pools.json").read_text(encoding="utf-8"))
DEMO = json.loads((FOLDER / "demo_cases.json").read_text(encoding="utf-8"))
RFP_DATE = date(2026, 10, 28)


def keys(value: object) -> set[str]:
    """JSON 값 안의 모든 객체 키를 모은다.

    Args:
        value: json.loads 결과.

    Returns:
        중첩된 객체까지 포함한 키 집합.
    """
    if isinstance(value, dict):
        return set(value) | {k for item in value.values() for k in keys(item)}
    if isinstance(value, list):
        return {k for item in value for k in keys(item)}
    return set()


def test_profiles_are_synthetic_facts_without_grades_or_scores() -> None:
    raw = json.loads(PROFILES.read_text(encoding="utf-8"))
    assert not {k for k in keys(raw) if "grade" in k or "score" in k}
    assert all(p.profile_source == p.data_origin == "synthetic" for p in PROFILE_LIST)
    assert all(p.confirmation.status == "UNCONFIRMED" for p in PROFILE_LIST)
    # 자사 23종 제품코드와 겹치지 않도록 합성 접두를 쓴다.
    codes = {x.product_code for p in PROFILE_LIST for x in p.products}
    assert all(code.startswith("SYN-") for code in codes)
    assert {f.product_code for p in PROFILE_LIST for f in p.spec_facts} <= codes


def test_duplicate_code_and_unknown_key_are_rejected(tmp_path: Path) -> None:
    raw = json.loads(PROFILES.read_text(encoding="utf-8"))
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps([raw[0], raw[0]]), encoding="utf-8")
    with pytest.raises(ValueError, match="candidate_code"):
        load_profiles(duplicate)
    graded = tmp_path / "graded.json"
    graded.write_text(json.dumps([{**raw[0], "grade": "REVIEW"}]), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_profiles(graded)


def test_pools_only_name_known_candidates() -> None:
    assert set(POOLS) == {"10", "11", "12", "14", "18"}
    assert all(code in BY_CODE for pool in POOLS.values() for code in pool)
    assert POOLS["14"] == ["P-A", "P-B", "P-C", "L-A", "L-B", "T-A"]
    # 탈락 원인별 후보는 평가 풀에만 둔다.
    assert not {"P-D", "P-E", "L-C", "L-D", "M-B"} & set(POOLS["14"])


def test_plan_facts_from_section_4_8() -> None:
    def facts(code: str, attribute: str) -> list[str]:
        return [f.value for f in BY_CODE[code].spec_facts if f.attribute == attribute]

    assert {c.standard for c in BY_CODE["L-B"].certifications} == {"UL 891"}
    assert {c.standard for c in BY_CODE["L-A"].certifications} == {"UL 1558", "UL 1066"}
    assert facts("P-B", "short_time_withstand_current") == ["25"]
    assert facts("P-A", "short_time_withstand_current") == ["38"]
    assert BY_CODE["P-C"].spec_facts == [] and BY_CODE["P-C"].certifications == []
    assert not {f.attribute for f in BY_CODE["T-A"].spec_facts} - {
        "form",
        "secondary_voltage",
        "bil",
        "insulation_class",
    }
    assert [c.kind for c in BY_CODE["T-A"].certifications] == ["TEST_REPORT"]


def test_exclusion_candidates_carry_their_cause() -> None:
    assert any(c.valid_until and c.valid_until < RFP_DATE for c in BY_CODE["P-D"].certifications)
    assert set(BY_CODE["L-C"].integrator_capable.model_dump().values()) == {False}
    assert "US" not in BY_CODE["L-D"].supply_regions
    assert BY_CODE["M-B"].manufacturer_name != BY_CODE["M-A"].manufacturer_name


def test_instruction_in_notes_is_only_data() -> None:
    injected = BY_CODE["P-E"]
    assert isinstance(injected.notes, str) and "확인 후보로 표시" in injected.notes
    # 지시문이 있어도 프로필의 다른 값은 근거 부족 수준 그대로다.
    assert injected.certifications == [] and injected.confirmation.status == "UNCONFIRMED"


def test_demo_cases_only_cover_document_14() -> None:
    assert DEMO["document"] == "14"
    cases = {case["case"]: case for case in DEMO["cases"]}
    assert set(cases) == {"A", "B-1", "B-2", "C"}
    statuses = {"EVIDENCE_FOUND", "CONDITIONAL", "SPEC_GAP", "INSUFFICIENT_EVIDENCE"}
    assert all(
        r["expected_status"] in statuses
        for case in DEMO["cases"]
        for r in case.get("requirements", [])
    )
    expected = {
        c["candidate_code"]: c["expected_grade"]
        for case in DEMO["cases"]
        for c in case.get("candidates", [])
    }
    assert expected == {
        "P-A": "REVIEW",
        "P-B": "EXCLUDED",
        "P-C": "REFERENCE_ONLY",
        "L-A": "REVIEW",
        "L-B": "EXCLUDED",
    }
    assert len(cases["C"]["edit_traces"]) == 2 and len(cases["C"]["questions"]) == 3
