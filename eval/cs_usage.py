"""저장된 분류 시도의 사용량을 읽는다. 실제 호출 수나 미보고 비용은 추정하지 않는다."""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from eval.cs_metrics import OUTCOMES

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "thought_tokens",
    "cached_input_tokens",
    "total_tokens",
)


def summarize_stored_usage(conn: Connection, run_id: UUID) -> dict:
    """고정 대상의 최초·재시도·전체 사용량을 같은 조회 시점에서 집계한다.

    Args:
        conn: 호출자가 실행 조회 권한을 확인한 내부 DB 연결.
        run_id: 사용량을 읽을 실행 ID.

    Returns:
        dict: 미보고와 0을 구분한 사용량·비용·시도 지연 집계.
            원시 출력·정답을 읽지 않으며 DB 오류를 성공으로 바꾸지 않는다.
    """
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT r.status,r.requested_count,t.target_ref,a.id AS attempt_id,a.attempt,"
            "a.outcome,a.finished_at,a.latency_ms,a.input_tokens,a.output_tokens,"
            "a.thought_tokens,a.cached_input_tokens,a.total_tokens,a.cost_usd,a.pricing_version,"
            "a.usage_raw IS NOT NULL AS has_usage_raw,a.requested_model,a.call_attempts "
            "FROM evaluation.runs r "
            "LEFT JOIN evaluation.experiment_targets t ON t.experiment_id=r.experiment_id "
            "LEFT JOIN evaluation.cases c ON c.run_id=r.id AND c.target_ref=t.target_ref "
            "LEFT JOIN evaluation.case_attempts a ON a.case_id=c.id AND a.step='classify' "
            "WHERE r.id=%s ORDER BY t.target_ref,a.attempt",
            (run_id,),
        ).fetchall()
    if not rows:
        raise ValueError("CS_USAGE_INVALID_INPUT")
    targets = {row["target_ref"] for row in rows if row["target_ref"] is not None}
    if rows[0]["requested_count"] != len(targets):
        raise ValueError("CS_USAGE_INVALID_INPUT")
    attempts = [row for row in rows if row["attempt_id"] is not None]
    for row in attempts:
        cost = row["cost_usd"]
        if cost is not None and (
            not isinstance(cost, Decimal)
            or not cost.is_finite()
            or cost < 0
            or not row["pricing_version"]
            or not row["pricing_version"].strip()
        ):
            raise ValueError("CS_USAGE_INVALID_INPUT")
    first = [row for row in attempts if row["attempt"] == 1]
    return {
        "version": "cs-stored-usage-v1",
        "run_id": str(run_id),
        "run_status": rows[0]["status"],
        "target_count": len(targets),
        "targets_without_first_attempt": len(targets - {row["target_ref"] for row in first}),
        "quality_gate_evaluated": False,
        "first": _summarize(first),
        "retry": _summarize([row for row in attempts if row["attempt"] > 1]),
        "all": _summarize(attempts),
    }


def _summarize(rows: list[dict]) -> dict:
    """저장된 시도 묶음의 값과 누락 수를 함께 반환한다.

    Args:
        rows: 최초 또는 재시도 또는 전체 저장 행.

    Returns:
        dict: 시도 상태, 토큰별 보고 범위, 지연 분위수, 비용과 가격표 판본, 요청 모델 목록,
            호출 시도 수, 사용량 원문 보고 수(원문 자체는 집계에 넣지 않는다).
    """
    latency = sorted(row["latency_ms"] for row in rows if row["latency_ms"] is not None)
    cost = _sum_report(rows, "cost_usd")
    # Decimal을 float로 반올림하지 않고 JSON에서도 정확한 USD 십진 문자열로 유지한다.
    for key in ("known_sum", "total"):
        if cost[key] is not None:
            cost[key] = format(cost[key], "f")
    cost["pricing_versions"] = sorted(
        {row["pricing_version"] for row in rows if row["cost_usd"] is not None}
    )
    return {
        "stored_attempt_count": len(rows),
        "unfinished_attempt_count": sum(row["finished_at"] is None for row in rows),
        "outcomes": {
            outcome: sum(row["outcome"] == outcome for row in rows) for outcome in sorted(OUTCOMES)
        },
        "tokens": {field: _sum_report(rows, field) for field in TOKEN_FIELDS},
        "latency_ms": {
            "reported_count": len(latency),
            "missing_count": len(rows) - len(latency),
            "p50": _percentile(latency, 0.5),
            "p95": _percentile(latency, 0.95),
        },
        "cost_usd": cost,
        "requested_models": sorted(
            {row["requested_model"] for row in rows if row["requested_model"] is not None}
        ),
        "call_attempts": _sum_report(rows, "call_attempts"),
        "usage_raw": {
            "reported_count": sum(row["has_usage_raw"] for row in rows),
            "missing_count": sum(not row["has_usage_raw"] for row in rows),
        },
    }


def sum_report(rows: list[dict], field: str) -> dict:
    """미보고가 섞이면 완전한 총액 대신 확인된 부분합만 제공한다.

    Args:
        rows: 집계 대상 행.
        field: DB의 수량 필드.

    Returns:
        dict: 보고/누락 수, 알려진 부분합과 전건 보고 시에만 존재하는 합계.
    """
    values = [row[field] for row in rows if row[field] is not None]
    known = sum(values) if values else None
    return {
        "reported_count": len(values),
        "missing_count": len(rows) - len(values),
        "known_sum": known,
        "total": known if values and len(values) == len(rows) else None,
    }


def percentile(values: Sequence[float], q: float) -> float | None:
    """관측값의 (n-1)q 위치를 선형 보간한다.

    Args:
        values: 오름차순 지연 값(정수·실수).
        q: 0에서 1 사이 분위수.

    Returns:
        float | None: 관측값이 없으면 None, 한 개면 그 값.
    """
    if not values:
        return None
    position = (len(values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


# 다른 평가 모듈이 쓰던 밑줄 이름은 별칭으로 남긴다.
_sum_report = sum_report
_percentile = percentile
