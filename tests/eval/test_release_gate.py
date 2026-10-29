"""KAN-156 출시 게이트·퇴행·결함 분류. 합격선·허용 폭은 검사용 합성 값이며 KAN-82 잠금 값이 아니다."""

import pytest

from eval.release_gate import (
    Gate,
    MetricValue,
    Tolerance,
    classify_defects,
    detect_regressions,
    evaluate_gates,
    rare_risk_misses,
)

METRICS = [
    MetricValue("category_macro_f1", "all", 0.82, 100),
    MetricValue("critical_miss_count", "all", 1.0, 12, held=2),
    MetricValue("schema_violation_rate", "all", 0.05, 90, errors=3),
    MetricValue("hit_at_5", "all", None, 0),
    MetricValue("priority_rule_exact", "all", 0.1 + 0.2, 50),
]
GATES = [
    Gate("category_macro_f1", ">=", 0.8),
    Gate("critical_miss_count", "<=", 0.0),
    Gate("schema_violation_rate", "<=", 0.02),
    Gate("hit_at_5", ">=", 0.7),
    Gate("dept_pipeline_exact", ">=", 0.9, blocker=False),
    Gate("priority_rule_exact", "==", 0.3),
]


def test_gates_pass_fail_and_not_evaluable():
    """PASS·FAIL·NOT_EVALUABLE과 값·분모 병기, '==' 오차 허용을 확인한다.

    Args:
        없음.

    Returns:
        None: 판정과 blocker를 확인한다.
    """
    result = evaluate_gates(METRICS, GATES)
    rows = {row["metric_key"]: row for row in result["gates"]}
    assert rows["category_macro_f1"]["status"] == "PASS"
    assert rows["critical_miss_count"]["status"] == "FAIL"
    assert rows["critical_miss_count"]["held"] == 2 and rows["critical_miss_count"]["support"] == 12
    assert rows["hit_at_5"]["status"] == "NOT_EVALUABLE"  # 값 None
    assert rows["dept_pipeline_exact"]["status"] == "NOT_EVALUABLE"  # 지표 없음
    assert rows["priority_rule_exact"]["status"] == "PASS"  # 0.1+0.2 == 0.3 (isclose)
    assert result["counts"] == {"PASS": 2, "FAIL": 2, "NOT_EVALUABLE": 2}
    assert result["blocker"] is True


def test_not_evaluable_blocker_gate_blocks_release():
    """미달이 없어도 blocker 게이트가 측정 불가면 출시를 막고, 비blocker 측정 불가는 막지 않는다.

    Args:
        없음.

    Returns:
        None: blocker 판정을 확인한다.
    """
    metrics = [MetricValue("category_macro_f1", "all", 0.9, 10)]
    assert evaluate_gates(metrics, [Gate("category_macro_f1", ">=", 0.8)])["blocker"] is False
    assert evaluate_gates(metrics, [Gate("hit_at_5", ">=", 0.7)])["blocker"] is True
    assert evaluate_gates(metrics, [Gate("hit_at_5", ">=", 0.7, blocker=False)])["blocker"] is False


