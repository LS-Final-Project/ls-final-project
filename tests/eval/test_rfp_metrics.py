"""KAN-143 RFP 평가기. 14번 사례 A·B-1 모양의 합성 정답을 리터럴로 쓰고 정답 파일은 읽지 않는다."""

import pytest

from eval.rfp_metrics import (
    GoldRequirement,
    PredictedRequirement,
    gate_values,
    gold_fingerprint,
    summarize_candidate_grades,
    summarize_edit_traces,
    summarize_integration_responsibility,
    summarize_requirement_extraction,
)

GOLD = {
    "A": [
        GoldRequirement("2.5", "tap", "EVIDENCE_FOUND", "MANDATORY"),
        GoldRequirement("2.5", "insulation_class", "EVIDENCE_FOUND"),
        GoldRequirement("2.5", "impedance", "CONDITIONAL"),
        GoldRequirement("2.5 C.1", "forced_air_capacity_increase", "CONDITIONAL", "MANDATORY"),
        GoldRequirement("2.5", "secondary_voltage", "SPEC_GAP", expected_path="ALTERNATIVE_SPEC"),
    ],
    "B-1": [
        GoldRequirement(
            "2.6", "rated_current", "INSUFFICIENT_EVIDENCE", "MANDATORY", "EXTERNAL_PRODUCT"
        ),
        GoldRequirement("2.6", "bil", "EVIDENCE_FOUND", "PREFERRED"),
    ],
}


def pred(clause, attribute, status, *, evidence=("b1",), necessity=None, path=None, value="x"):
    """예측 요구 하나를 만든다.

    Args:
        clause: 조항 번호.
        attribute: 속성.
        status: 예측 상태.
        evidence: 근거 블록 ID.
        necessity: 예측 necessity.
        path: 예측 보완 경로.
        value: 추출 값 문자열.

    Returns:
        PredictedRequirement: 예측.
    """
    return PredictedRequirement(
        clause, attribute, status, necessity, path, frozenset(evidence), value
    )


PREDICTED = {
    "A": [
        pred("2.5", "tap", "EVIDENCE_FOUND", necessity="MANDATORY", value="±2×2.5%"),
        pred("2.5", "insulation_class", "EVIDENCE_FOUND", evidence=(), value="Class F"),
        pred("2.5", "impedance", "CONDITIONAL", value="5.75%"),
        pred("2.5", "secondary_voltage", "SPEC_GAP", path="ALTERNATIVE_SPEC", value="600  v"),
        pred("2.5", "primary_voltage", "EVIDENCE_FOUND", value=None),
        pred("2.5", "tap", "EVIDENCE_FOUND", value="±2×2.5%"),
    ],
    "B-1": [
        pred("2.6", "rated_current", "EVIDENCE_FOUND", necessity="MANDATORY"),
        pred("2.6", "bil", "EVIDENCE_FOUND", necessity="UNDETERMINED"),
    ],
}


def test_extraction_status_and_critical_errors_match_hand_calculation():
    """추출 P/R·상태 혼동·중대오류 (a)(b)(c)·경로·necessity 손계산을 확인한다.

    Args:
        없음.

    Returns:
        None: 정답 7건, 예측 8건(FP 1, 중복 1), 매칭 6건.
    """
    result = summarize_requirement_extraction(GOLD, PREDICTED, run_label="FIRST")
    extraction = result["extraction"]
    assert (extraction["gold"], extraction["predicted"], extraction["matched"]) == (7, 8, 6)
    assert extraction["duplicate_predictions"] == 1
    assert (extraction["precision"], extraction["recall"]) == (
        pytest.approx(6 / 8),
        pytest.approx(6 / 7),
    )
    assert extraction["f1"] == pytest.approx(0.8)
    assert (extraction["precision_count"], extraction["recall_count"]) == ("6/8", "6/7")
    status = result["status"]
    assert status["confusion"]["EVIDENCE_FOUND"]["EVIDENCE_FOUND"] == 3
    assert status["confusion"]["CONDITIONAL"]["NO_PREDICTION"] == 1
    assert status["confusion"]["INSUFFICIENT_EVIDENCE"]["EVIDENCE_FOUND"] == 1
    found = status["per_class"]["EVIDENCE_FOUND"]
    assert (found["precision"], found["recall"]) == (pytest.approx(3 / 4), 1.0)
    critical = result["critical_errors"]
    assert critical["unsupported_evidence_found"]["keys"] == [("A", "2.5", "insulation_class")]
    assert critical["gap_judged_found"]["keys"] == [("B-1", "2.6", "rated_current")]
    assert critical["mandatory_not_extracted"]["keys"] == [
        ("A", "2.5 C.1", "forced_air_capacity_increase")
    ]
    path = result["supplement_path"]
    assert (path["total"], path["exact_match_rate"]) == (2, 0.5)
    assert path["confusion"]["EXTERNAL_PRODUCT"]["NO_PREDICTION"] == 1
    assert result["necessity"] == {"total": 4, "exact_match_rate": 0.5}
    assert result["quality_gate_evaluated"] is False
    assert "±2×2.5%" not in str(result)


