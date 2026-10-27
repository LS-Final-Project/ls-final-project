"""C/S 품질·사용량·감사를 같은 읽기 스냅샷으로 묶는 내부 보고서와 합성 CLI."""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.api.platform.evaluations.cs_smoke import FIXTURES, matches_profile
from eval.cs_metrics import RETURN_SUBTYPE_LABELS
from eval.cs_response_audit import summarize_stored_response_audit
from eval.cs_stored_metrics import (
    TERMINAL_STATUSES,
    compare_stored_attempts,
    summarize_stored_first_attempts,
)
from eval.cs_strata import Metric
from eval.cs_usage import summarize_stored_usage

METRICS: tuple[Metric, ...] = ("category", "urgency", "secondary")
RUN_META_SQL = (
    "SELECT r.id,r.status,r.provider,r.runner_kind,r.requested_count,"
    "e.id AS experiment_id,e.purpose,e.data_origin,e.data_split,e.gold_version,"
    "e.dataset_hash,e.target_set_hash,e.config_hash,e.model_id,e.prompt_version,"
    "e.policy_version,e.input_builder_version,e.configuration,"
    "transaction_timestamp() AS generated_at "
    "FROM evaluation.runs r JOIN evaluation.experiments e ON e.id=r.experiment_id "
    "WHERE r.id=%s"
)


def _not_synthetic(meta: dict) -> bool:
    """고정 합성 프로필의 local/test fake 실행이 아니면 참이다.

    Args:
        meta: RUN_META_SQL로 읽은 실행·실험 판본.

    Returns:
        bool: 운영·실자료·시험·다른 프로필이면 True.
    """
    return (
        meta["purpose"] != "smoke"
        or meta["data_origin"] != "synthetic"
        or meta["provider"] != "fake"
        or meta["model_id"] != "fake"
        or meta["runner_kind"] not in {"local", "test"}
        or meta["data_split"] == "test"
        or not matches_profile(meta)
    )


def build_stored_report(
    conn: psycopg.Connection,
    run_id: UUID,
    gold: Mapping[str, Mapping[str, str | None]],
    *,
    gold_version: str | None,
    synthetic_only: bool = False,
) -> dict:
    """같은 트랜잭션의 한 스냅샷에서 지표 세 종류와 사용량·감사를 읽는다.

    Args:
        conn: 조회 권한을 확인한 유휴 내부 연결. 기존 트랜잭션은 인수하지 않는다.
        run_id: 보고할 실행 ID.
        gold: category/urgency/secondary별 인수한 고정 대상 정답과 선택적 return_subtype.
        gold_version: 실험과 일치해야 하는 정답 판본. 잠금 확인은 호출자 책임이다.
        synthetic_only: CLI용 고정 합성 프로필 제한을 적용할지 여부.

    Returns:
        dict: 실행 판본·조회 시점·품질·사용량·감사. 실패 시 부분 보고서는 반환하지 않는다.
            함수가 연 읽기 전용 트랜잭션만 종료하며 연결은 닫지 않는다.
    """
    # 반품 하위유형은 잠긴 정답이 있을 때만 보고하는 선택 지표다.
    metrics = (*METRICS, "return_subtype") if "return_subtype" in gold else METRICS
    if conn.info.transaction_status != TransactionStatus.IDLE or set(gold) != set(metrics):
        raise ValueError("CS_REPORT_INVALID_INPUT")
    if any(not isinstance(gold[metric], Mapping) for metric in metrics):
        raise ValueError("CS_REPORT_INVALID_INPUT")
    frozen_gold = {metric: dict(gold[metric]) for metric in metrics}
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with conn.cursor(row_factory=dict_row) as cursor:
            meta = cursor.execute(RUN_META_SQL, (run_id,)).fetchone()
        if meta is None or meta["gold_version"] != gold_version:
            raise ValueError("CS_REPORT_INVALID_INPUT")
        if synthetic_only and (
            _not_synthetic(meta)
            or any(set(frozen_gold[metric]) != set(FIXTURES) for metric in METRICS)
            or not set(frozen_gold.get("return_subtype", {})) <= set(FIXTURES)
        ):
            raise ValueError("CS_REPORT_SYNTHETIC_ONLY")
        quality = {
            metric: compare_stored_attempts(
                conn, run_id, frozen_gold[metric], gold_version=gold_version, metric=metric
            )
            for metric in metrics
        }
        usage = summarize_stored_usage(conn, run_id)
        audit = summarize_stored_response_audit(conn, run_id)
        provenance = {
            key: meta[key]
            for key in (
                "purpose",
                "data_origin",
                "data_split",
                "gold_version",
                "dataset_hash",
                "target_set_hash",
                "config_hash",
                "model_id",
                "prompt_version",
                "policy_version",
                "input_builder_version",
            )
        }
        return {
            "version": "cs-run-report-v1",
            "run_id": str(run_id),
            "experiment_id": str(meta["experiment_id"]),
            "run_status": meta["status"],
            "generated_at": meta["generated_at"].isoformat(),
            "snapshot_isolation": "repeatable_read",
            "target_count": meta["requested_count"],
            "quality_gate_evaluated": False,
            "provenance": provenance,
            "quality": quality,
            "usage": usage,
            "response_audit": audit,
        }


