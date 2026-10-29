"""승인된 본인 실행의 내용 없는 사용량·응답 감사와 저장된 품질 집계만 공개한다."""

import hashlib
import json
import logging
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import (
    canonical_hash,
    check_actor,
    config_payload,
    validate_smoke_experiment,
)
from backend.api.platform.evaluations.cs_smoke import FIXTURES, matches_profile
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings
from eval.cs_gold_locks import GOLD_BUNDLE_ID, GOLD_LOCKS
from eval.cs_metrics import OUTCOMES, RETURN_SUBTYPE_LABELS
from eval.cs_report import LOCKED_LABELS, LOCKED_REPORT_VERSION
from eval.cs_response_audit import summarize_stored_response_audit
from eval.cs_usage import summarize_stored_usage

logger = logging.getLogger(__name__)


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
COMPARISON_KEY_VERSION = "cs-comparison-key-v1"
# eval.cs_stored_metrics.compare_stored_runs의 비교 가능 조건과 같다. 바꾸면 키 판본도 올린다.
COMPARISON_FIELDS = (
    "purpose",
    "data_origin",
    "dataset_hash",
    "data_split",
    "gold_version",
    "target_set_hash",
    "policy_version",
)
Ratio = Annotated[float, Field(ge=0, le=1)]
Count = Annotated[int, Field(ge=0)]
Outcomes = dict[
    Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD", "NOT_STARTED"],
    Count,
]


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
    outcomes: Outcomes
    per_class: dict[Literal["DEFECT", "NON_DEFECT"], SyntheticClassQuality]


# 잠금 저장 행(eval.cs_report.store_locked_quality_metrics)의 지표별 segment·집계 행. 클래스 F1 행은 따로 센다.
LOCKED_ROWS: dict[str, dict[str, tuple[str, ...]]] = {
    "category": {"all": ("accuracy", "macro_f1")},
    "urgency": {"all": ("accuracy", "macro_f1", "non_none_recall", "critical_missed")},
    "secondary": {"all": ("accuracy", "composite_macro_f1", "invented")},
    "return_subtype": {"all": ("accuracy", "macro_f1", "null_predictions")},
    "provided_info": {
        "scope=FULL": ("precision", "recall", "f1", "exact_match"),
        "scope=PRECISION_ONLY": ("precision",),
    },
}
LOCKED_DETAILS = {
    "report_version",
    "bundle_id",
    "gold_version",
    "gold_sha256",
    "data_origin",
    "data_split",
    "run_status",
    "locked_denominator",
    "target_count",
    "scored_target_count",
    "excluded_by_reason",
    "outcomes",
}
INFO_DETAILS = {"scope", "scope_target_count", "valid_count", "items", "recall_scored"}
# segment마다 다른 값을 뺀 지표 공통 details. 지표 안의 모든 segment에서 같아야 한다.
SHARED_DETAILS = LOCKED_DETAILS - {"locked_denominator", "excluded_by_reason", "outcomes"}


class LockedValue(BaseModel):
    """잠금 집계 행 하나의 저장 수치. 건수 지표(critical_missed 등)는 1을 넘을 수 있다."""

    model_config = ConfigDict(extra="forbid")
    value: Annotated[float, Field(ge=0)] | None
    numerator: Count | None
    denominator: Count | None
    support: Count | None


class LockedSegment(BaseModel):
    """한 채점 범위의 고정 분모·결과 분포·집계. provided_info 외에는 segment가 all 하나다."""

    model_config = ConfigDict(extra="forbid")
    locked_denominator: Count
    target_count: Count
    outcomes: Outcomes
    excluded_by_reason: dict[str, Count]
    values: dict[str, LockedValue]
    per_class: dict[str, SyntheticClassQuality]
    items: dict[Literal["tp", "fp", "fn"], Count] | None


