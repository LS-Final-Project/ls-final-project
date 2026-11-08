"""KAN-161 모델이 필수 여부를 UNDETERMINED로 냈는데 인용·상위 조항 문맥에 의무 표현이 있으면 규칙으로 MANDATORY다.

합성 조항과 가짜 모델 응답만 쓴다.
"""

import json
from datetime import date
from uuid import UUID

import pytest

from backend.api.tft.partners.grading import RfpContext, grade_candidate
from backend.api.tft.partners.schemas import PartnerProfile
from backend.api.tft.worker_processor import to_grading
from backend.engine.documents.model import Block, Position, build_document
from backend.engine.rfp.clauses import build_clause_tree
from backend.engine.rfp.requirements import build_inputs, validate_output


def section(*lines: str):
    blocks = [
        Block(block_id=f"n{i:03d}", kind="PARAGRAPH", text=t, position=Position(paragraph_index=i))
        for i, t in enumerate(["PART 2 - PRODUCTS", "2.4 SWITCHGEAR", *lines])
    ]
    tree = build_clause_tree(build_document("DOCX", b"synthetic", blocks, [], tables=0))
    return build_inputs(tree, ["2.4"])[0]


LEAD_IN = section(
    "A. Switchgear shall have the following ratings:",
    "1. Short-time withstand current: 38 kA.",
    "B. Enclosure finish should be gray.",
    "C. Breakers shall be drawout type.",
    "D. Space heaters should be provided as follows:",
    "1. Heater rating: 120 V.",
)


def answer(clause_no: str, quote: str, necessity: str, **extra: object) -> str:
    requirement = {
        "clause_no": clause_no,
        "quote": quote,
        "kind": "SPEC",
        "equipment": "15kV switchgear",
        "task": None,
        "attribute": "rating",
        "value": None,
        "unit": None,
        "operator": "TEXT",
        "standard_system": "NONE",
        "necessity": necessity,
        "designated_model": None,
        **extra,
    }
    return json.dumps({"requirements": [requirement]})


def judged(clause_no: str, quote: str, necessity: str, item=LEAD_IN, **extra: object):
    [kept], rejected, _, _ = validate_output(answer(clause_no, quote, necessity, **extra), item)
    assert rejected == []
    return kept


def test_an_undetermined_quote_with_shall_becomes_mandatory_by_rule():
    kept = judged("2.4.C", "Breakers shall be drawout type.", "UNDETERMINED")
    assert (kept.necessity, kept.necessity_by_rule) == ("MANDATORY", True)
    # 부정 의무도 의무다.
    item = section("A. Fuses shall not be used.")
    kept = judged("2.4.A", "Fuses shall not be used.", "UNDETERMINED", item=item)
    assert kept.necessity == "MANDATORY"


def test_shall_only_in_the_parent_context_also_raises_it():
    assert LEAD_IN.clauses[1].path.startswith("Switchgear shall have")
    kept = judged("2.4.A.1", "Short-time withstand current: 38 kA.", "UNDETERMINED")
    assert (kept.necessity, kept.necessity_by_rule) == ("MANDATORY", True)


@pytest.mark.parametrize(
    ("clause_no", "quote"),
    [
        ("2.4.B", "Enclosure finish should be gray."),
        # 가까운 상위 문맥이 should면 먼 상위의 shall보다 앞선다.
        ("2.4.D.1", "Heater rating: 120 V."),
    ],
)
def test_should_or_a_should_lead_in_stays_undetermined(clause_no, quote):
    kept = judged(clause_no, quote, "UNDETERMINED")
    assert (kept.necessity, kept.necessity_by_rule) == ("UNDETERMINED", False)


@pytest.mark.parametrize("necessity", ["PREFERRED", "CONDITION_BOUND", "MANDATORY"])
def test_a_model_decision_other_than_undetermined_is_kept(necessity):
    kept = judged("2.4.C", "Breakers shall be drawout type.", necessity)
    assert (kept.necessity, kept.necessity_by_rule) == (necessity, False)


def test_text_without_obligation_and_a_model_sent_flag_are_not_trusted():
    item = section("A. Indoor enclosure.")
    kept = judged("2.4.A", "Indoor enclosure.", "UNDETERMINED", item=item, necessity_by_rule=True)
    assert (kept.necessity, kept.necessity_by_rule) == ("UNDETERMINED", False)


