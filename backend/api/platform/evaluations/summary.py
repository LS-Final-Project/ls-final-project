"""승인된 합성 실행의 내용 없는 사용량·응답 감사와 합성 품질 집계만 공개한다."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import check_actor, validate_smoke_experiment
from backend.api.platform.evaluations.cs_smoke import FIXTURES, matches_profile
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings
from eval.cs_metrics import OUTCOMES, RETURN_SUBTYPE_LABELS
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


UsdAmount = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$", max_length=128)]


class SummaryCost(BaseModel):
    """가격표 문자열 없이 정확한 USD 부분합·합계와 보고 범위를 제공한다."""

    model_config = ConfigDict(extra="forbid")
    reported_count: int
    missing_count: int
    known_sum: UsdAmount | None
    total: UsdAmount | None
    pricing_version_count: int


class AttemptSummary(BaseModel):
    """최초·재시도·전체별로 공개할 수치 필드만 지정한다."""

    model_config = ConfigDict(extra="forbid")
    stored_attempt_count: int
    unfinished_attempt_count: int
    tokens: SummaryTokens
    latency_ms: SummaryLatency
    cost_usd: SummaryCost | None = None
    stored_raw_response_count: int
    schema_violation_count: int
    schema_violation_rate: float | None
    incomplete_evidence_attempt_count: int


RunStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "TIMED_OUT"]
QUALITY_PREFIX = "return_subtype_first_"
QUALITY_ROWS = {
    ("accuracy", None),
    ("macro_f1", None),
    ("null_predictions", None),
    *(("f1", label) for label in RETURN_SUBTYPE_LABELS),
}
QUALITY_DETAILS = {
    "report_version",
    "data_origin",
    "gold_version",
    "gold_sha256",
    "run_status",
    "target_count",
    "scored_target_count",
    "outcomes",
}
Ratio = Annotated[float, Field(ge=0, le=1)]
Count = Annotated[int, Field(ge=0)]


class SyntheticClassQuality(BaseModel):
    """한 하위유형 클래스의 저장된 수치."""

    model_config = ConfigDict(extra="forbid")
    support: Count
    tp: Count
    fp: Count
    fn: Count
    precision: Ratio
    recall: Ratio
    f1: Ratio


class SyntheticQuality(BaseModel):
    """합성 정답으로 저장한 반품 하위유형 최초 집계. 실제 품질·합격 판정이 아니다."""

    model_config = ConfigDict(extra="forbid")
    metric: Literal["return_subtype"] = "return_subtype"
    attempt: Literal["first"] = "first"
    data_origin: Literal["synthetic"] = "synthetic"
    quality_gate_evaluated: Literal[False] = False
    scoring_version: str
    stored_scoring_version_count: Annotated[int, Field(ge=1)]
    stored_at: datetime
    gold_version: str | None
    gold_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    run_status_at_scoring: RunStatus
    target_count: Count
    scored_target_count: Count
    correct: Count
    accuracy: Ratio | None
    macro_f1: Ratio | None
    null_predictions: Count
    outcomes: dict[
        Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD", "NOT_STARTED"],
        Count,
    ]
    per_class: dict[Literal["DEFECT", "NON_DEFECT"], SyntheticClassQuality]


class EvaluationRunSummary(BaseModel):
    """품질 채점이 아닌 합성 저장 기록 집계임을 명시한다."""

    model_config = ConfigDict(extra="forbid")
    report_version: Literal["cs-run-summary-v1"] = "cs-run-summary-v1"
    run_id: UUID
    status: RunStatus
    generated_at: datetime
    data_origin: Literal["synthetic"] = "synthetic"
    quality_available: Literal[False] = False
    quality_unavailable_reason: Literal["GOLD_NOT_CONNECTED"] = "GOLD_NOT_CONNECTED"
    target_count: int
    targets_without_first_attempt: int
    first: AttemptSummary
    retry: AttemptSummary
    all: AttemptSummary
    synthetic_quality: SyntheticQuality | None = None


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
                cost = usage[group]["cost_usd"]
                groups[group] = AttemptSummary(
                    cost_usd=SummaryCost(
                        reported_count=cost["reported_count"],
                        missing_count=cost["missing_count"],
                        known_sum=cost["known_sum"],
                        total=cost["total"],
                        pricing_version_count=len(cost["pricing_versions"]),
                    ),
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
                synthetic_quality=_read_synthetic_quality(conn, run_id, row["gold_version"]),
                **groups,
            )
    except (psycopg.Error, ValueError):
        raise HTTPException(503, "Evaluation summary is unavailable") from None


def _read_synthetic_quality(
    conn: psycopg.Connection, run_id: UUID, gold_version: str | None
) -> SyntheticQuality | None:
    """권한 검사를 통과한 같은 스냅샷에서 최신 합성 품질 판본의 실행 단위 행만 읽는다.

    Args:
        conn: 요약과 같은 읽기 전용 트랜잭션의 연결.
        run_id: 조회 중인 합성 실행.
        gold_version: 실험의 정답 판본. 저장 행의 판본과 같아야 한다.

    Returns:
        SyntheticQuality | None: 저장 행이 없으면 None. 행 구성·수치가 저장 계약과 다르면
            ValueError로 거부한다. 정답 파일·사례 정답·예측은 읽지 않는다.
    """
    pattern = QUALITY_PREFIX.replace("_", "\\_") + "%"
    with conn.cursor(row_factory=dict_row) as cursor:
        # 추가 전용 재채점은 새 판본을 쌓으므로 가장 최근 판본 하나를 보이고 판본 수를 함께 낸다.
        versions = cursor.execute(
            "SELECT scoring_version FROM evaluation.metrics "
            "WHERE run_id=%s AND case_id IS NULL AND metric_key LIKE %s "
            "GROUP BY scoring_version ORDER BY max(created_at) DESC, scoring_version DESC",
            (run_id, pattern),
        ).fetchall()
        if not versions:
            return None
        rows = cursor.execute(
            "SELECT metric_key,class_label,segment,value,numerator,denominator,support,details,"
            "threshold,comparison,passed,created_at FROM evaluation.metrics "
            "WHERE run_id=%s AND case_id IS NULL AND scoring_version=%s AND metric_key LIKE %s",
            (run_id, versions[0]["scoring_version"], pattern),
        ).fetchall()
    by_key = {(r["metric_key"].removeprefix(QUALITY_PREFIX), r["class_label"]): r for r in rows}
    if (
        len(rows) != len(QUALITY_ROWS)
        or set(by_key) != QUALITY_ROWS
        or any(
            r["segment"] != "all"
            or r["threshold"] is not None
            or r["comparison"] is not None
            or r["passed"] is not None
            for r in rows
        )
    ):
        raise ValueError("SYNTHETIC_QUALITY_INVALID")
    accuracy, macro, nulls = (
        by_key[(key, None)] for key in ("accuracy", "macro_f1", "null_predictions")
    )
    common = accuracy["details"]
    if (
        set(common) != QUALITY_DETAILS
        or macro["details"] != common
        or nulls["details"] != common
        or common["report_version"] != "cs-return-subtype-first-attempt-v1"
        or common["data_origin"] != "synthetic"
        or common["gold_version"] != gold_version
        or set(common["outcomes"]) != OUTCOMES | {"NOT_STARTED"}
    ):
        raise ValueError("SYNTHETIC_QUALITY_INVALID")
    per_class = {
        label: SyntheticClassQuality(
            support=by_key[("f1", label)]["support"],
            f1=by_key[("f1", label)]["value"],
            **by_key[("f1", label)]["details"],
        )
        for label in RETURN_SUBTYPE_LABELS
    }
    scored = common["scored_target_count"]
    if (
        accuracy["denominator"] != scored
        or nulls["denominator"] != scored
        or nulls["numerator"] != nulls["value"]
        or sum(item.support for item in per_class.values()) != scored
        or sum(common["outcomes"].values()) != scored
    ):
        raise ValueError("SYNTHETIC_QUALITY_INVALID")
    return SyntheticQuality(
        scoring_version=versions[0]["scoring_version"],
        stored_scoring_version_count=len(versions),
        stored_at=accuracy["created_at"],
        gold_version=common["gold_version"],
        gold_sha256=common["gold_sha256"],
        run_status_at_scoring=common["run_status"],
        target_count=common["target_count"],
        scored_target_count=scored,
        correct=accuracy["numerator"],
        accuracy=accuracy["value"],
        macro_f1=macro["value"],
        null_predictions=nulls["value"],
        outcomes=common["outcomes"],
        per_class=per_class,
    )