def test_remeasure_shares_the_gold_fingerprint():
    """REMEASURE는 같은 정답이면 순서와 무관하게 같은 지문, 기대 상태가 바뀌면 다른 지문이다.

    Args:
        없음.

    Returns:
        None: 지문 비교를 확인한다.
    """
    first = summarize_requirement_extraction(GOLD, PREDICTED, run_label="FIRST")
    reordered = {"B-1": GOLD["B-1"][::-1], "A": GOLD["A"]}
    again = summarize_requirement_extraction(reordered, {}, run_label="REMEASURE")
    assert again["run_label"] == "REMEASURE"
    assert again["gold_fingerprint"] == first["gold_fingerprint"]
    assert again["extraction"]["precision"] is None and again["extraction"]["recall"] == 0.0
    changed = {**GOLD, "B-1": [GoldRequirement("2.6", "bil", "CONDITIONAL")]}
    assert gold_fingerprint(changed) != first["gold_fingerprint"]


def test_edit_trace_extraction_counts_deleted_values():
    """정규화 후 삭제 값과 같은 예측 값을 센다. 삭제 값이 없는 사례는 분모에서 뺀다.

    Args:
        없음.

    Returns:
        None: A 사례의 값 있는 예측 5건 중 1건.
    """
    result = summarize_edit_traces(PREDICTED, {"A": frozenset({" 600 V "}), "B-1": frozenset()})
    assert (result["predictions"], result["edit_trace_extractions"]) == (5, 1)
    assert result["rate"] == pytest.approx(0.2)
    assert result["keys"] == [("A", "2.5", "secondary_voltage")]
    assert summarize_edit_traces(PREDICTED, {})["rate"] is None


def test_candidate_grades_separate_risky_recommendations():
    """등급 혼동·추천 정밀도와 정답 EXCLUDED를 추천으로 올린 위험 오류를 따로 센다.

    Args:
        없음.

    Returns:
        None: 추천 2건 중 정답 추천 1건, 위험 오류 1건, 미예측 1건.
    """
    gold = {
        "A": {
            "P-A": "MANDATORY_CONFIRMED",
            "P-B": "REVIEW",
            "L-A": "EXCLUDED",
            "T-A": "REFERENCE_ONLY",
        }
    }
    predicted = {"A": {"P-A": "MANDATORY_CONFIRMED", "P-B": "REFERENCE_ONLY", "L-A": "REVIEW"}}
    result = summarize_candidate_grades(gold, predicted)  # type: ignore[arg-type]
    assert result["recommendation_precision"] == 0.5 and result["recommendation_count"] == "1/2"
    assert result["excluded_recommended"] == {"count": 1, "denominator": 1, "keys": [("A", "L-A")]}
    assert result["grades"]["confusion"]["REFERENCE_ONLY"]["NO_PREDICTION"] == 1
    assert result["grades"]["per_class"]["REVIEW"]["fp"] == 1
    assert summarize_candidate_grades({}, {})["recommendation_precision"] is None