@pytest.mark.parametrize(
    ("text", "raised"),
    [
        ("가. 외함은 강판으로 제작하여야 한다.", True),
        ("가. 차단기는 3 사이클 이내에 차단할 수 있어야 한다.", True),
        ("가. 시험 성적서를 제출할 것.", True),
        ("가. 도장 색상은 회색을 권장한다.", False),
        ("가. 원격 감시를 지원할 수 있다.", False),
        ("A. Provide space heaters.", True),
        ("A. Fan failure detection may be provided.", False),
    ],
)
def test_korean_and_imperative_wording(text, raised):
    item = section(text)
    quote = text.split(" ", 1)[1]
    kept = judged(item.clauses[0].clause_no, quote, "UNDETERMINED", item=item)
    assert kept.necessity_by_rule is raised


def test_a_raised_rating_shortfall_excludes_the_partner():
    """의무 문맥의 정격 미달이 UNDETERMINED로 남으면 검토 후보가 된다. 규칙으로 올리면 제외다."""
    kept = judged(
        "2.4.A.1",
        "Short-time withstand current: 38 kA.",
        "UNDETERMINED",
        attribute="short-time withstand current",
        attribute_key="short_time_withstand_current",
        value="38",
        unit="kA",
        operator="GTE",
    )
    graded = to_grading(UUID(int=1), kept)
    assert graded is not None and graded.necessity == "MANDATORY"
    weak = PartnerProfile.model_validate(
        {
            "candidate_code": "P-X",
            "display_name": "가상 P-X",
            "manufacturer_name": "가상제조 P-X",
            "profile_source": "synthetic",
            "profile_version": 1,
            "data_origin": "synthetic",
            "derivation_kind": "PROVIDED",
            "known_at": "2026-10-28",
            "product_family": "합성 제품군",
            "products": [{"product_code": "X-1", "model": "X-100", "scope": ["SUPPLY"]}],
            "integrator_capable": {
                "assembly": True,
                "test": True,
                "integration": True,
                "warranty": True,
            },
            "spec_facts": [
                {
                    "product_code": "X-1",
                    "attribute": "short_time_withstand_current",
                    "value": "25",
                    "unit": "kA",
                    "conditions": None,
                    "evidence_id": "PX-DS-01",
                    "section": "3.1",
                }
            ],
            "certifications": [],
            "similar_tasks": [],
            "supply_regions": ["US"],
            "lead_time": None,
            "warranty": None,
            "confirmation": {
                "status": "UNCONFIRMED",
                "as_of": "2026-10-28",
                "reviewer": "가상 검토자",
                "follow_up_questions": [],
            },
            "notes": None,
        }
    )
    context = RfpContext(
        manufacturer_list=None,
        off_list_permission="UNSTATED",
        supply_region="US",
        requires_integrator=False,
    )
    summary, [row] = grade_candidate(weak, [graded], context, date(2026, 11, 20))
    assert row.mandatory_check.failed == ["short_time_withstand_current"]
    assert summary.overall_grade == "EXCLUDED"


STREAMLINED = section(
    "G. Capacities and Characteristics:",
    "1. Comply with IEEE C37.06.",
    "2. Rated Continuous Current: 4000 A.",
    "3. Enclosure color: ANSI 61 gray.",
    "4. Optional: 2000 A main breaker.",
)


def test_a_streamlined_rating_line_without_any_modality_is_mandatory():
    """'항목: 값' 축약 문장은 규격서에서 'shall be'다. 형제 항목의 Comply는 상위 문맥이 아니다."""
    assert "Comply" not in STREAMLINED.clauses[2].path
    kept = judged(
        "2.4.G.2",
        "Rated Continuous Current: 4000 A.",
        "UNDETERMINED",
        item=STREAMLINED,
        value="4000",
        unit="A",
        operator="EQ",
    )
    assert (kept.necessity, kept.necessity_by_rule) == ("MANDATORY", True)


def test_a_streamlined_text_line_stays_undetermined():
    # 측정값이 없는 문장형(TEXT)은 축약 문장 규칙으로 올리지 않는다.
    kept = judged("2.4.G.3", "Enclosure color: ANSI 61 gray.", "UNDETERMINED", item=STREAMLINED)
    assert (kept.necessity, kept.necessity_by_rule) == ("UNDETERMINED", False)


def test_an_optional_or_alternate_colon_line_is_not_a_streamlined_obligation():
    """'Optional: 2000 A'처럼 선택·대안 표지로 시작하는 콜론 문장은 필수로 올리지 않는다(PR #655 리뷰)."""
    kept = judged(
        "2.4.G.4",
        "Optional: 2000 A main breaker.",
        "UNDETERMINED",
        item=STREAMLINED,
        value="2000",
        unit="A",
        operator="EQ",
    )
    assert (kept.necessity, kept.necessity_by_rule) == ("UNDETERMINED", False)