def store_synthetic_return_subtype_metrics(
    conn: psycopg.Connection,
    run_id: UUID,
    gold_csv: bytes,
    *,
    gold_sha256: str,
    gold_version: str | None,
    scoring_version: str,
) -> dict:
    """종료된 합성 실행의 반품 하위유형 최초 집계를 실행 단위 metrics 행으로만 저장한다.

    Args:
        conn: 운영자가 연 유휴 내부 연결. 기존 트랜잭션은 인수하지 않는다.
        run_id: 저장할 종료 실행 ID.
        gold_csv: `ticket_id,return_subtype` 정답 파일 바이트. 경로나 원문은 저장하지 않는다.
        gold_sha256: 인수 기록의 정답 파일 SHA-256(소문자 hex).
        gold_version: 실험과 일치해야 하는 정답 판본.
        scoring_version: 새 채점 판본. 같은 판본의 기존 행은 덮어쓰지 않는다.

    Returns:
        dict: 저장한 채점 판본과 행 수. 거부 시 원문 없는 ValueError이며 아무 행도 남기지 않는다.
            사례 ID·정답·예측·원시 출력은 행에 넣지 않는다. 합격 판정은 하지 않는다.
    """
    if conn.info.transaction_status != TransactionStatus.IDLE or not 0 < len(scoring_version) <= 64:
        raise ValueError("CS_QUALITY_INVALID_INPUT")
    if hashlib.sha256(gold_csv).hexdigest() != gold_sha256:
        raise ValueError("CS_QUALITY_GOLD_HASH_MISMATCH")
    with conn.transaction():
        # 집계와 저장을 한 스냅샷에서 하고 실행 행을 잠가 종료 뒤 변경과 섞지 않는다.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with conn.cursor(row_factory=dict_row) as cursor:
            meta = cursor.execute(RUN_META_SQL + " FOR SHARE OF r", (run_id,)).fetchone()
        if meta is None:
            raise ValueError("CS_QUALITY_INVALID_INPUT")
        if meta["data_split"] == "test":
            raise ValueError("CS_QUALITY_FINAL_TEST_LOCKED")
        targets = {
            row[0]
            for row in conn.execute(
                "SELECT target_ref FROM evaluation.experiment_targets WHERE experiment_id=%s",
                (meta["experiment_id"],),
            )
        }
        if _not_synthetic(meta) or targets != set(FIXTURES):
            raise ValueError("CS_QUALITY_SYNTHETIC_ONLY")
        if meta["gold_version"] != gold_version:
            raise ValueError("CS_QUALITY_GOLD_VERSION_MISMATCH")
        if meta["status"] not in TERMINAL_STATUSES:
            raise ValueError("CS_QUALITY_RUN_NOT_TERMINAL")
        gold = _parse_return_subtype_gold(gold_csv)
        if not gold or not set(gold) <= targets:
            raise ValueError("CS_QUALITY_TARGET_MISMATCH")
        first = summarize_stored_first_attempts(
            conn, run_id, gold, gold_version=gold_version, metric="return_subtype"
        )
        overall = first["report"]["overall"]
        common = {
            "report_version": overall["version"],
            "data_origin": "synthetic",
            "gold_version": gold_version,
            "gold_sha256": gold_sha256,
            "run_status": meta["status"],
            "target_count": first["target_count"],
            "scored_target_count": first["scored_target_count"],
            "outcomes": overall["outcomes"],
        }
        total, nulls = overall["total"], overall["null_predictions"]
        rows = [
            ("accuracy", None, overall["accuracy"], overall["correct"], total, total, common),
            ("macro_f1", None, overall["macro_f1"], None, None, total, common),
            ("null_predictions", None, nulls, nulls, total, total, common),
        ] + [
            (
                "f1",
                label,
                item["f1"],
                item["tp"],
                None,
                item["support"],
                {key: item[key] for key in ("tp", "fp", "fn", "precision", "recall")},
            )
            for label, item in overall["per_class"].items()
        ]
        try:
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO evaluation.metrics(id,run_id,scoring_version,metric_key,"
                    "class_label,value,numerator,denominator,support,details) "
                    "VALUES (gen_random_uuid(),%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (run_id, scoring_version, f"return_subtype_first_{key}", label, value)
                        + (numerator, denominator, support, Jsonb(details))
                        for key, label, value, numerator, denominator, support, details in rows
                    ],
                )
        except psycopg.errors.UniqueViolation:
            raise ValueError("CS_QUALITY_ALREADY_STORED") from None
    return {"run_id": str(run_id), "scoring_version": scoring_version, "stored_rows": len(rows)}


