"""C/S 품질·사용량·감사를 같은 읽기 스냅샷으로 묶는 내부 보고서와 합성 CLI."""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from backend.api.platform.evaluations.cs_smoke import FIXTURES, matches_profile
from eval.cs_response_audit import summarize_stored_response_audit
from eval.cs_stored_metrics import compare_stored_attempts
from eval.cs_strata import Metric
from eval.cs_usage import summarize_stored_usage

METRICS: tuple[Metric, ...] = ("category", "urgency", "secondary")


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
            meta = cursor.execute(
                "SELECT r.id,r.status,r.provider,r.runner_kind,r.requested_count,"
                "e.id AS experiment_id,e.purpose,e.data_origin,e.data_split,e.gold_version,"
                "e.dataset_hash,e.target_set_hash,e.config_hash,e.model_id,e.prompt_version,"
                "e.policy_version,e.input_builder_version,e.configuration,"
                "transaction_timestamp() AS generated_at "
                "FROM evaluation.runs r JOIN evaluation.experiments e ON e.id=r.experiment_id "
                "WHERE r.id=%s",
                (run_id,),
            ).fetchone()
        if meta is None or meta["gold_version"] != gold_version:
            raise ValueError("CS_REPORT_INVALID_INPUT")
        if synthetic_only and (
            meta["purpose"] != "smoke"
            or meta["data_origin"] != "synthetic"
            or meta["provider"] != "fake"
            or meta["model_id"] != "fake"
            or meta["runner_kind"] not in {"local", "test"}
            or meta["data_split"] == "test"
            or not matches_profile(meta)
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


def main(argv: list[str] | None = None) -> int:
    """local/test의 고정 합성 실행을 새 비공개 JSON 파일로 내보낸다.

    Args:
        argv: 선택적 명령행 인자. DB 연결 정보는 환경 변수로만 받는다.

    Returns:
        int: 성공0 또는 원문 없는 오류2. 기존 파일을 덮어쓰지 않는다.
    """
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