@pytest.mark.parametrize(
    "call",
    [
        lambda: summarize_requirement_extraction(
            {"A": [GoldRequirement("2.5", "tap", "EVIDENCE_FOUND")] * 2}, {}, run_label="FIRST"
        ),
        lambda: summarize_requirement_extraction(
            {"A": [GoldRequirement("2.5", "tap", "MET")]},
            {},
            run_label="FIRST",  # type: ignore[arg-type]
        ),
        lambda: summarize_requirement_extraction(
            GOLD, {"A": [pred("2.5", "tap", "PASSED")]}, run_label="FIRST"
        ),
        lambda: summarize_requirement_extraction(GOLD, {"Z": []}, run_label="FIRST"),
        lambda: summarize_requirement_extraction(GOLD, PREDICTED, run_label="SECOND"),  # type: ignore[arg-type]
        lambda: summarize_candidate_grades({"A": {"P-A": "REVIEW"}}, {"A": {"X-1": "REVIEW"}}),
        lambda: summarize_candidate_grades({"A": {"P-A": "TOP"}}, {}),  # type: ignore[dict-item]
    ],
    ids=[
        "dup-gold-key",
        "gold-enum",
        "pred-enum",
        "unknown-case",
        "run-label",
        "unknown-candidate",
        "grade-enum",
    ],
)
def test_invalid_inputs_are_rejected(call):
    """정답 키 중복·모르는 어휘·모르는 사례·후보·실행 라벨을 거부한다.

    Args:
        call: 잘못된 입력으로 평가기를 부르는 함수.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        call()


def test_edit_traces_reject_non_text_values():
    """삭제 값 원소나 예측 value_text가 문자열이 아니면 AttributeError가 아니라 입력 오류다.

    Args:
        없음.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        summarize_edit_traces(PREDICTED, {"A": frozenset({1})})
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        summarize_edit_traces(
            {"A": [pred("2.5", "x", "EVIDENCE_FOUND", value=123)]}, {"A": frozenset({"600 V"})}
        )


def reasoned(clause, attribute, status, reason):
    """사유가 있는 예측 요구 하나를 만든다.

    Args:
        clause: 조항 번호.
        attribute: 속성.
        status: 예측 상태.
        reason: 예측 사유(엔진 사유 문장).

    Returns:
        PredictedRequirement: 예측.
    """
    return PredictedRequirement(
        clause, attribute, status, None, None, frozenset({"b1"}), "x", reason
    )


def test_status_match_needs_status_and_normalized_reason():
    """상태 일치는 정답 요구 전체가 분모이고, 정답 사유가 있으면 정규화한 사유 문장까지 같아야 한다."""
    gold = {
        "A": [
            GoldRequirement("1", "a", "SPEC_GAP", expected_reason="정격  전류가 부족함"),
            GoldRequirement("1", "b", "SPEC_GAP", expected_reason="정격 전압 차이"),
            GoldRequirement("1", "c", "CONDITIONAL"),
            GoldRequirement("1", "d", "EVIDENCE_FOUND", expected_reason="근거 있음"),
            GoldRequirement("1", "e", "EVIDENCE_FOUND"),
        ]
    }
    predicted = {
        "A": [
            reasoned("1", "a", "SPEC_GAP", " 정격 전류가  부족함 "),
            reasoned("1", "b", "SPEC_GAP", "다른 사유"),
            reasoned("1", "c", "CONDITIONAL", None),
            reasoned("1", "d", "EVIDENCE_FOUND", None),
        ]
    }
    match = summarize_requirement_extraction(gold, predicted, run_label="FIRST")["status_match"]
    assert match == {
        "matched": 2,
        "total": 5,
        "rate": 0.4,
        "count": "2/5",
        "reason_compared_count": 3,
        "reason_compared": False,
    }
    status_only = summarize_requirement_extraction(GOLD, PREDICTED, run_label="FIRST")
    assert status_only["status_match"]["reason_compared"] is False
    assert status_only["status_match"]["count"] == "5/7"


