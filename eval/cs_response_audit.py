"""원시 내용 없이 저장 증거의 존재 여부로 분류 응답과 사례 폴백을 집계한다."""

from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from eval.cs_metrics import OUTCOMES


def summarize_stored_response_audit(conn: Connection, run_id: UUID) -> dict:
    """최초·재시도 응답 분모와 사례 단위 폴백 기록을 분리한다.

    Args:
        conn: 호출자가 실행 조회 권한을 확인한 내부 DB 연결.
        run_id: 감사 집계를 읽을 실행.

    Returns:
        dict: 내용 없는 응답/출력 존재 건수·스키마 위반율·폴백 기록.
            증거 누락/모순 또는 미종료 시도가 있으면 위반율은 None이다.
    """
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT r.status,r.requested_count,t.target_ref,c.id AS case_id,"
            "c.status AS case_status,c.fallback_used,a.id AS attempt_id,a.attempt,a.outcome,"
            "a.finished_at,(a.raw_output IS NOT NULL) AS has_raw,"
            "(a.parsed_output IS NOT NULL) AS has_parsed "
            "FROM evaluation.runs r "
            "LEFT JOIN evaluation.experiment_targets t ON t.experiment_id=r.experiment_id "
            "LEFT JOIN evaluation.cases c ON c.run_id=r.id AND c.target_ref=t.target_ref "
            "LEFT JOIN evaluation.case_attempts a ON a.case_id=c.id AND a.step='classify' "
            "WHERE r.id=%s ORDER BY t.target_ref,a.attempt",
            (run_id,),
        ).fetchall()
    if not rows:
        raise ValueError("CS_RESPONSE_AUDIT_INVALID_INPUT")
    cases = {row["target_ref"]: row for row in rows if row["target_ref"] is not None}
    if rows[0]["requested_count"] != len(cases):
        raise ValueError("CS_RESPONSE_AUDIT_INVALID_INPUT")
    attempts = [row for row in rows if row["attempt_id"] is not None]
    first = [row for row in attempts if row["attempt"] == 1]
    return {
        "version": "cs-response-audit-v1",
        "run_id": str(run_id),
        "run_status": rows[0]["status"],
        "target_count": len(cases),
        "targets_without_first_attempt": len(set(cases) - {row["target_ref"] for row in first}),
        "quality_gate_evaluated": False,
        "first": _summarize(first),
        "retry": _summarize([row for row in attempts if row["attempt"] > 1]),
        "all": _summarize(attempts),
        "case_fallback": {
            "scope": "case",
            "target_count": len(cases),
            "recorded_used": sum(row["fallback_used"] is True for row in cases.values()),
            "recorded_not_used": sum(row["fallback_used"] is False for row in cases.values()),
            "missing_case": sum(row["case_id"] is None for row in cases.values()),
            "pending_case": sum(row["case_status"] == "PENDING" for row in cases.values()),
        },
    }


def _summarize(rows: list[dict]) -> dict:
    """저장 상태별 원시 증거와 스키마 위반 분모의 완전성을 확인한다.

    Args:
        rows: 선택된 분류 시도 행. 원문 대신 존재 여부만 포함한다.

    Returns:
        dict: 위반 수/저장 원시 응답 수와 증거 문제 건수. 문제 유형은 중복될 수 있다.
    """
    issues = dict.fromkeys(
        ("missing_raw", "unexpected_raw", "inconsistent_parsed", "unfinished"), 0
    )
    incomplete = 0
    for row in rows:
        flags = {
            "missing_raw": row["outcome"] in {"VALID", "SCHEMA_VIOLATION"} and not row["has_raw"],
            "unexpected_raw": row["outcome"] not in {"VALID", "SCHEMA_VIOLATION"}
            and row["has_raw"],
            "inconsistent_parsed": (row["outcome"] == "VALID") != row["has_parsed"],
            "unfinished": row["finished_at"] is None,
        }
        for key, flag in flags.items():
            issues[key] += flag
        incomplete += any(flags.values())
    responses = sum(row["has_raw"] for row in rows)
    violations = sum(row["outcome"] == "SCHEMA_VIOLATION" for row in rows)
    return {
        "stored_attempt_count": len(rows),
        "outcomes": {
            outcome: sum(row["outcome"] == outcome for row in rows) for outcome in sorted(OUTCOMES)
        },
        "stored_raw_response_count": responses,
        "stored_parsed_output_count": sum(row["has_parsed"] for row in rows),
        "schema_violation_count": violations,
        "schema_violation_rate": violations / responses if responses and not incomplete else None,
        "incomplete_evidence_attempt_count": incomplete,
        "evidence_issues": issues,
    }
