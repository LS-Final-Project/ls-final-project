"""출시 게이트 판정·퇴행·결함 분류(KAN-156). 파일·DB·모델에 접근하지 않는다.

합격선·허용 폭·실패율 상한은 이 모듈이 정하지 않는다(KAN-82에서 잠근다). 기본값 없이 입력으로만 받고,
값이 없으면 NOT_EVALUABLE이다. contracts/platform/evaluations.md의 "임의로 합격 처리하지 않는다"에 따라
blocker 게이트는 미달뿐 아니라 측정 불가도 출시를 막는다. 저장 시각(recorded_at)은 호출자가 붙인다.
"""

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

Comparison = Literal[">=", "<=", "=="]
Direction = Literal["higher_better", "lower_better"]
MetricKind = Literal["QUALITY", "COST", "LATENCY"]
SCHEMA_KEY = "schema_violation_rate"
FAILURE_KEY = "transport_failure_rate"
CRITICAL_LABELS = ("SAFETY", "SITE_DOWN")
REGRESSION_DEFECTS = {
    "QUALITY": "QUALITY_REGRESSION",
    "COST": "COST_REGRESSION",
    "LATENCY": "LATENCY_REGRESSION",
}


@dataclass(frozen=True)
class Gate:
    """지표 하나의 합격선. 값은 KAN-82에서 잠근 것을 호출자가 넘긴다."""

    metric_key: str
    comparison: Comparison
    threshold: float
    blocker: bool = True
    segment: str = "all"


@dataclass(frozen=True)
class MetricValue:
    """채점기 결과에서 꺼낸 지표 값과 분모. value None은 계산 불가다."""

    metric_key: str
    segment: str
    value: float | None
    support: int | None
    held: int = 0
    errors: int = 0


@dataclass(frozen=True)
class Tolerance:
    """지표 key의 퇴행 허용 폭. max_drop은 나빠진 방향의 절대 차이이며 key의 모든 segment에 적용한다."""

    metric_key: str
    direction: Direction
    max_drop: float
    kind: MetricKind


def _index(metrics: Iterable[MetricValue]) -> dict[tuple[str, str], MetricValue]:
    """지표를 (metric_key, segment)로 찾게 한다.

    Args:
        metrics: 지표 값 목록.

    Returns:
        dict: (key, segment) → 값. 형식이 틀리거나 같은 쌍이 둘이면 EVAL_INVALID_INPUT ValueError다.
    """
    index: dict[tuple[str, str], MetricValue] = {}
    for metric in metrics:
        pair = (metric.metric_key, metric.segment) if isinstance(metric, MetricValue) else None
        if pair is None or pair in index:
            raise ValueError("EVAL_INVALID_INPUT")
        index[pair] = metric
    return index


def _passes(value: float, comparison: Comparison, threshold: float) -> bool:
    """합격선 비교. '=='는 float 오차를 피하려고 math.isclose(abs_tol=1e-9)로 본다.

    Args:
        value: 지표 값.
        comparison: 비교 방식.
        threshold: 합격선.

    Returns:
        bool: 합격이면 True.
    """
    if comparison == ">=":
        return value >= threshold
    if comparison == "<=":
        return value <= threshold
    return math.isclose(value, threshold, rel_tol=0.0, abs_tol=1e-9)


def evaluate_gates(metrics: list[MetricValue], gates: list[Gate]) -> dict:
    """게이트마다 PASS·FAIL·NOT_EVALUABLE을 판정한다.

    Args:
        metrics: 지표 값. 같은 (metric_key, segment)는 하나만 허용한다.
        gates: 합격선. 같은 (metric_key, segment)는 하나만 허용한다.

    Returns:
        dict: gates(게이트별 판정·값·합격선·support·held·errors), 판정별 건수, blocker(blocker 게이트 중
            FAIL 또는 NOT_EVALUABLE이 하나라도 있으면 True). 값이 None이거나 지표가 없으면 NOT_EVALUABLE이며
            합격 처리하지 않는다. 잘못된 입력은 EVAL_INVALID_INPUT ValueError다.
    """
    index = _index(metrics)
    seen: set[tuple[str, str]] = set()
    rows = []
    for gate in gates:
        if (
            not isinstance(gate, Gate)
            or gate.comparison not in (">=", "<=", "==")
            or (gate.metric_key, gate.segment) in seen
            or not math.isfinite(gate.threshold)
        ):
            raise ValueError("EVAL_INVALID_INPUT")
        seen.add((gate.metric_key, gate.segment))
        metric = index.get((gate.metric_key, gate.segment))
        value = None if metric is None else metric.value
        status = (
            "NOT_EVALUABLE"
            if value is None
            else "PASS"
            if _passes(value, gate.comparison, gate.threshold)
            else "FAIL"
        )
        rows.append(
            {
                "metric_key": gate.metric_key,
                "segment": gate.segment,
                "status": status,
                "value": value,
                "comparison": gate.comparison,
                "threshold": gate.threshold,
                "blocker": gate.blocker,
                "support": None if metric is None else metric.support,
                "held": None if metric is None else metric.held,
                "errors": None if metric is None else metric.errors,
            }
        )
    counts = Counter(row["status"] for row in rows)
    return {
        "version": "release-gate-v1",
        "gates": rows,
        "counts": {status: counts[status] for status in ("PASS", "FAIL", "NOT_EVALUABLE")},
        "blocker": any(row["blocker"] and row["status"] != "PASS" for row in rows),
    }