def test_mandatory_gap_judged_found_counts_binding_requirements_only():
    """필수(MANDATORY·CONDITION_BOUND) 미충족·미확인을 확정 충족으로 본 건수와 분모만 세고, necessity가 없으면 None이다."""
    gold = {
        "A": [
            GoldRequirement("1", "a", "SPEC_GAP", "MANDATORY"),
            GoldRequirement("1", "b", "INSUFFICIENT_EVIDENCE", "CONDITION_BOUND"),
            GoldRequirement("1", "c", "SPEC_GAP", "PREFERRED"),
            GoldRequirement("1", "d", "INSUFFICIENT_EVIDENCE", "MANDATORY"),
        ]
    }
    predicted = {
        "A": [
            pred("1", "a", "EVIDENCE_FOUND"),
            pred("1", "b", "CONDITIONAL"),
            pred("1", "c", "EVIDENCE_FOUND"),
        ]
    }
    found = summarize_requirement_extraction(gold, predicted, run_label="FIRST")["critical_errors"][
        "mandatory_gap_judged_found"
    ]
    assert found == {
        "count": 1,
        "denominator": 3,
        "necessity_missing": 0,
        "keys": [("A", "1", "a")],
    }
    # necessity가 없는 정답 요구가 하나라도 있으면(상태와 무관) blocker 값을 내지 않는다.
    gold["A"].append(GoldRequirement("1", "f", "EVIDENCE_FOUND"))
    unknown = summarize_requirement_extraction(gold, predicted, run_label="FIRST")
    partial = unknown["critical_errors"]["mandatory_gap_judged_found"]
    assert (partial["count"], partial["denominator"], partial["necessity_missing"]) == (
        None,
        None,
        1,
    )
    bare = {"A": [GoldRequirement("1", "a", "SPEC_GAP")]}
    none = summarize_requirement_extraction(
        bare, {"A": [pred("1", "a", "EVIDENCE_FOUND")]}, run_label="FIRST"
    )
    assert none["critical_errors"]["mandatory_gap_judged_found"]["count"] is None
    assert none["critical_errors"]["gap_judged_found"]["count"] == 1


def test_mandatory_confirmed_precision_and_constraint_denominator():
    """필수 기술조건 확인 정밀도는 예측 MANDATORY_CONFIRMED만 분모로 하고, 필수 제약 위반은 정답 EXCLUDED 수를 분모로 낸다."""
    gold = {
        "A": {
            "P-A": "MANDATORY_CONFIRMED",
            "P-B": "REVIEW",
            "P-C": "MANDATORY_CONFIRMED",
            "L-A": "EXCLUDED",
            "L-B": "EXCLUDED",
        }
    }
    predicted = {
        "A": {
            "P-A": "MANDATORY_CONFIRMED",
            "P-B": "MANDATORY_CONFIRMED",
            "P-C": "REVIEW",
            "L-A": "REVIEW",
        }
    }
    result = summarize_candidate_grades(gold, predicted)  # type: ignore[arg-type]
    assert (result["mandatory_confirmed_precision"], result["mandatory_confirmed_count"]) == (
        0.5,
        "1/2",
    )
    assert result["recommendation_precision"] == 0.75
    assert result["excluded_recommended"]["count"] == 1
    assert result["excluded_recommended"]["denominator"] == 2
    assert result["review_unconfirmed_listed"] is None
    assert summarize_candidate_grades({}, {})["mandatory_confirmed_precision"] is None


def test_gate_values_match_rfp_metric_keys_and_leave_missing_tools_none():
    """gate_values는 RFP_METRIC_KEYS 순서로 9개 값을 내고, 도구가 없거나 요약이 없는 키는 None이다."""
    from eval.adoption import RFP_METRIC_KEYS

    extraction = summarize_requirement_extraction(GOLD, PREDICTED, run_label="FIRST")
    values = gate_values(extraction, None)
    assert tuple(values) == RFP_METRIC_KEYS
    assert values["rfp_requirement_recall"] == extraction["extraction"]["recall"]
    assert values["rfp_requirement_precision"] == extraction["extraction"]["precision"]
    # 픽스처는 정답 사유가 없고(상태만 비교) necessity가 없는 정답 요구가 있어 두 게이트 값은 None이다.
    assert extraction["status_match"]["rate"] == 5 / 7
    for key in (
        "rfp_status_match_rate",
        "rfp_critical_false_fulfillment_count",
        "rfp_evidence_accuracy",
        "rfp_integration_responsibility_rate",
        "rfp_draft_trace_rate",
        "rfp_partner_recommendation_precision",
        "rfp_mandatory_constraint_violation_count",
    ):
        assert values[key] is None
    grades = summarize_candidate_grades(
        {"A": {"P": "MANDATORY_CONFIRMED", "L": "EXCLUDED"}}, {"A": {"P": "MANDATORY_CONFIRMED"}}
    )
    both = gate_values(None, grades)
    # 검토 후보 미확인 조건 표시를 같이 재지 않았으면 협력사 정밀도 게이트는 None이다(KAN-191).
    assert grades["mandatory_confirmed_precision"] == 1.0
    assert both["rfp_partner_recommendation_precision"] is None
    assert both["rfp_mandatory_constraint_violation_count"] == 0
    assert both["rfp_requirement_recall"] is None
    # 발생 가능 0건이면 0건 게이트는 None이다(아무것도 측정하지 않은 통과 방지).
    no_excluded = summarize_candidate_grades(
        {"A": {"P": "MANDATORY_CONFIRMED"}}, {"A": {"P": "MANDATORY_CONFIRMED"}}
    )
    assert gate_values(None, no_excluded)["rfp_mandatory_constraint_violation_count"] is None
    no_binding = {
        "A": [GoldRequirement("1", "a", "EVIDENCE_FOUND", "MANDATORY", expected_reason="r")]
    }
    nothing = summarize_requirement_extraction(no_binding, {}, run_label="FIRST")
    assert nothing["critical_errors"]["mandatory_gap_judged_found"]["denominator"] == 0
    assert gate_values(nothing, None)["rfp_critical_false_fulfillment_count"] is None
    full = {
        "A": [
            GoldRequirement("1", "a", "SPEC_GAP", "MANDATORY", expected_reason="정격 부족"),
            GoldRequirement("1", "b", "EVIDENCE_FOUND", "PREFERRED", expected_reason="근거 있음"),
        ]
    }
    guessed = {
        "A": [
            reasoned("1", "a", "EVIDENCE_FOUND", "x"),
            reasoned("1", "b", "EVIDENCE_FOUND", "근거  있음"),
        ]
    }
    measured = gate_values(summarize_requirement_extraction(full, guessed, run_label="FIRST"), None)
    assert measured["rfp_status_match_rate"] == 0.5
    assert measured["rfp_critical_false_fulfillment_count"] == 1