class LockedMetric(BaseModel):
    """KAN-95 잠금 정답으로 저장한 한 지표의 최신 채점 판본. 합격 판정이 아니다."""

    model_config = ConfigDict(extra="forbid")
    scoring_version: str
    stored_scoring_version_count: Annotated[int, Field(ge=1)]
    stored_at: datetime
    gold_version: str
    gold_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    run_status_at_scoring: RunStatus
    target_count: Count
    scored_target_count: Count
    segments: dict[str, LockedSegment]


class LockedMetrics(BaseModel):
    """다섯 지표를 모두 키로 두고 저장되지 않은 지표는 null이다."""

    model_config = ConfigDict(extra="forbid")
    category: LockedMetric | None
    urgency: LockedMetric | None
    secondary: LockedMetric | None
    return_subtype: LockedMetric | None
    provided_info: LockedMetric | None


class LockedQuality(BaseModel):
    """실험이 정답 묶음에 연결된 실행의 최초 시도 잠금 품질."""

    model_config = ConfigDict(extra="forbid")
    bundle_id: str
    attempt: Literal["first"] = "first"
    quality_gate_evaluated: Literal[False] = False
    metrics: LockedMetrics


# 검색·초안·RFP 채점 행의 metric_key 접두와 단계. 분류는 기존 품질 블록이 다루므로 넣지 않는다.
STAGE_PREFIXES: dict[str, Literal["search", "draft", "rfp"]] = {
    "search_": "search",
    "draft_": "draft",
    "rfp_": "rfp",
}
# details에서 공개하는 키. 정수 건수만 내고 그 밖의 키·값은 버린다(원문성 값 차단).
STAGE_DETAIL_KEYS = ("tp", "fp", "fn")


class StageMetric(BaseModel):
    """단계 채점 행 하나의 저장 값. 합격 판정은 화면이 내리지 않는다."""

    model_config = ConfigDict(extra="forbid")
    metric_key: str
    segment: str
    class_label: str | None
    value: float | None
    support: Count | None
    threshold: float | None
    comparison: Literal[">=", "<=", "="] | None
    passed: bool | None
    details: dict[Literal["tp", "fp", "fn"], Count]
    quality_gate_evaluated: Literal[False] = False


class StageMetricBlock(BaseModel):
    """한 단계의 최신 채점 판본 행 묶음."""

    model_config = ConfigDict(extra="forbid")
    stage: Literal["classification", "search", "draft", "rfp"]
    scoring_version: str
    stored_scoring_version_count: Annotated[int, Field(ge=1)]
    stored_at: datetime
    items: list[StageMetric]


class EvaluationRunSummary(BaseModel):
    """저장 기록 집계와 잠금 정답 품질 연결 여부를 함께 낸다."""

    model_config = ConfigDict(extra="forbid")
    report_version: Literal["cs-run-summary-v2"] = "cs-run-summary-v2"
    run_id: UUID
    status: RunStatus
    generated_at: datetime
    data_origin: Literal["synthetic", "demo", "real"]
    quality_available: bool
    quality_unavailable_reason: Literal["GOLD_NOT_CONNECTED", "GOLD_PARTIAL"] | None
    target_count: int
    targets_without_first_attempt: int
    first: AttemptSummary
    retry: AttemptSummary
    all: AttemptSummary
    synthetic_quality: SyntheticQuality | None = None
    locked_quality: LockedQuality | None = None
    comparison_key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    stage_metrics: list[StageMetricBlock] = Field(default_factory=list)