def _parse_return_subtype_gold(data: bytes) -> dict[str, str]:
    """두 열 반품 하위유형 정답을 엄격하게 읽는다.

    Args:
        data: hash를 확인한 정답 파일 바이트.

    Returns:
        dict[str, str]: 대상 ID별 DEFECT/NON_DEFECT. 헤더·중복·빈 값·허용값 밖은 거부한다.
    """
    try:
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
    except (UnicodeDecodeError, csv.Error):
        raise ValueError("CS_QUALITY_GOLD_INVALID") from None
    if not rows or rows[0] != ["ticket_id", "return_subtype"]:
        raise ValueError("CS_QUALITY_GOLD_INVALID")
    gold: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) != 2 or not row[0] or row[0] in gold or row[1] not in RETURN_SUBTYPE_LABELS:
            raise ValueError("CS_QUALITY_GOLD_INVALID")
        gold[row[0]] = row[1]
    return gold


STORE_COMMAND = "store-return-subtype"
# 저장 함수와 그 안의 저장 집계 검증이 내는 원문 없는 코드만 그대로 전달한다.
STORE_ERROR_CODES = re.compile(
    r"CS_QUALITY_[A-Z_]+|CS_STORED_METRICS_INVALID_INPUT|CS_METRICS_INVALID_INPUT"
)


def store_main(argv: list[str]) -> int:
    """종료된 합성 실행의 반품 하위유형 집계 행을 저장하는 운영자 명령.

    Args:
        argv: 하위 명령 뒤의 인자. DB 연결 정보는 환경 변수로만 받는다.

    Returns:
        int: 성공0 또는 오류1. 오류는 코드만 stderr에 쓰고 경로·원문·스택은 출력하지 않는다.
    """
    parser = argparse.ArgumentParser(
        prog=f"python -m eval.cs_report {STORE_COMMAND}",
        description="합성 실행의 반품 하위유형 집계 행 저장; 실제 품질 평가 아님",
    )
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--gold-file", type=Path, required=True)
    parser.add_argument("--gold-sha256", required=True)
    parser.add_argument("--gold-version", default=None)
    parser.add_argument("--scoring-version", required=True)
    args = parser.parse_args(argv)
    dsn = os.getenv("EVALUATION_REPORT_DATABASE_URL")
    if os.getenv("ENVIRONMENT") not in {"local", "test"} or not dsn:
        print("CS_REPORT_LOCAL_CONFIGURATION_REQUIRED", file=sys.stderr)
        return 1
    try:
        gold_csv = args.gold_file.read_bytes()
    except OSError:
        print("CS_QUALITY_GOLD_UNREADABLE", file=sys.stderr)
        return 1
    try:
        with psycopg.connect(dsn) as conn:
            result = store_synthetic_return_subtype_metrics(
                conn,
                args.run_id,
                gold_csv,
                gold_sha256=args.gold_sha256,
                gold_version=args.gold_version,
                scoring_version=args.scoring_version,
            )
    except ValueError as error:
        code = str(error)
        print(
            code if STORE_ERROR_CODES.fullmatch(code) else "CS_QUALITY_STORE_FAILED",
            file=sys.stderr,
        )
        return 1
    except psycopg.Error:
        print("CS_QUALITY_STORE_FAILED", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    """local/test의 고정 합성 실행을 새 비공개 JSON 파일로 내보낸다.

    Args:
        argv: 선택적 명령행 인자. 첫 인자가 store-return-subtype이면 집계 행 저장 명령이다.
            DB 연결 정보는 환경 변수로만 받는다.

    Returns:
        int: 성공0 또는 원문 없는 오류2(저장 명령은 1). 기존 파일을 덮어쓰지 않는다.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == [STORE_COMMAND]:
        return store_main(argv[1:])
    parser = argparse.ArgumentParser(description="고정 합성 C/S 실행 보고서; 실제 품질 평가 아님")
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    dsn = os.getenv("EVALUATION_REPORT_DATABASE_URL")
    if os.getenv("ENVIRONMENT") not in {"local", "test"} or not dsn:
        print("CS_REPORT_LOCAL_CONFIGURATION_REQUIRED", file=sys.stderr)
        return 2
    # 실제 정답이 아닌 보고서 연결 검사용 합성 기대값이다.
    gold: dict[str, dict[str, str | None]] = {
        "category": dict.fromkeys(FIXTURES, "RETURN"),
        "urgency": dict.fromkeys(FIXTURES, "NONE"),
        "secondary": dict.fromkeys(FIXTURES, None),
    }
    try:
        with psycopg.connect(dsn) as conn:
            report = build_stored_report(
                conn, args.run_id, gold, gold_version=None, synthetic_only=True
            )
        report["synthetic_gold_fixture"] = "cs-report-demo-gold-v1"
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(payload)
        except OSError:
            args.output.unlink(missing_ok=True)
            raise
    except (ValueError, OSError, psycopg.Error):
        print("CS_REPORT_EXPORT_FAILED", file=sys.stderr)
        return 2
    print("CS_REPORT_EXPORTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