def test_integration_responsibility_counts_assembly_requirements_with_a_check_and_a_path():
    """gates-rfp-v1 rfp_integration_responsibility_rate: 조립품 공급 요구 단위(KAN-186)."""
    from backend.api.tft.rfp.integration import Requirement, integration_responsibility

    unit = [
        Requirement(clause_no="2.3.A", assembly_clause=True),
        Requirement(clause_no="2.9.B", task="FACTORY_TEST"),
    ]
    gold = {"14": ["2.3.A", "2.9.B"], "10": ["2.2"]}
    predicted = {
        # Unresolved holder with the fallback paths: both assembly requirements count.
        "14": integration_responsibility(unit).model_dump(mode="json"),
        # The engine did not see 2.2 as assembly supply: missed.
        "10": integration_responsibility([Requirement(clause_no="2.2")]).model_dump(mode="json"),
    }
    summary = summarize_integration_responsibility(gold, predicted)
    assert (summary["count"], summary["denominator"], summary["missed"]) == (
        "2/3",
        3,
        [("10", "2.2")],
    )
    assert summary["rate"] == pytest.approx(2 / 3)
    assert gate_values(None, None, summary)["rfp_integration_responsibility_rate"] == pytest.approx(
        2 / 3
    )
    # Listed but neither a holder nor a path: not handled.
    bare = {**predicted["14"], "fallback_paths": []}
    assert summarize_integration_responsibility({"14": ["2.3.A"]}, {"14": bare})["rate"] == 0.0
    decided = integration_responsibility(unit, "OWN").model_dump(mode="json")
    assert summarize_integration_responsibility({"14": ["2.3.A"]}, {"14": decided})["rate"] == 1.0
    # A document that was not predicted counts against, an unknown document is refused.
    assert summarize_integration_responsibility({"14": ["2.3.A"]}, {})["count"] == "0/1"
    with pytest.raises(ValueError):
        summarize_integration_responsibility({"14": ["2.3.A"]}, {"99": decided})
    with pytest.raises(ValueError):
        summarize_integration_responsibility({"14": "2.3.A"}, {})
    # Nothing to measure: the gate stays unmeasured.
    empty = summarize_integration_responsibility({}, {})
    assert empty["rate"] is None
    assert gate_values(None, None, empty)["rfp_integration_responsibility_rate"] is None