def rare_risk_misses(urgency_summary: Mapping[str, Any]) -> dict:
    """긴급도 집계에서 CRITICAL 유형별 누락과 SAFETY↔SITE_DOWN 혼동을 꺼낸다.

    Args:
        urgency_summary: eval.cs_metrics.summarize_urgency_first_attempts 결과.

    Returns:
        dict: classes(유형별 support·missed·status), 합계, 상호 혼동, unmeasured 목록. support가 0인 유형은
            UNMEASURED로 표시해 0건 누락을 성공으로 읽지 않게 한다. 형식이 틀리면 EVAL_INVALID_INPUT ValueError다.
    """
    try:
        critical = urgency_summary["critical"]
        by_gold = {label: critical["by_gold"][label] for label in CRITICAL_LABELS}
        classes = {
            label: {
                "support": int(row["support"]),
                "missed": int(row["missed"]),
                "status": "MEASURED" if row["support"] else "UNMEASURED",
            }
            for label, row in by_gold.items()
        }
        confusion = {
            "safety_as_site_down": int(critical["safety_as_site_down"]),
            "site_down_as_safety": int(critical["site_down_as_safety"]),
        }
    except (KeyError, TypeError, ValueError):
        raise ValueError("EVAL_INVALID_INPUT") from None
    return {
        "classes": classes,
        "support": sum(row["support"] for row in classes.values()),
        "missed": sum(row["missed"] for row in classes.values()),
        "confusion": confusion,
        "unmeasured": [label for label, row in classes.items() if row["status"] == "UNMEASURED"],
    }


