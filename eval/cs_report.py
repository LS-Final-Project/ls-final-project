"""C/S 품질·사용량·감사를 같은 읽기 스냅샷으로 묶는 내부 보고서와 합성 CLI."""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.api.platform.evaluations.cs_smoke import FIXTURES, matches_profile
from eval.cs_gold_locks import GOLD_BUNDLE_ID, GOLD_LOCKS, GoldLock
from eval.cs_metrics import (
    CATEGORY_LABELS,
    PROVIDED_INFO_SCOPES,
    RETURN_SUBTYPE_LABELS,
    URGENCY_LABELS,
    ProvidedInfoGold,
)
from eval.cs_response_audit import summarize_stored_response_audit
from eval.cs_stored_metrics import (
    TERMINAL_STATUSES,
    compare_stored_attempts,
    summarize_stored_first_attempts,
)
from eval.cs_strata import GoldValue, Metric
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


QualityRow = tuple[str, str | None, float | None, int | None, int | None, int | None, dict]


def _store_quality_rows(
    conn: psycopg.Connection,
    run_id: UUID,
    gold_csv: bytes,
    *,
    gold_sha256: str,
    scoring_version: str,
    build: Callable[[dict, set[str]], tuple[str, dict[str, list[QualityRow]]]],
) -> dict:
    """종료 실행의 품질 집계를 한 스냅샷에서 계산해 실행 단위 metrics 행으로만 저장한다.

    Args:
        conn: 운영자가 연 유휴 내부 연결. 기존 트랜잭션은 인수하지 않는다.
        run_id: 저장할 종료 실행 ID.
        gold_csv: 정답 파일 바이트. 경로나 원문은 저장하지 않는다.
        gold_sha256: 행을 읽기 전에 대조할 정답 파일 SHA-256(소문자 hex).
        scoring_version: 새 채점 판본. 같은 판본의 기존 행은 덮어쓰지 않는다.
        build: 잠근 실행 메타·실험 대상을 받아 경로별 거부 검사 후 metric_key 접두와 segment별
            행을 낸다.

    Returns:
        dict: 저장한 채점 판본과 행 수. 거부 시 원문 없는 ValueError이며 아무 행도 남기지 않는다.
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
        prefix, segments = build(meta, targets)
        rows = [(segment, row) for segment, items in segments.items() for row in items]
        try:
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO evaluation.metrics(id,run_id,scoring_version,metric_key,segment,"
                    "class_label,value,numerator,denominator,support,details) "
                    "VALUES (gen_random_uuid(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (run_id, scoring_version, prefix + key, segment, label, value)
                        + (numerator, denominator, support, Jsonb(details))
                        for segment, (
                            key,
                            label,
                            value,
                            numerator,
                            denominator,
                            support,
                            details,
                        ) in rows
                    ],
                )
        except psycopg.errors.UniqueViolation:
            raise ValueError("CS_QUALITY_ALREADY_STORED") from None
    return {"run_id": str(run_id), "scoring_version": scoring_version, "stored_rows": len(rows)}


def _class_rows(per_class: Mapping[str, dict]) -> list[QualityRow]:
    """클래스별 F1 행을 tp/fp/fn/precision/recall 구성요소와 함께 만든다.

    Args:
        per_class: 기존 집계의 클래스별 지표.

    Returns:
        list[QualityRow]: 클래스마다 한 행.
    """
    return [
        (
            "f1",
            label,
            item["f1"],
            item["tp"],
            None,
            item["support"],
            {key: item[key] for key in ("tp", "fp", "fn", "precision", "recall")},
        )
        for label, item in per_class.items()
    ]


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

    def build(meta: dict, targets: set[str]) -> tuple[str, dict[str, list[QualityRow]]]:
        """합성 경계와 부분집합 정답을 확인하고 반품 하위유형 행을 만든다.

        Args:
            meta: 잠근 실행·실험 메타.
            targets: 실험 고정 대상.

        Returns:
            tuple[str, dict[str, list[QualityRow]]]: metric_key 접두와 segment `all`의 저장 행.
        """
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
        return "return_subtype_first_", {
            "all": [
                ("accuracy", None, overall["accuracy"], overall["correct"], total, total, common),
                ("macro_f1", None, overall["macro_f1"], None, None, total, common),
                ("null_predictions", None, nulls, nulls, total, total, common),
            ]
            + _class_rows(overall["per_class"])
        }

    return _store_quality_rows(
        conn,
        run_id,
        gold_csv,
        gold_sha256=gold_sha256,
        scoring_version=scoring_version,
        build=build,
    )


LOCKED_REPORT_VERSION = "cs-locked-quality-v1"
LOCKED_LABELS: dict[str, tuple[str, ...]] = {
    "category": CATEGORY_LABELS,
    "urgency": URGENCY_LABELS,
    "secondary": CATEGORY_LABELS,
    "return_subtype": RETURN_SUBTYPE_LABELS,
}


def store_locked_quality_metrics(
    conn: psycopg.Connection,
    run_id: UUID,
    metric: str,
    gold_csv: bytes,
    *,
    scoring_version: str,
    locks: Mapping[str, GoldLock] | None = None,
) -> dict:
    """종료된 비합성 실행의 지표 하나를 KAN-95 잠금 정답으로 채점해 실행 단위 행으로만 저장한다.

    Args:
        conn: 운영자가 연 유휴 내부 연결. 기존 트랜잭션은 인수하지 않는다.
        run_id: 저장할 종료 실행 ID. 실험 gold_version은 정답 묶음 ID여야 한다.
        metric: category·urgency·secondary·return_subtype·provided_info 중 하나.
        gold_csv: 그 지표의 잠금 정답 파일 바이트. 경로나 원문은 저장하지 않는다.
        scoring_version: 새 채점 판본. 같은 판본의 기존 행은 덮어쓰지 않는다.
        locks: 지표별 잠금 판본·hash·열·분모. 없으면 GOLD_LOCKS다. 판본·hash는 인수로 받지 않는다.

    Returns:
        dict: 저장한 채점 판본과 행 수. 거부 시 원문 없는 ValueError이며 아무 행도 남기지 않는다.
            사례 ID·정답·예측·원시 출력은 행에 넣지 않는다. 합격 판정은 하지 않는다.
    """
    locks = GOLD_LOCKS if locks is None else locks
    if metric not in (*LOCKED_LABELS, "provided_info") or metric not in locks:
        raise ValueError("CS_QUALITY_METRIC_UNSUPPORTED")
    lock = locks[metric]
    stored_metric = cast(Metric, metric)

    def build(meta: dict, targets: set[str]) -> tuple[str, dict[str, list[QualityRow]]]:
        """비합성 경계·묶음 판본·고정 분모를 확인하고 지표 행을 만든다.

        Args:
            meta: 잠근 실행·실험 메타.
            targets: 실험 고정 대상.

        Returns:
            tuple[str, dict[str, list[QualityRow]]]: metric_key 접두와 segment별 저장 행.
                provided_info만 채점 범위별 segment를 쓰고 나머지는 `all`이다.
        """
        # 합성 경로의 SYNTHETIC_ONLY와 반대 경계라 한 실행에 두 경로의 행이 함께 남지 않는다.
        if (
            meta["purpose"] == "smoke"
            or meta["provider"] == "fake"
            or meta["model_id"] == "fake"
            or matches_profile(meta)
        ):
            raise ValueError("CS_QUALITY_LOCKED_RUN_REQUIRED")
        if meta["gold_version"] != GOLD_BUNDLE_ID:
            raise ValueError("CS_QUALITY_BUNDLE_MISMATCH")
        if meta["status"] not in TERMINAL_STATUSES:
            raise ValueError("CS_QUALITY_RUN_NOT_TERMINAL")
        gold: Mapping[str, GoldValue] = (
            _parse_provided_info_gold(gold_csv, lock.columns)
            if metric == "provided_info"
            else _parse_gold(
                gold_csv, lock.columns, LOCKED_LABELS[metric], nullable=metric == "secondary"
            )
        )
        scoped = {target: gold[target] for target in targets if target in gold}
        denominators = {
            scope: {"train": train, "valid": valid}.get(meta["data_split"])
            for scope, train, valid in lock.denominators
        }
        counts: Mapping[str, int] = (
            Counter(cast(ProvidedInfoGold, value)[1] for value in scoped.values())
            if metric == "provided_info"
            else {lock.denominators[0][0]: len(scoped)}
        )
        # 결과를 본 뒤 대상을 빼지 못하게 범위별로 잠근 split 분모와 정확히 같아야 한다.
        if (
            {scope: counts.get(scope, 0) for scope in denominators} != denominators
            or not set(counts) <= set(denominators)
            or (metric != "return_subtype" and len(scoped) != len(targets))
        ):
            raise ValueError("CS_QUALITY_DENOMINATOR_MISMATCH")
        denominator = next(iter(denominators.values()))
        first = summarize_stored_first_attempts(
            conn, run_id, scoped, gold_version=GOLD_BUNDLE_ID, metric=stored_metric
        )
        overall = first["report"]["overall"]
        prefix = f"cs_locked_{metric}_first_"
        common = {
            "report_version": LOCKED_REPORT_VERSION,
            "bundle_id": GOLD_BUNDLE_ID,
            "gold_version": lock.gold_version,
            "gold_sha256": lock.gold_sha256,
            "data_origin": meta["data_origin"],
            "data_split": meta["data_split"],
            "run_status": meta["status"],
            "locked_denominator": denominator,
            "target_count": first["target_count"],
            "scored_target_count": first["scored_target_count"],
            "excluded_by_reason": (
                {"NOT_RETURN": len(targets) - len(scoped)} if metric == "return_subtype" else {}
            ),
        }
        if metric == "provided_info":
            return prefix, _provided_info_rows(overall, common, denominators)
        common["outcomes"] = overall["outcomes"]
        total = overall["total"]
        rows: list[QualityRow] = [
            ("accuracy", None, overall["accuracy"], overall["correct"], total, total, common)
        ]
        per_class = overall["per_class"]
        if metric == "secondary":
            composite, absent = overall["composite"], overall["absent"]
            per_class = composite["per_class"]
            rows += [
                ("composite_macro_f1", None, composite["macro_f1"], None, None)
                + (composite["support"], common),
                ("invented", None, absent["invented"], absent["invented"], absent["support"])
                + (absent["support"], common),
            ]
        else:
            rows.append(("macro_f1", None, overall["macro_f1"], None, None, total, common))
        if metric == "urgency":
            non_none, critical = overall["non_none"], overall["critical"]
            rows += [
                ("non_none_recall", None, non_none["recall"], non_none["detected"])
                + (non_none["support"], non_none["support"], common),
                ("critical_missed", None, critical["missed"], critical["missed"])
                + (critical["support"], critical["support"], common),
            ]
        if metric == "return_subtype":
            nulls = overall["null_predictions"]
            rows.append(("null_predictions", None, nulls, nulls, total, total, common))
        return prefix, {"all": rows + _class_rows(per_class)}

    return _store_quality_rows(
        conn,
        run_id,
        gold_csv,
        gold_sha256=lock.gold_sha256,
        scoring_version=scoring_version,
        build=build,
    )


def _provided_info_rows(
    overall: dict, common: dict, denominators: Mapping[str, int | None]
) -> dict[str, list[QualityRow]]:
    """provided_info 채점 범위별 집계 행을 만들며 두 범위를 합하지 않는다.

    Args:
        overall: 같은 스냅샷의 provided_info 최초 집계.
        common: 실행·정답 판본 공통 details.
        denominators: 채점 범위별 잠금 split 분모.

    Returns:
        dict[str, list[QualityRow]]: `scope=FULL`의 precision·recall·F1·완전 일치와
            `scope=PRECISION_ONLY`의 precision. details의 항목 수는 집계값만이며 항목 코드는 없다.
    """
    scopes = overall["scopes"]
    segments: dict[str, list[QualityRow]] = {}
    for scope, result in scopes.items():
        full = scope == "FULL"
        details = {
            **common,
            "scope": scope,
            "locked_denominator": denominators[scope],
            "scope_target_count": result["total"],
            "valid_count": result["valid_count"],
            "items": {key: result[key] for key in ("tp", "fp", "fn") if key in result},
            "recall_scored": full,
            "excluded_by_reason": {"PRECISION_ONLY": scopes["PRECISION_ONLY"]["total"]}
            if full
            else {},
            "outcomes": result["outcomes"],
        }
        tp, total = result["tp"], result["total"]
        rows: list[QualityRow] = [
            ("precision", None, result["precision"], tp, tp + result["fp"], total, details)
        ]
        if full:
            rows += [
                ("recall", None, result["recall"], tp, tp + result["fn"], total, details),
                ("f1", None, result["f1"], None, None, total, details),
                ("exact_match", None, result["exact_match_rate"], result["exact_match"])
                + (total, total, details),
            ]
        segments[f"scope={scope}"] = rows
    return segments


def _parse_gold(
    data: bytes, columns: tuple[str, ...], labels: tuple[str, ...], *, nullable: bool = False
) -> dict[str, str | None]:
    """두 열 단일 분류 정답을 엄격하게 읽는다.

    Args:
        data: hash를 확인한 정답 파일 바이트.
        columns: 기대하는 헤더(`ticket_id`와 정답 열).
        labels: 허용하는 정답 값.
        nullable: 빈 값을 null 정답으로 받을지 여부.

    Returns:
        dict[str, str | None]: 대상 ID별 정답. 헤더·중복·허용값 밖·허용하지 않은 빈 값은 거부한다.
    """
    try:
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
    except (UnicodeDecodeError, csv.Error):
        raise ValueError("CS_QUALITY_GOLD_INVALID") from None
    if not rows or tuple(rows[0]) != columns or len(columns) != 2:
        raise ValueError("CS_QUALITY_GOLD_INVALID")
    gold: dict[str, str | None] = {}
    for row in rows[1:]:
        if len(row) != 2 or not row[0] or row[0] in gold:
            raise ValueError("CS_QUALITY_GOLD_INVALID")
        if row[1] not in labels and not (nullable and row[1] == ""):
            raise ValueError("CS_QUALITY_GOLD_INVALID")
        gold[row[0]] = row[1] or None
    return gold


def _parse_return_subtype_gold(data: bytes) -> dict[str, str | None]:
    """두 열 반품 하위유형 정답을 엄격하게 읽는다.

    Args:
        data: hash를 확인한 정답 파일 바이트.

    Returns:
        dict[str, str | None]: 대상 ID별 DEFECT/NON_DEFECT. 빈 값은 허용하지 않는다.
    """
    return _parse_gold(data, ("ticket_id", "return_subtype"), RETURN_SUBTYPE_LABELS)


def _parse_provided_info_gold(data: bytes, columns: tuple[str, ...]) -> dict[str, GoldValue]:
    """세 열 provided_info 정답과 채점 범위를 엄격하게 읽는다.

    docs/evaluation.md "C/S 제공 정보 정답 잠금"의 기록 "`ticket_id,provided_info,scoring_scope`,
    `;` 구분, 빈 값은 `[]`"를 그대로 따른다. 실제 파일 표기가 이와 다르면 추정하지 않고 거부한다.

    Args:
        data: hash를 확인한 정답 파일 바이트.
        columns: 기대하는 헤더.

    Returns:
        dict[str, GoldValue]: 대상 ID별 (항목 집합, FULL/PRECISION_ONLY). 헤더·중복 ID·빈 칸·
            빈 항목·앞뒤 공백·중복 항목·범위 값 밖은 CS_QUALITY_GOLD_INVALID로 거부한다.
    """
    try:
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
    except (UnicodeDecodeError, csv.Error):
        raise ValueError("CS_QUALITY_GOLD_INVALID") from None
    if not rows or tuple(rows[0]) != columns or len(columns) != 3:
        raise ValueError("CS_QUALITY_GOLD_INVALID")
    gold: dict[str, GoldValue] = {}
    for row in rows[1:]:
        if len(row) != 3 or not row[0] or row[0] in gold or row[2] not in PROVIDED_INFO_SCOPES:
            raise ValueError("CS_QUALITY_GOLD_INVALID")
        items = [] if row[1] == "[]" else row[1].split(";")
        if any(not item or item != item.strip() or item == "[]" for item in items) or len(
            set(items)
        ) != len(items):
            raise ValueError("CS_QUALITY_GOLD_INVALID")
        gold[row[0]] = (frozenset(items), row[2])
    return gold


STORE_COMMAND = "store-return-subtype"
STORE_LOCKED_COMMAND = "store-locked-quality"
# 저장 함수와 그 안의 저장 집계 검증이 내는 원문 없는 코드만 그대로 전달한다.
STORE_ERROR_CODES = re.compile(
    r"CS_QUALITY_[A-Z_]+|CS_STORED_METRICS_INVALID_INPUT|CS_METRICS_INVALID_INPUT"
)


def _run_store(gold_file: Path, store: Callable[[psycopg.Connection, bytes], dict]) -> int:
    """저장 명령의 공통 환경 확인·파일 읽기·오류 코드 출력을 수행한다.

    Args:
        gold_file: 정답 파일 경로. 출력·저장하지 않는다.
        store: 연결과 정답 바이트를 받아 저장 결과를 내는 함수.

    Returns:
        int: 성공0 또는 오류1. 오류는 코드만 stderr에 쓰고 경로·원문·스택은 출력하지 않는다.
    """
    dsn = os.getenv("EVALUATION_REPORT_DATABASE_URL")
    if os.getenv("ENVIRONMENT") not in {"local", "test"} or not dsn:
        print("CS_REPORT_LOCAL_CONFIGURATION_REQUIRED", file=sys.stderr)
        return 1
    try:
        gold_csv = gold_file.read_bytes()
    except OSError:
        print("CS_QUALITY_GOLD_UNREADABLE", file=sys.stderr)
        return 1
    try:
        with psycopg.connect(dsn) as conn:
            result = store(conn, gold_csv)
    except ValueError as error:
        code = str(error)
        print(
            code if STORE_ERROR_CODES.fullmatch(code) else "CS_QUALITY_STORE_FAILED",
            file=sys.stderr,
        )
        return 1
    except Exception:  # 예상 밖 오류도 원문·스택(경로·DSN 포함 가능) 없이 끝낸다.
        print("CS_QUALITY_STORE_FAILED", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


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
    return _run_store(
        args.gold_file,
        lambda conn, gold_csv: store_synthetic_return_subtype_metrics(
            conn,
            args.run_id,
            gold_csv,
            gold_sha256=args.gold_sha256,
            gold_version=args.gold_version,
            scoring_version=args.scoring_version,
        ),
    )


def store_locked_main(argv: list[str]) -> int:
    """종료된 비합성 실행의 지표 하나를 잠금 정답으로 채점해 집계 행을 저장하는 운영자 명령.

    Args:
        argv: 하위 명령 뒤의 인자. 정답 판본·hash는 받지 않고 잠금 상수에서 읽는다.

    Returns:
        int: 성공0 또는 오류1. 오류는 코드만 stderr에 쓰고 경로·원문·스택은 출력하지 않는다.
    """
    parser = argparse.ArgumentParser(
        prog=f"python -m eval.cs_report {STORE_LOCKED_COMMAND}",
        description="잠금 정답 지표 집계 행 저장; 합격 판정 없음",
    )
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--gold-file", type=Path, required=True)
    parser.add_argument("--scoring-version", required=True)
    args = parser.parse_args(argv)
    return _run_store(
        args.gold_file,
        lambda conn, gold_csv: store_locked_quality_metrics(
            conn, args.run_id, args.metric, gold_csv, scoring_version=args.scoring_version
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """local/test의 고정 합성 실행을 새 비공개 JSON 파일로 내보낸다.

    Args:
        argv: 선택적 명령행 인자. 첫 인자가 store-return-subtype·store-locked-quality면 집계 행
            저장 명령이다.
            DB 연결 정보는 환경 변수로만 받는다.

    Returns:
        int: 성공0 또는 원문 없는 오류2(저장 명령은 1). 기존 파일을 덮어쓰지 않는다.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == [STORE_COMMAND]:
        return store_main(argv[1:])
    if argv[:1] == [STORE_LOCKED_COMMAND]:
        return store_locked_main(argv[1:])
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