def test_review_candidates_must_show_every_unconfirmed_item_of_the_gold():
    """검토 후보 미확인 조건 표시(KAN-191): 실제 등급 판정의 미확인 항목 이름을 정답 목록과 대조한다."""
    from backend.api.tft.partners.grading import RfpContext, grade_candidate
    from eval.rfp_metrics import MANUFACTURER_CONDITIONS, unconfirmed_items
    from tests.tft.test_partner_grading import DAY, MV, fact, profile
    from tests.tft.test_partner_grading import req as grading_req

    context = RfpContext(
        manufacturer_list=["Listed Maker"],
        off_list_permission="UNSTATED",
        supply_region="US",
        requires_integrator=True,
    )
    # 정격 전압 근거만 있고 형식 근거·통합 책임(연동)은 없다. 제조사는 고객 목록 밖(허용 여부 미기재).
    partner = profile("P-A", facts=[fact("rated_max_voltage", "15", "kV")], integration=None)
    summary, grades = grade_candidate(partner, MV[:2], context, DAY)
    assert summary.overall_grade == "REVIEW"
    shown = unconfirmed_items(summary.model_dump(), [g.model_dump() for g in grades])
    assert shown == {"form", "integrator_capable.integration", "manufacturer_list"}

    gold = {"B": {"P-A": "REVIEW", "P-B": "REVIEW", "P-C": "REVIEW", "L-B": "EXCLUDED"}}
    predicted = {"B": {"P-A": "REVIEW", "P-B": "REVIEW", "P-C": "MANDATORY_CONFIRMED"}}
    gold_items = {
        "B": {
            "P-A": ["form", "manufacturer_list"],
            "P-B": ["form", "integrator_capable.integration"],
            "P-C": ["bil"],
        }
    }
    predicted_items = {"B": {"P-A": sorted(shown), "P-B": ["form"]}}
    result = summarize_candidate_grades(gold, predicted, gold_items, predicted_items)  # type: ignore[arg-type]
    listing = result["review_unconfirmed_listed"]
    # 분모는 정답·예측 모두 REVIEW인 후보(P-A·P-B). P-C는 등급 자체가 틀려 등급 지표에서 센다.
    assert (listing["count"], listing["rate"]) == ("1/2", 0.5)
    assert listing["missing"] == [("B", "P-B", ["integrator_capable.integration"])]
    # 표시를 같이 쟀을 때만 게이트 값(필수 기술조건 확인 정밀도)이 나온다.
    assert result["mandatory_confirmed_count"] == "0/1"
    assert gate_values(None, result)["rfp_partner_recommendation_precision"] == 0.0
    # 검토 후보 대상이 0건이면(0/0) 표시를 잰 것이 없으므로 게이트 값은 None이다.
    none_review = summarize_candidate_grades(
        {"B": {"P-A": "MANDATORY_CONFIRMED"}}, {"B": {"P-A": "MANDATORY_CONFIRMED"}}, {}, {}
    )
    assert none_review["review_unconfirmed_listed"]["count"] == "0/0"
    assert gate_values(None, none_review)["rfp_partner_recommendation_precision"] is None
    # 제조사 확인 문구가 아닌 확인 사항(지정 모델 안내)만 있으면 manufacturer_list로 세지 않는다.
    designated_only = [grading_req(9, "relay", "SEL-751A", designated_model="SEL-751A")]
    forbidden = context.model_copy(update={"off_list_permission": "FORBIDDEN"})
    only, only_grades = grade_candidate(partner, designated_only, forbidden, DAY)
    assert only.on_customer_manufacturer_list is False and only.conditions_to_confirm
    dumped = [g.model_dump() for g in only_grades]
    assert "manufacturer_list" not in unconfirmed_items(only.model_dump(), dumped)
    # 등급 판정의 제조사 확인 문구가 MANUFACTURER_CONDITIONS와 같은지(문구가 바뀌면 여기서 걸린다).
    for permission in ("ALLOWED", "UNSTATED"):
        other = context.model_copy(update={"off_list_permission": permission})
        made, _ = grade_candidate(partner, MV[:1], other, DAY)
        assert (
            set(made.conditions_to_confirm)
            and set(made.conditions_to_confirm) <= MANUFACTURER_CONDITIONS
        )
    # 정답 REVIEW 후보에 미확인 목록이 없으면 정답이 덜 만들어진 것이라 받지 않는다.
    with pytest.raises(ValueError, match="EVAL_INVALID_INPUT"):
        summarize_candidate_grades(gold, predicted, {"B": {"P-A": []}}, predicted_items)  # type: ignore[arg-type]