@pytest.mark.parametrize(
    "metrics,gates",
    [
        (METRICS + [MetricValue("hit_at_5", "all", 0.5, 3)], GATES),
        (METRICS, GATES + [Gate("hit_at_5", ">=", 0.1)]),
        (METRICS, [Gate("hit_at_5", ">", 0.1)]),
    ],
    ids=["duplicate-metric", "duplicate-gate", "bad-comparison"],
)
def test_invalid_gate_input_is_rejected(metrics, gates):
    """같은 key·segment 중복과 허용 밖 비교를 거부한다.

    Args:
        metrics: 지표.
        gates: 게이트.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        evaluate_gates(metrics, gates)


def test_rare_risk_marks_zero_support_unmeasured():
    """CRITICAL 유형별 누락을 꺼내고 표본 0건 유형은 UNMEASURED로 둔다.

    Args:
        없음.

    Returns:
        None: 유형별 값과 혼동을 확인한다.
    """
    summary = {
        "critical": {
            "support": 3,
            "missed": 1,
            "by_gold": {
                "SAFETY": {"support": 3, "missed": 1},
                "SITE_DOWN": {"support": 0, "missed": 0},
            },
            "safety_as_site_down": 1,
            "site_down_as_safety": 0,
        }
    }
    result = rare_risk_misses(summary)
    assert result["classes"]["SAFETY"] == {"support": 3, "missed": 1, "status": "MEASURED"}
    assert result["classes"]["SITE_DOWN"]["status"] == "UNMEASURED"
    assert result["unmeasured"] == ["SITE_DOWN"]
    assert result["confusion"] == {"safety_as_site_down": 1, "site_down_as_safety": 0}
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        rare_risk_misses({"critical": {}})


TOLERANCES = [
    Tolerance("urgency_macro_f1", "higher_better", 0.02, "QUALITY"),
    Tolerance("total_tokens", "lower_better", 100.0, "COST"),
    Tolerance("latency_p95_ms", "lower_better", 50.0, "LATENCY"),
    Tolerance("hit_at_5", "higher_better", 0.05, "QUALITY"),
    Tolerance("cost_usd", "lower_better", 0.01, "COST"),
]
CURRENT = [
    MetricValue("urgency_macro_f1", "all", 0.70, 100),
    MetricValue("urgency_macro_f1", "PHONE", 0.60, 20),
    MetricValue("total_tokens", "all", 1200.0, None),
    MetricValue("latency_p95_ms", "all", 900.0, None),
    MetricValue("hit_at_5", "all", 0.8, 40),
]
BASELINE = [
    MetricValue("urgency_macro_f1", "all", 0.75, 100),
    MetricValue("urgency_macro_f1", "PHONE", 0.61, 20),
    MetricValue("total_tokens", "all", 1150.0, None),
    MetricValue("latency_p95_ms", "all", 800.0, None),
]


def test_regressions_respect_direction_and_tolerance():
    """높을수록 좋은 지표의 하락·낮을수록 좋은 지표의 상승을 허용 폭과 비교한다.

    Args:
        없음.

    Returns:
        None: REGRESSION·OK·NO_BASELINE·NOT_EVALUABLE을 확인한다.
    """
    result = detect_regressions(CURRENT, BASELINE, tolerances=TOLERANCES)
    items = {(i["metric_key"], i["segment"]): i for i in result["items"]}
    assert items[("urgency_macro_f1", "all")]["status"] == "REGRESSION"  # 0.05 하락 > 0.02
    assert items[("urgency_macro_f1", "all")]["worsened_by"] == pytest.approx(0.05)
    assert items[("urgency_macro_f1", "PHONE")]["status"] == "OK"  # 0.01 하락
    assert items[("total_tokens", "all")]["status"] == "OK"  # 50 증가 ≤ 100
    assert items[("latency_p95_ms", "all")]["status"] == "REGRESSION"  # 100 증가 > 50
    assert items[("hit_at_5", "all")]["status"] == "NO_BASELINE"
    assert items[("cost_usd", "all")]["status"] == "NOT_EVALUABLE"
    assert result["counts"] == {"OK": 2, "REGRESSION": 2, "NO_BASELINE": 1, "NOT_EVALUABLE": 1}
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        detect_regressions(CURRENT, BASELINE, tolerances=TOLERANCES + TOLERANCES[:1])
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        detect_regressions(
            CURRENT, BASELINE, tolerances=[Tolerance("x", "lower_better", -1.0, "COST")]
        )


def test_defects_are_classified_by_source():
    """게이트·퇴행·호출 실패를 결함 유형으로 나누고 근거를 남긴다.

    Args:
        없음.

    Returns:
        None: 7종 유형과 blocker 전달을 확인한다.
    """
    gates = evaluate_gates(METRICS, GATES)
    regressions = detect_regressions(
        CURRENT + [MetricValue("cost_usd", "all", 0.5, None)],
        BASELINE + [MetricValue("cost_usd", "all", 0.1, None)],
        tolerances=TOLERANCES,
    )
    outcomes = {"VALID": 80, "SCHEMA_VIOLATION": 5, "TRANSPORT_FAILURE": 10, "TIMEOUT": 5}
    result = classify_defects(gates, regressions, outcomes, max_failure_rate=0.1)
    by_type = {}
    for defect in result["defects"]:
        by_type.setdefault(defect["type"], []).append(defect["metric_key"])
    assert by_type == {
        "QUALITY_MISS": ["critical_miss_count"],
        "SCHEMA": ["schema_violation_rate"],
        "UNMEASURED": ["hit_at_5", "dept_pipeline_exact"],
        "QUALITY_REGRESSION": ["urgency_macro_f1"],
        "LATENCY_REGRESSION": ["latency_p95_ms"],
        "COST_REGRESSION": ["cost_usd"],
        "FAILURE_RATE": ["transport_failure_rate"],
    }
    failure = result["defects"][-1]
    assert failure["value"] == pytest.approx(0.15)
    assert failure["basis"] == {"failed": 15, "total": 100, "max_failure_rate": 0.1}
    assert result["blocker"] is True
    unmeasured = classify_defects(evaluate_gates([], []), {"items": []}, {}, max_failure_rate=0.1)
    assert [d["type"] for d in unmeasured["defects"]] == ["UNMEASURED"]
    assert unmeasured["blocker"] is False
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        classify_defects(gates, regressions, outcomes, max_failure_rate=1.5)
