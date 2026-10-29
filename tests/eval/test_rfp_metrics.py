"""KAN-143 RFP 평가기. 14번 사례 A·B-1 모양의 합성 정답을 리터럴로 쓰고 정답 파일은 읽지 않는다."""

import pytest

from eval.rfp_metrics import (
    GoldRequirement,
    PredictedRequirement,
    gold_fingerprint,
    summarize_candidate_grades,
    summarize_edit_traces,
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
    assert result["excluded_recommended"] == {"count": 1, "keys": [("A", "L-A")]}
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
