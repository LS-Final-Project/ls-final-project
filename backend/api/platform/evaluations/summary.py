"""승인된 합성 실행의 내용 없는 사용량·응답 감사만 공개한다."""

from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import check_actor, validate_smoke_experiment
from backend.api.platform.evaluations.cs_smoke import FIXTURES, matches_profile
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings
from eval.cs_response_audit import summarize_stored_response_audit
from eval.cs_usage import summarize_stored_usage


class TokenTotal(BaseModel):
    """관측 부분합과 누락 없는 전체 합계를 구별한다."""

    model_config = ConfigDict(extra="forbid")
    reported_count: int
    missing_count: int
    known_sum: int | None
    total: int | None


class SummaryTokens(BaseModel):
    """합산 의미가 다른 토큰 필드를 분리한다."""

    input_tokens: TokenTotal
    output_tokens: TokenTotal
    thought_tokens: TokenTotal
    cached_input_tokens: TokenTotal
    total_tokens: TokenTotal


class SummaryLatency(BaseModel):
    """저장된 지연 표본 수와 선형 보간 분위수."""

    reported_count: int
    missing_count: int
    p50: float | None
    p95: float | None


class AttemptSummary(BaseModel):
    """최초·재시도·전체별로 공개할 수치 필드만 지정한다."""

    model_config = ConfigDict(extra="forbid")
    stored_attempt_count: int
    unfinished_attempt_count: int
    tokens: SummaryTokens
    latency_ms: SummaryLatency
    stored_raw_response_count: int
    schema_violation_count: int
    schema_violation_rate: float | None
    incomplete_evidence_attempt_count: int


class EvaluationRunSummary(BaseModel):
    """품질 채점이 아닌 합성 저장 기록 집계임을 명시한다."""

    model_config = ConfigDict(extra="forbid")
    report_version: Literal["cs-run-summary-v1"] = "cs-run-summary-v1"
    run_id: UUID
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "TIMED_OUT"]
    generated_at: datetime
    data_origin: Literal["synthetic"] = "synthetic"
    quality_available: Literal[False] = False
    quality_unavailable_reason: Literal["GOLD_NOT_CONNECTED"] = "GOLD_NOT_CONNECTED"
    target_count: int
    targets_without_first_attempt: int
    first: AttemptSummary
    retry: AttemptSummary
    all: AttemptSummary


def get_run_summary(
    settings: Settings, access: EvaluationAccessSettings, actor: UUID, run_id: UUID
) -> EvaluationRunSummary:
    """소유권·승인·집계를 한 읽기 전용 스냅샷에서 확인한다.

    Args:
        settings: local/test 환경과 플랫폼 DB 연결.
        access: 현재 평가자·구성 승인 목록.
        actor: 현재 세션을 검증한 내부 사용자.
        run_id: 조회할 합성 실행 UUID.

    Returns:
        EvaluationRunSummary: 내용 없는 집계. DB·집계 실패는 안전한503으로 반환한다.
    """
    check_actor(settings, access, actor)
    dsn = settings.database_url.get_secret_value() if settings.database_url else ""
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL statement_timeout='5s'")
            with conn.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    "SELECT e.*,r.provider,r.runner_kind,r.status,transaction_timestamp() AS generated_at "
                    "FROM evaluation.runs r JOIN evaluation.experiments e ON e.id=r.experiment_id "
                    "JOIN evaluation.admission_receipts a ON a.run_id=r.id "
                    "WHERE r.id=%s AND r.requested_by_user_id=%s AND e.created_by_user_id=%s",
                    (run_id, actor, actor),
                ).fetchone()
            if row is None or row["data_split"] == "test":
                raise ApplicationError(404, "NOT_FOUND", "Run is unavailable.")
            validate_smoke_experiment(row, access)
            if (
                not matches_profile(row)
                or row["provider"] != "fake"
                or row["runner_kind"] not in {"local", "test"}
            ):
                raise ApplicationError(403, "LOCAL_SMOKE_ONLY", "Summary is unavailable.")
            with conn.cursor(row_factory=dict_row) as cursor:
                targets = cursor.execute(
                    "SELECT target_ref,data_origin,attributes FROM evaluation.experiment_targets "
                    "WHERE experiment_id=%s",
                    (row["id"],),
                ).fetchall()
            if {target["target_ref"] for target in targets} != set(FIXTURES) or any(
                target["data_origin"] != "synthetic" or target["attributes"] != {}
                for target in targets
            ):
                raise ApplicationError(409, "TARGET_SET_MISMATCH", "Targets differ.")
            usage = summarize_stored_usage(conn, run_id)
            audit = summarize_stored_response_audit(conn, run_id)
            groups = {}
            for group in ("first", "retry", "all"):
                groups[group] = AttemptSummary(
                    **{
                        key: usage[group][key]
                        for key in (
                            "stored_attempt_count",
                            "unfinished_attempt_count",
                            "tokens",
                            "latency_ms",
                        )
                    },
                    **{
                        key: audit[group][key]
                        for key in (
                            "stored_raw_response_count",
                            "schema_violation_count",
                            "schema_violation_rate",
                            "incomplete_evidence_attempt_count",
                        )
                    },
                )
            return EvaluationRunSummary(
                run_id=run_id,
                status=row["status"],
                generated_at=row["generated_at"],
                target_count=usage["target_count"],
                targets_without_first_attempt=usage["targets_without_first_attempt"],
                **groups,
            )
    except (psycopg.Error, ValueError):
        raise HTTPException(503, "Evaluation summary is unavailable") from None