def get_run_summary(
    settings: Settings, access: EvaluationAccessSettings, actor: UUID, run_id: UUID
) -> EvaluationRunSummary:
    """소유권·승인·집계를 한 읽기 전용 스냅샷에서 확인한다.

    Args:
        settings: local/test 환경과 플랫폼 DB 연결.
        access: 현재 평가자·구성 승인 목록.
        actor: 현재 세션을 검증한 내부 사용자.
        run_id: 조회할 실행 UUID. 합성 smoke 또는 train/valid 비합성 실행이다.

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
            locked = row["purpose"] != "smoke"
            if locked:
                _validate_locked_run(row, access)
            else:
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
            refs = sorted(target["target_ref"] for target in targets)
            if locked:
                # 비합성 대상은 고정 목록이 없어 접수 때 동결한 대상 hash와 대조한다.
                mismatch = (
                    hashlib.sha256("\n".join(refs).encode()).hexdigest() != row["target_set_hash"]
                )
            else:
                mismatch = set(refs) != set(FIXTURES) or any(
                    target["data_origin"] != "synthetic" or target["attributes"] != {}
                    for target in targets
                )
            if mismatch:
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
            locked_quality = _read_locked_quality(conn, run_id, row) if locked else None
            stored = (
                sum(m is not None for m in dict(locked_quality.metrics).values())
                if locked_quality
                else 0
            )
            return EvaluationRunSummary(
                run_id=run_id,
                status=row["status"],
                generated_at=row["generated_at"],
                data_origin=row["data_origin"],
                quality_available=stored == len(LOCKED_ROWS),
                quality_unavailable_reason=None
                if stored == len(LOCKED_ROWS)
                else "GOLD_PARTIAL"
                if stored
                else "GOLD_NOT_CONNECTED",
                target_count=usage["target_count"],
                targets_without_first_attempt=usage["targets_without_first_attempt"],
                synthetic_quality=None
                if locked
                else _read_synthetic_quality(conn, run_id, row["gold_version"]),
                locked_quality=locked_quality,
                # 잠금 경로는 서버 비밀 HMAC 키 판본 도입 전까지 비교 키를 내지 않는다.
                comparison_key=None if locked else comparison_key(row),
                stage_metrics=_read_stage_metrics(conn, run_id),
                **groups,
            )
    except (psycopg.Error, ValueError):
        raise HTTPException(503, "Evaluation summary is unavailable") from None


def _validate_locked_run(experiment: dict, access: EvaluationAccessSettings) -> None:
    """잠금 품질을 저장할 수 있는 비합성 실행만 요약 대상으로 허용한다.

    Args:
        experiment: 실행 provider를 함께 조회한 실험 메타데이터. test split은 이미 거부했다.
        access: 현재 서버의 허용 구성 목록.

    Returns:
        None: 승인·구성 hash·경계를 모두 통과하면 반환하며 위반은 안전한 오류다.
    """
    if experiment["config_hash"] not in access.approved_config_hashes:
        raise ApplicationError(403, "EXPERIMENT_NOT_APPROVED", "Experiment is not approved.")
    # 저장 함수의 LOCKED_RUN_REQUIRED와 같은 경계에 split 제한을 더한다.
    if (
        experiment["purpose"] not in {"pipeline", "rule_replay"}
        or experiment["provider"] == "fake"
        or experiment["model_id"] == "fake"
        or experiment["data_split"] not in {"train", "valid"}
        or matches_profile(experiment)
    ):
        raise ApplicationError(403, "LOCKED_RUN_REQUIRED", "Summary is unavailable.")
    if canonical_hash(config_payload(experiment)) != experiment["config_hash"]:
        raise ApplicationError(409, "EXPERIMENT_HASH_MISMATCH", "Experiment hash differs.")


def comparison_key(experiment: dict) -> str:
    """비교 가능 조건 원값을 내보내지 않고 같은 조건인지 판단할 키를 만든다.

    Args:
        experiment: COMPARISON_FIELDS를 가진 실험 메타데이터.

    Returns:
        str: 키 판본과 조건 7개의 canonical JSON SHA-256 hex. 실행 상태와 무관하다.
    """
    payload = {
        "key_version": COMPARISON_KEY_VERSION,
        **{k: experiment[k] for k in COMPARISON_FIELDS},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _read_stage_metrics(conn: psycopg.Connection, run_id: UUID) -> list[StageMetricBlock]:
    """같은 스냅샷에서 검색·초안·RFP 실행 단위 채점 행을 한 번에 읽어 단계별 최신 판본으로 묶는다.

    Args:
        conn: 요약과 같은 읽기 전용 트랜잭션의 연결.
        run_id: 조회 중인 실행.

    Returns:
        list[StageMetricBlock]: STAGE_PREFIXES 순서의 단계 묶음. 행이 없는 단계는 빠지고, 저장 행이 없으면
            빈 목록이다. 단계마다 저장 시각이 가장 늦은 채점 판본 하나만 쓰고(합성 품질과 같은 규칙) 판본 수를
            stored_scoring_version_count로 낸다. 값 null은 null로 두고, details는 음이 아닌 정수 tp·fp·fn만 남긴다.
    """
    patterns = [prefix.replace("_", "\\_") + "%" for prefix in STAGE_PREFIXES]
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT metric_key,segment,class_label,value,support,threshold,comparison,passed,details,"
            "scoring_version,created_at FROM evaluation.metrics "
            "WHERE run_id=%s AND case_id IS NULL AND metric_key LIKE ANY(%s)",
            (run_id, patterns),
        ).fetchall()
    blocks = []
    for prefix, stage in STAGE_PREFIXES.items():
        stage_rows = [r for r in rows if r["metric_key"].startswith(prefix)]
        if not stage_rows:
            continue
        stored: dict[str, datetime] = {}
        for r in stage_rows:
            version = r["scoring_version"]
            stored[version] = max(stored.get(version, r["created_at"]), r["created_at"])
        latest = max(stored, key=lambda version: (stored[version], version))
        items = [
            StageMetric(
                metric_key=r["metric_key"],
                segment=r["segment"],
                class_label=r["class_label"],
                value=None if r["value"] is None else float(r["value"]),
                support=r["support"],
                threshold=None if r["threshold"] is None else float(r["threshold"]),
                comparison=r["comparison"],
                passed=r["passed"],
                details={
                    key: r["details"][key]
                    for key in STAGE_DETAIL_KEYS
                    if type(r["details"].get(key)) is int and r["details"][key] >= 0
                },
            )
            for r in sorted(
                (r for r in stage_rows if r["scoring_version"] == latest),
                key=lambda r: (r["metric_key"], r["segment"], r["class_label"] or ""),
            )
        ]
        blocks.append(
            StageMetricBlock(
                stage=stage,
                scoring_version=latest,
                stored_scoring_version_count=len(stored),
                stored_at=stored[latest],
                items=items,
            )
        )
    return blocks


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
    rows, versions = _latest_rows(conn, run_id, QUALITY_PREFIX)
    if not versions:
        return None
    try:
        return _build_synthetic_quality(rows, versions, gold_version)
    except (TypeError, KeyError, AttributeError):
        # 저장 행 안쪽 값의 타입이 어긋나도 원문 없는 503으로 끝낸다.
        raise ValueError("SYNTHETIC_QUALITY_INVALID") from None


def _latest_rows(
    conn: psycopg.Connection, run_id: UUID, prefix: str
) -> tuple[list[dict], list[dict]]:
    """한 접두의 실행 단위 행 중 가장 최근 채점 판본의 행과 최신순 판본 목록을 읽는다.

    Args:
        conn: 요약과 같은 읽기 전용 트랜잭션의 연결.
        run_id: 조회 중인 실행.
        prefix: metric_key 접두.

    Returns:
        tuple[list[dict], list[dict]]: 선택 판본의 행과 판본 목록. 저장 행이 없으면 둘 다 비었다.
    """
    pattern = prefix.replace("_", "\\_") + "%"
    with conn.cursor(row_factory=dict_row) as cursor:
        # 추가 전용 재채점은 새 판본을 쌓으므로 가장 최근 판본 하나를 보이고 판본 수를 함께 낸다.
        versions = cursor.execute(
            "SELECT scoring_version FROM evaluation.metrics "
            "WHERE run_id=%s AND case_id IS NULL AND metric_key LIKE %s "
            "GROUP BY scoring_version ORDER BY max(created_at) DESC, scoring_version DESC",
            (run_id, pattern),
        ).fetchall()
        if not versions:
            return [], []
        rows = cursor.execute(
            "SELECT metric_key,class_label,segment,value,numerator,denominator,support,details,"
            "threshold,comparison,passed,created_at FROM evaluation.metrics "
            "WHERE run_id=%s AND case_id IS NULL AND scoring_version=%s AND metric_key LIKE %s",
            (run_id, versions[0]["scoring_version"], pattern),
        ).fetchall()
    return rows, versions


def _build_synthetic_quality(
    rows: list[dict], versions: list[dict], gold_version: str | None
) -> SyntheticQuality:
    """선택한 판본의 저장 행이 계약과 같은지 확인하고 응답 모델로 조립한다.

    Args:
        rows: 선택한 판본의 실행 단위 품질 행.
        versions: 최신순 채점 판본 목록.
        gold_version: 실험의 정답 판본.

    Returns:
        SyntheticQuality: 검증한 합성 품질. 계약 위반은 ValueError다.
    """
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


def _read_locked_quality(
    conn: psycopg.Connection, run_id: UUID, experiment: dict
) -> LockedQuality | None:
    """정답 묶음에 연결된 실험이면 다섯 지표의 최신 잠금 판본 행을 같은 스냅샷에서 읽는다.

    Args:
        conn: 요약과 같은 읽기 전용 트랜잭션의 연결.
        run_id: 권한 검사를 통과한 비합성 실행.
        experiment: 실험 메타데이터. gold_version·data_origin·data_split을 저장 행과 대조한다.

    Returns:
        LockedQuality | None: 묶음 ID가 아니면 None, 저장되지 않은 지표는 null. 계약과 다른 행은
            run_id와 위반 종류만 기록하고 ValueError로 거부한다. 정답 파일·사례는 읽지 않는다.
    """
    if experiment["gold_version"] != GOLD_BUNDLE_ID:
        return None
    metrics: dict[str, LockedMetric | None] = {}
    for metric in LOCKED_ROWS:
        rows, versions = _latest_rows(conn, run_id, f"cs_locked_{metric}_first_")
        try:
            metrics[metric] = (
                _build_locked_metric(metric, rows, versions, experiment) if versions else None
            )
        except (TypeError, KeyError, AttributeError, ValueError) as error:
            # 저장 값·검증 메시지는 남기지 않고 실행과 위반 종류만 남긴다.
            violation = (
                str(error)
                if type(error) is ValueError and str(error).startswith("LOCKED_")
                else "LOCKED_VALUE_INVALID"
            )
            logger.warning(
                "Locked quality rows rejected",
                extra={"run_id": str(run_id), "metric": metric, "violation": violation},
            )
            raise ValueError("LOCKED_QUALITY_INVALID") from None
    return LockedQuality(bundle_id=GOLD_BUNDLE_ID, metrics=LockedMetrics(**metrics))


def _build_locked_metric(
    metric: str, rows: list[dict], versions: list[dict], experiment: dict
) -> LockedMetric:
    """선택 판본의 잠금 행이 저장 계약·잠금표·실험과 같은지 확인하고 응답 모델로 조립한다.

    Args:
        metric: 다섯 지표 중 하나.
        rows: 선택한 판본의 실행 단위 행.
        versions: 최신순 채점 판본 목록.
        experiment: 실험 메타데이터.

    Returns:
        LockedMetric: 검증한 지표. 위반은 `LOCKED_`로 시작하는 종류 코드의 ValueError다.
    """
    lock = GOLD_LOCKS[metric]
    info = metric == "provided_info"
    split = 0 if experiment["data_split"] == "train" else 1
    denominators = {
        f"scope={scope}" if info else "all": counts[split] for scope, *counts in lock.denominators
    }
    prefix = f"cs_locked_{metric}_first_"
    labels = LOCKED_LABELS.get(metric, ())
    expected = {
        (segment, key, None) for segment, keys in LOCKED_ROWS[metric].items() for key in keys
    } | {("all", "f1", label) for label in labels}
    by_key = {
        (r["segment"], r["metric_key"].removeprefix(prefix), r["class_label"]): r for r in rows
    }
    if len(rows) != len(expected) or set(by_key) != expected:
        raise ValueError("LOCKED_ROW_SET")
    if any(
        r["threshold"] is not None or r["comparison"] is not None or r["passed"] is not None
        for r in rows
    ):
        raise ValueError("LOCKED_JUDGEMENT_RECORDED")
    head_segment, head_keys = next(iter(LOCKED_ROWS[metric].items()))
    first = by_key[(head_segment, head_keys[0], None)]
    shared = {key: first["details"][key] for key in SHARED_DETAILS}
    if (
        shared["report_version"] != LOCKED_REPORT_VERSION
        or shared["bundle_id"] != GOLD_BUNDLE_ID
        or (shared["gold_version"], shared["gold_sha256"]) != (lock.gold_version, lock.gold_sha256)
        or shared["data_origin"] != experiment["data_origin"]
        or shared["data_split"] != experiment["data_split"]
    ):
        raise ValueError("LOCKED_LOCK_MISMATCH")
    scored = shared["scored_target_count"]
    if scored != sum(denominators.values()):
        raise ValueError("LOCKED_DENOMINATOR")
    segments = {}
    for segment, keys in LOCKED_ROWS[metric].items():
        aggregate = [by_key[(segment, key, None)] for key in keys]
        details = aggregate[0]["details"]
        scope = segment.removeprefix("scope=")
        total = details["scope_target_count"] if info else scored
        excluded = (
            {"PRECISION_ONLY": denominators["scope=PRECISION_ONLY"]}
            if scope == "FULL"
            else {"NOT_RETURN": shared["target_count"] - scored}
            if metric == "return_subtype"
            else {}
        )
        if (
            set(details) != (LOCKED_DETAILS | INFO_DETAILS if info else LOCKED_DETAILS)
            or any(r["details"] != details for r in aggregate)
            or {key: details[key] for key in SHARED_DETAILS} != shared
            or set(details["outcomes"]) != OUTCOMES | {"NOT_STARTED"}
            or (
                info
                and (
                    details["scope"] != scope
                    or details["recall_scored"] is not (scope == "FULL")
                    or details["valid_count"] != details["outcomes"]["VALID"]
                    or set(details["items"])
                    != ({"tp", "fp", "fn"} if scope == "FULL" else {"tp", "fp"})
                )
            )
        ):
            raise ValueError("LOCKED_DETAILS")
        if (
            details["locked_denominator"] != denominators[segment]
            or total != denominators[segment]
            or sum(details["outcomes"].values()) != total
            or details["excluded_by_reason"] != excluded
            or (metric != "return_subtype" and shared["target_count"] != scored)
            or aggregate[0]["support"] != total
        ):
            raise ValueError("LOCKED_DENOMINATOR")
        per_class = {
            label: SyntheticClassQuality(
                support=by_key[("all", "f1", label)]["support"],
                f1=by_key[("all", "f1", label)]["value"],
                **by_key[("all", "f1", label)]["details"],
            )
            for label in labels
        }
        values = {
            r["metric_key"].removeprefix(prefix): LockedValue(
                **{key: r[key] for key in ("value", "numerator", "denominator", "support")}
            )
            for r in aggregate
        }
        class_support = values["composite_macro_f1"].support if metric == "secondary" else total
        if labels and sum(item.support for item in per_class.values()) != class_support:
            raise ValueError("LOCKED_DENOMINATOR")
        segments[segment] = LockedSegment(
            locked_denominator=details["locked_denominator"],
            target_count=total,
            outcomes=details["outcomes"],
            excluded_by_reason=details["excluded_by_reason"],
            values=values,
            per_class=per_class,
            items=details["items"] if info else None,
        )
    return LockedMetric(
        scoring_version=versions[0]["scoring_version"],
        stored_scoring_version_count=len(versions),
        stored_at=first["created_at"],
        gold_version=shared["gold_version"],
        gold_sha256=shared["gold_sha256"],
        run_status_at_scoring=shared["run_status"],
        target_count=shared["target_count"],
        scored_target_count=scored,
        segments=segments,
    )