def detect_regressions(
    current: list[MetricValue], baseline: list[MetricValue], *, tolerances: list[Tolerance]
) -> dict:
    """허용 폭이 있는 지표만 기준 실행과 비교해 퇴행을 찾는다.

    Args:
        current: 이번 실행 지표.
        baseline: 기준 실행 지표.
        tolerances: key별 방향·허용 폭·종류(QUALITY·COST·LATENCY). 토큰·비용·지연도 같은 구조로 넘긴다.

    Returns:
        dict: items(key·segment별 status·값·delta·나빠진 폭), 상태별 건수. 나빠진 폭이 max_drop을 넘으면
            REGRESSION, 아니면 OK다. 이번 값이 없거나 None이면 NOT_EVALUABLE, 기준 값이 없거나 None이면
            NO_BASELINE이다. 잘못된 입력은 EVAL_INVALID_INPUT ValueError다.
    """
    now, before = _index(current), _index(baseline)
    keys = [t.metric_key if isinstance(t, Tolerance) else None for t in tolerances]
    if len(set(keys)) != len(keys) or any(
        key is None
        or t.direction not in ("higher_better", "lower_better")
        or t.kind not in REGRESSION_DEFECTS
        or not math.isfinite(t.max_drop)
        or t.max_drop < 0
        for key, t in zip(keys, tolerances, strict=True)
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    items = []
    for tolerance in tolerances:
        key = tolerance.metric_key
        segments = sorted({s for k, s in (*now, *before) if k == key}) or ["all"]
        for segment in segments:
            value = getattr(now.get((key, segment)), "value", None)
            base = getattr(before.get((key, segment)), "value", None)
            delta = worse = None
            if value is None:
                status = "NOT_EVALUABLE"
            elif base is None:
                status = "NO_BASELINE"
            else:
                delta = value - base
                worse = -delta if tolerance.direction == "higher_better" else delta
                status = "REGRESSION" if worse > tolerance.max_drop else "OK"
            items.append(
                {
                    "metric_key": key,
                    "segment": segment,
                    "kind": tolerance.kind,
                    "direction": tolerance.direction,
                    "status": status,
                    "value": value,
                    "baseline": base,
                    "delta": delta,
                    "worsened_by": worse,
                    "max_drop": tolerance.max_drop,
                }
            )
    counts = Counter(item["status"] for item in items)
    return {
        "items": items,
        "counts": {s: counts[s] for s in ("OK", "REGRESSION", "NO_BASELINE", "NOT_EVALUABLE")},
    }


def classify_defects(
    gate_result: Mapping[str, Any],
    regression_result: Mapping[str, Any],
    outcomes: Mapping[str, int],
    *,
    max_failure_rate: float,
) -> dict:
    """게이트·퇴행·호출 결과를 결함 유형으로 나눈다. 심각도는 두지 않고 blocker 여부만 옮긴다.

    Args:
        gate_result: evaluate_gates 결과.
        regression_result: detect_regressions 결과.
        outcomes: 시도 결과별 건수(VALID·SCHEMA_VIOLATION·TRANSPORT_FAILURE·TIMEOUT·HELD 등).
        max_failure_rate: (TRANSPORT_FAILURE + TIMEOUT) / 전체의 상한. KAN-82에서 잠근 값을 넘긴다.

    Returns:
        dict: defects(type·metric_key·segment·value·blocker·basis), 유형별 건수, blocker(게이트 결과 그대로).
            유형은 QUALITY_MISS(게이트 FAIL), SCHEMA(schema_violation_rate 게이트 FAIL, QUALITY_MISS와 중복
            없음), UNMEASURED(게이트 NOT_EVALUABLE·퇴행 NOT_EVALUABLE·시도 0건), QUALITY/COST/LATENCY_REGRESSION
            (Tolerance.kind), FAILURE_RATE(상한 초과)다. 게이트에서 오지 않은 항목의 blocker는 None이다.
            잘못된 입력은 EVAL_INVALID_INPUT ValueError다.
    """
    if (
        not math.isfinite(max_failure_rate)
        or not 0 <= max_failure_rate <= 1
        or any(type(n) is not int or n < 0 for n in outcomes.values())
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    defects: list[dict[str, Any]] = []
    try:
        for row in gate_result["gates"]:
            if row["status"] == "PASS":
                continue
            kind = (
                "UNMEASURED"
                if row["status"] == "NOT_EVALUABLE"
                else "SCHEMA"
                if row["metric_key"] == SCHEMA_KEY
                else "QUALITY_MISS"
            )
            defects.append(
                {
                    "type": kind,
                    "metric_key": row["metric_key"],
                    "segment": row["segment"],
                    "value": row["value"],
                    "blocker": row["blocker"],
                    "basis": {"comparison": row["comparison"], "threshold": row["threshold"]},
                }
            )
        for item in regression_result["items"]:
            if item["status"] not in ("REGRESSION", "NOT_EVALUABLE"):
                continue
            defects.append(
                {
                    "type": (
                        REGRESSION_DEFECTS[item["kind"]]
                        if item["status"] == "REGRESSION"
                        else "UNMEASURED"
                    ),
                    "metric_key": item["metric_key"],
                    "segment": item["segment"],
                    "value": item["value"],
                    "blocker": None,
                    "basis": {
                        "baseline": item["baseline"],
                        "delta": item["delta"],
                        "max_drop": item["max_drop"],
                        "direction": item["direction"],
                    },
                }
            )
        blocker = bool(gate_result["blocker"])
    except (KeyError, TypeError):
        raise ValueError("EVAL_INVALID_INPUT") from None
    total = sum(outcomes.values())
    failed = outcomes.get("TRANSPORT_FAILURE", 0) + outcomes.get("TIMEOUT", 0)
    rate = failed / total if total else None
    if rate is None or rate > max_failure_rate:
        defects.append(
            {
                "type": "UNMEASURED" if rate is None else "FAILURE_RATE",
                "metric_key": FAILURE_KEY,
                "segment": "all",
                "value": rate,
                "blocker": None,
                "basis": {"failed": failed, "total": total, "max_failure_rate": max_failure_rate},
            }
        )
    return {
        "defects": defects,
        "counts": dict(sorted(Counter(d["type"] for d in defects).items())),
        "blocker": blocker,
    }
