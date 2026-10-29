"""후보 비교 실측 결과를 평가 저장소(로컬 DB)에 들여오는 스크립트(KAN-144). 기본은 dry-run이다.

scripts/cs_candidate_run.py --execute --labels-out의 결과 JSON과 라벨 파일에서 후보 하나를 골라 실험·실행·사례·
시도 행을 만들고, eval.cs_report.store_locked_quality_metrics로 category·urgency 잠금 집계 행을 만든다.

- 실험은 POST /experiments와 같은 등록 함수(register_experiment)로 넣는다. 지정 평가자(--actor)·local/test
  환경·접수 허용 설정을 그대로 따르고 test 분할은 거부된다. 승인 목록은 바꾸지 않으며 admissible=false도 그대로
  출력한다.
- 시도는 사례마다 attempt 1 하나다. --audit(cs_candidate_run --audit-out)이 있으면 outcome·사용량·지연·입력 hash·
  응답 판본·비용을 감사에서 채운다. 감사가 없으면 결과 파일에 사례별 기록이 없으므로 추론한다. outcome은 라벨이
  있으면 VALID다. null이면 run 행 outcomes에서 0이 아닌 비VALID 종류가 하나뿐이고 그 수가 null 수와 같을 때만 그
  값을 쓰며, 아니면 거부한다. 이때 사용량·지연·입력 hash는 보고되지 않았으므로 NULL이다. 원시 응답은 어느 쪽이든
  넣지 않는다.
- 예측은 잠금 채점이 읽는 parsed_output(VALID면 cases.final_output에도)에 코드만 넣는다. 정답 파일은 채점에만 쓰고
  저장·출력하지 않는다.
- 접수 영수증(admission_receipts)에는 반입임을 표시할 열이 없어 만들지 않는다. 따라서 실행 요약 API는 이 실행을
  열지 않는다.
- 다시 실행하면 같은 구성의 본인 실험을 이어 쓴다. 실행 행이 있으면 새로 만들지 않고, 저장된 잠금 집계가 있으면
  건너뛴다. 모두 있으면 EXISTS로 멈춘다.

DB는 DATABASE_URL(Settings)만 쓰고 출력하지 않는다. scripts.cs_index_docs.is_local이 로컬로 보지 않으면(운영자
Cloud SQL Proxy 포트 55434 포함) 거부한다. --allow-remote는 로컬 판정만 풀고 55434 거부는 풀지 않는다.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import (
    canonical_hash,
    config_payload,
    register_experiment,
)
from backend.api.platform.evaluations.schemas import ExperimentCreate
from backend.engine.cs.classification import (
    INPUT_VERSION,
    INPUT_VERSION_STRIPPED,
    ClassificationOutput,
)
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings
from eval.cs_gold_locks import GOLD_BUNDLE_ID, GOLD_LOCKS, GoldLock
from eval.cs_report import store_locked_quality_metrics
from scripts.cs_candidate_run import SplitBlocked, knob_fields, load_targets
from scripts.cs_index_docs import PROXY_PORTS, is_local
from tests.cs.cs_dev_data import load_dev_tickets

RUNNER_REF = "eval_import_run"
METRICS = ("category", "urgency")
CASE_STATUS = {
    "VALID": "VALID",
    "SCHEMA_VIOLATION": "INVALID_OUTPUT",
    "TIMEOUT": "FAILED",
    "TRANSPORT_FAILURE": "FAILED",
    "HELD": "HELD",
}


class AuditUsage(BaseModel):
    """감사 항목의 토큰 수. 미보고는 None이다."""

    model_config = ConfigDict(extra="forbid", strict=True)
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    thought_tokens: int | None = Field(ge=0)
    cached_input_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)


class AuditEntry(BaseModel):
    """cs_candidate_run --audit-out의 사례 항목(audit_entry). case_attempts 열 제약과 같은 범위만 받는다."""

    model_config = ConfigDict(extra="forbid")
    outcome: Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD"]
    error_code: str | None
    http_status: int | None = Field(ge=100, le=599)
    model: str | None
    model_version: str | None
    input_version: str
    input_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    usage: AuditUsage
    latency_ms: float | None = Field(ge=0)
    finish_reasons: list[str]
    attempts: int | None = Field(ge=1)
    cost_usd: Decimal | None = Field(ge=0)
    pricing_version: str | None


@dataclass(frozen=True)
class Plan:
    """반입할 실험 등록 요청과 사례별 시도(사례 ID → (outcome, 예측 코드 또는 None)).

    audit은 감사 파일이 있을 때의 사례별 항목이고, 없으면 None이라 사용량 등 열을 NULL로 넣는다.
    """

    body: ExperimentCreate
    attempts: dict[str, tuple[str, dict[str, Any] | None]]
    model: str
    started_at: datetime
    finished_at: datetime
    git_sha: str | None
    audit: dict[str, AuditEntry] | None = None


def build_plan(
    run_bytes: bytes,
    labels_bytes: bytes,
    manifest: Path,
    *,
    candidate: str,
    data_split: str,
    audit_bytes: bytes | None = None,
) -> Plan:
    """결과·라벨·manifest(와 감사)를 검사하고 실험 등록 요청과 사례별 시도를 만든다.

    Args:
        run_bytes: cs_candidate_run 결과 JSON 바이트.
        labels_bytes: 그 후보의 라벨 JSON 바이트.
        manifest: 개발 manifest(load_dev_tickets가 SHA·분할·행 수를 검사한다).
        candidate: 후보 이름.
        data_split: train 또는 valid.
        audit_bytes: 그 후보의 사례 감사 JSON 바이트(선택). 있으면 outcome을 추론하지 않고 감사에서 읽는다.

    Returns:
        Plan: 등록 요청과 시도. 실험의 input_builder_version은 결과 meta의 실제 입력 판본이다(KAN-105 4차).
            meta에 input_version이 없으면 그 기록 전 실행이라 PHONE 표기를 지운 v3으로 본다. 라벨 hash·대상
            hash·건수·outcome이 결과와 맞지 않거나, 분할이 다르거나, outcome을 하나로 정할 수 없거나, 입력
            판본이 v3·v4가 아니거나 strip_phone_annotation과 맞지 않으면 EVAL_IMPORT_INVALID ValueError다. 감사가
            있으면 meta.audit_files hash, 사례 ID 집합(KeyError), outcome 집계, VALID ⇔ 라벨 있음, 요청
            모델(없으면 None)과 입력 판본이 맞아야 한다. test 분할은 SplitBlocked다.
    """
    run = json.loads(run_bytes)
    labels = json.loads(labels_bytes)
    meta = run["meta"]
    [row] = [item for item in run["result"]["candidates"] if item["name"] == candidate]
    [spec] = [item for item in meta["candidates"] if item["name"] == candidate]
    ids = sorted(labels)
    # input_version을 남기기 전(KAN-105 4차 이전) 결과는 모두 표기를 지운 v3 입력으로 돌았다.
    input_version = meta.get("input_version", INPUT_VERSION_STRIPPED)
    stripped = meta.get("strip_phone_annotation", input_version == INPUT_VERSION_STRIPPED)
    if (
        input_version not in (INPUT_VERSION, INPUT_VERSION_STRIPPED)
        or stripped is not (input_version == INPUT_VERSION_STRIPPED)
        or data_split not in ("train", "valid")
        or meta["label_files"][candidate] != hashlib.sha256(labels_bytes).hexdigest()
        or meta["targets"] != len(ids)
        or meta["target_ids_sha256"] != hashlib.sha256("\n".join(ids).encode()).hexdigest()
    ):
        raise ValueError("EVAL_IMPORT_INVALID")
    outcomes = {key: value for key, value in row["outcomes"].items() if value}
    nulls = sum(value is None for value in labels.values())
    others = {key: value for key, value in outcomes.items() if key != "VALID"}
    audit = None
    if audit_bytes is not None:
        audit = {
            key: AuditEntry.model_validate(value) for key, value in json.loads(audit_bytes).items()
        }
        if (
            meta["audit_files"][candidate] != hashlib.sha256(audit_bytes).hexdigest()
            # 사례 집합이 라벨과 다르면 아래 labels[key]나 시도 생성의 audit[ticket_id]가 KeyError로 거부한다.
            or Counter(entry.outcome for entry in audit.values()) != Counter(outcomes)
            or any(
                (entry.outcome == "VALID") is (labels[key] is None)
                or entry.model not in (None, spec["model"])
                or entry.input_version != input_version
                for key, entry in audit.items()
            )
        ):
            raise ValueError("EVAL_IMPORT_INVALID")
    # 합계가 대상 수이고 VALID가 라벨 수면 나머지 합은 null 수다. 감사가 없을 때 그 종류가 둘 이상이면 사례별로
    # 정할 수 없다.
    elif (
        sum(outcomes.values()) != len(ids)
        or outcomes.get("VALID", 0) != len(ids) - nulls
        or len(others) > 1
    ):
        raise ValueError("EVAL_IMPORT_INVALID")
    missing = next(iter(others), "VALID")
    records = load_targets(manifest, sys.maxsize, 0)
    splits = {item["ticket_id"]: item["data_split"] for item in load_dev_tickets(manifest)}
    if any(splits.get(ticket_id) != data_split for ticket_id in ids):
        raise ValueError("EVAL_IMPORT_INVALID")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    origin = document["provenance"]["data_origin"]
    body = ExperimentCreate(
        name=f"import {candidate} {data_split}"[:120],
        purpose="pipeline",
        data_origin=origin,
        data_split=data_split,
        gold_version=GOLD_BUNDLE_ID,
        prompt_version=spec["prompt_version"],
        model_id=spec["model"],
        input_builder_version=input_version,
        stage="classification",
        knobs=knob_fields(spec),
        dataset_manifest={
            "sources": [
                {
                    "source": "cs-dev-manifest",
                    "source_instance": data_split,
                    "file_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    "count": len(ids),
                    "data_origin": origin,
                }
            ]
        },
        targets=[
            {
                "target_ref": ticket_id,
                "target_kind": "cs_ticket",
                "data_origin": origin,
                # 등록 규칙(RAW_TEXT_KEY_PATTERN의 body)이 body_language 키를 거부하므로 channel만 넣는다.
                # 잠금 집계 그룹표에서 body_language는 UNKNOWN이 된다.
                "attributes": {"channel": records[ticket_id]["channel"]},
            }
            for ticket_id in ids
        ],
    )
    attempts: dict[str, tuple[str, dict[str, Any] | None]] = {
        ticket_id: (
            ("VALID", ClassificationOutput.model_validate(labels[ticket_id]).model_dump())
            if labels[ticket_id] is not None
            else (audit[ticket_id].outcome if audit else missing, None)
        )
        for ticket_id in ids
    }
    return Plan(
        body,
        attempts,
        spec["model"],
        datetime.fromisoformat(meta["started_at"]),
        datetime.fromisoformat(meta["finished_at"]),
        meta.get("git_sha"),
        audit,
    )


def hashes(body: ExperimentCreate) -> dict[str, str]:
    """register_experiment와 같은 방식으로 dataset·대상·구성 hash를 계산한다(dry-run 출력용).

    Args:
        body: 등록 요청.

    Returns:
        dict: config_hash·dataset_hash·target_set_hash.
    """
    manifest = body.dataset_manifest.model_dump(exclude_none=True)
    files = sorted(source["file_sha256"] for source in manifest.get("sources", []))
    row: dict[str, Any] = body.model_dump(exclude={"name", "dataset_manifest", "targets"})
    row.update(
        dataset_manifest=manifest,
        dataset_hash=hashlib.sha256("\n".join(files).encode()).hexdigest(),
        target_set_hash=hashlib.sha256(
            "\n".join(sorted(t.target_ref for t in body.targets)).encode()
        ).hexdigest(),
    )
    return {
        "config_hash": canonical_hash(config_payload(row)),
        "dataset_hash": row["dataset_hash"],
        "target_set_hash": row["target_set_hash"],
    }


def outcome_counts(plan: Plan) -> dict[str, int]:
    """사례 상태별 건수.

    Args:
        plan: 반입 계획.

    Returns:
        dict: VALID·INVALID_OUTPUT·FAILED·HELD 건수(0 포함).
    """
    statuses = [CASE_STATUS[outcome] for outcome, _ in plan.attempts.values()]
    return {
        status: statuses.count(status) for status in ("VALID", "INVALID_OUTPUT", "FAILED", "HELD")
    }


def check_dsn(dsn: str, allow_remote: bool) -> None:
    """로컬 DB DSN인지 본다(scripts.cs_index_docs.is_local, 55434 거부 포함).

    --allow-remote는 로컬 판정만 건너뛰고 운영자 Cloud SQL Proxy 포트(PROXY_PORTS)는 계속 거부한다.

    Args:
        dsn: 연결 문자열.
        allow_remote: 로컬 판정만 건너뛸지.

    Returns:
        None: 위반은 EVAL_IMPORT_DSN_REFUSED ValueError다.
    """
    if dsn and is_local(dsn):
        return
    try:
        ports = str(conninfo_to_dict(dsn).get("port") or os.environ.get("PGPORT") or "")
    except psycopg.ProgrammingError:
        raise ValueError("EVAL_IMPORT_DSN_REFUSED") from None
    if not (dsn and allow_remote) or any(p.strip() in PROXY_PORTS for p in ports.split(",")):
        raise ValueError("EVAL_IMPORT_DSN_REFUSED")


def register(
    settings: Settings, access: EvaluationAccessSettings, actor: UUID, body: ExperimentCreate
) -> tuple[UUID, bool | None, str | None]:
    """실험을 등록하거나, 같은 구성의 본인 실험이 있으면 그 ID를 쓴다.

    Args:
        settings: DB·환경 설정.
        access: 평가자·접수 허용 설정.
        actor: 지정 평가자.
        body: 등록 요청.

    Returns:
        tuple: (실험 ID, admissible, blocked_reason). 이어 쓰는 경우 admissible·사유는 None이다. 남의 실험과
            구성이 같거나 등록 규칙 위반이면 그 ApplicationError가 올라간다.
    """
    try:
        registered = asyncio.run(register_experiment(settings, access, actor, body))
    except ApplicationError as error:
        if error.code != "EVALUATION_EXPERIMENT_EXISTS" or not error.details:
            raise
        return UUID(error.details[0]["experiment_id"]), None, None
    return registered.experiment_id, registered.admissible, registered.blocked_reason


def insert_run(
    conn: psycopg.Connection, plan: Plan, experiment_id: UUID, actor: UUID, scoring_version: str
) -> UUID:
    """실행·사례·시도를 한 트랜잭션으로 넣는다. 같은 실험에 반입 실행이 있으면 그 ID를 돌려준다.

    Args:
        conn: 유휴 연결.
        plan: 반입 계획.
        experiment_id: 등록한 실험.
        actor: 요청자.
        scoring_version: 실행의 첫 채점 판본.

    Returns:
        UUID: 실행 ID. 같은 실험에 반입이 아닌 실행이 있으면 EVAL_IMPORT_RUN_CONFLICT ValueError다.
    """
    counts = outcome_counts(plan)
    with conn.transaction():
        existing = conn.execute(
            "SELECT id,runner_ref,provider FROM evaluation.runs WHERE experiment_id=%s",
            (experiment_id,),
        ).fetchall()
        if existing:
            if len(existing) != 1 or existing[0][1:] != (RUNNER_REF, "gemini"):
                raise ValueError("EVAL_IMPORT_RUN_CONFLICT")
            return existing[0][0]
        run_id = uuid4()
        status = (
            "SUCCEEDED"
            if counts["VALID"] == len(plan.attempts)
            else "PARTIAL"
            if counts["VALID"]
            else "FAILED"
        )
        conn.execute(
            "INSERT INTO evaluation.runs (id,experiment_id,status,runner_kind,runner_ref,"
            "requested_by_user_id,provider,scoring_version,code_version,requested_count,"
            "valid_output_count,held_count,failed_count,created_at,started_at,finished_at) "
            "VALUES (%s,%s,%s,'local',%s,%s,'gemini',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                run_id,
                experiment_id,
                status,
                RUNNER_REF,
                actor,
                scoring_version,
                plan.git_sha,
                len(plan.attempts),
                counts["VALID"],
                counts["HELD"],
                counts["INVALID_OUTPUT"] + counts["FAILED"],
                plan.started_at,
                plan.started_at,
                plan.finished_at,
            ),
        )
        cases = []
        attempts = []
        for ticket_id, (outcome, parsed) in plan.attempts.items():
            case_id = uuid4()
            output = Jsonb(parsed) if parsed is not None else None
            cases.append(
                (case_id, run_id, experiment_id, ticket_id, CASE_STATUS[outcome], output)
                + (plan.started_at, plan.finished_at)
            )
            entry = plan.audit[ticket_id] if plan.audit else None
            attempts.append(
                (uuid4(), case_id, outcome, plan.model, output, plan.started_at, plan.finished_at)
                + audit_columns(entry, plan.model)
            )
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO evaluation.cases (id,run_id,experiment_id,target_ref,status,"
                "final_output,created_at,completed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                cases,
            )
            cursor.executemany(
                "INSERT INTO evaluation.case_attempts (id,case_id,step,attempt,is_final,outcome,"
                "provider,model_id,parsed_output,started_at,finished_at,requested_model,"
                "error_code,http_status,model_version,finish_reasons,input_sha256,latency_ms,"
                "input_tokens,output_tokens,thought_tokens,cached_input_tokens,total_tokens,"
                "call_attempts,cost_usd,pricing_version) VALUES (%s,%s,'classify',1,true,%s,"
                "'gemini',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                attempts,
            )
    return run_id


def audit_columns(entry: AuditEntry | None, model: str) -> tuple[object, ...]:
    """감사 항목을 case_attempts 열 값으로 바꾼다. 감사가 없으면 요청 모델만 두고 나머지는 미보고(NULL)다.

    Args:
        entry: 사례 감사 항목 또는 None.
        model: 후보 모델(감사가 없거나 항목의 요청 모델이 None일 때 쓴다).

    Returns:
        tuple: requested_model·error_code·http_status·model_version·finish_reasons·input_sha256·
            latency_ms(반올림)·토큰 5종·call_attempts·cost_usd·pricing_version 순서.
    """
    if entry is None:
        return (model, None, None, None, [], None, None) + (None,) * 5 + (None, None, None)
    usage = entry.usage
    return (
        entry.model or model,
        entry.error_code,
        entry.http_status,
        entry.model_version,
        entry.finish_reasons,
        entry.input_sha256,
        None if entry.latency_ms is None else round(entry.latency_ms),
        usage.input_tokens,
        usage.output_tokens,
        usage.thought_tokens,
        usage.cached_input_tokens,
        usage.total_tokens,
        entry.attempts,
        entry.cost_usd,
        entry.pricing_version,
    )


def store_metrics(
    conn: psycopg.Connection,
    run_id: UUID,
    golds: Mapping[str, bytes],
    scoring_version: str,
    locks: Mapping[str, GoldLock] | None = None,
) -> dict[str, int | str]:
    """category·urgency 잠금 집계를 저장한다. 이미 저장된 지표는 건너뛴다.

    Args:
        conn: 유휴 연결.
        run_id: 반입 실행.
        golds: {지표: 잠금 정답 CSV 바이트}.
        scoring_version: 채점 판본.
        locks: 잠금표(테스트 주입용). None이면 GOLD_LOCKS다.

    Returns:
        dict: 지표별 저장 행 수 또는 'EXISTS'.
    """
    stored: dict[str, int | str] = {}
    for metric in METRICS:
        try:
            result = store_locked_quality_metrics(
                conn, run_id, metric, golds[metric], scoring_version=scoring_version, locks=locks
            )
        except ValueError as error:
            if str(error) != "CS_QUALITY_ALREADY_STORED":
                raise
            stored[metric] = "EXISTS"
            continue
        stored[metric] = result["stored_rows"]
    return stored


def main(argv: list[str] | None = None, locks: Mapping[str, GoldLock] | None = None) -> int:
    """인자를 읽어 dry-run 요약을 내거나 --execute로 반입한다.

    Args:
        argv: 명령행 인자(테스트 주입용).
        locks: 잠금표(테스트 주입용).

    Returns:
        int: 0 성공(EXISTS 포함), 2 test 분할 거부, 1 입력·등록·DB 오류. 오류는 코드만 출력하고 DSN·원문·
            사례 ID·정답은 출력하지 않는다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--audit", type=Path, help="그 후보의 사례 감사(<name>.audit.json, 선택)")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-split", choices=["train", "valid"], required=True)
    parser.add_argument("--gold-category", type=Path, required=True)
    parser.add_argument("--gold-urgency", type=Path, required=True)
    parser.add_argument("--actor", type=UUID, help="로컬 지정 평가자 UUID(--execute에 필요)")
    parser.add_argument("--scoring-version", default="kan144-import-v1")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--allow-remote", action="store_true", help="로컬 판정만 풀고 55434는 계속 거부"
    )
    args = parser.parse_args(argv)
    try:
        plan = build_plan(
            args.run.read_bytes(),
            args.labels.read_bytes(),
            args.manifest,
            candidate=args.candidate,
            data_split=args.data_split,
            audit_bytes=args.audit.read_bytes() if args.audit else None,
        )
        golds = {
            "category": args.gold_category.read_bytes(),
            "urgency": args.gold_urgency.read_bytes(),
        }
        # 채점 전에 잠금 정답 파일이 맞는지 본다(정답 내용은 출력하지 않는다).
        table = GOLD_LOCKS if locks is None else locks
        if any(hashlib.sha256(golds[m]).hexdigest() != table[m].gold_sha256 for m in METRICS):
            raise ValueError("EVAL_IMPORT_GOLD_MISMATCH")
        summary: dict[str, Any] = {
            "mode": "execute" if args.execute else "dry-run",
            "experiments": 1,
            "runs": 1,
            "cases": len(plan.attempts),
            "attempts": len(plan.attempts),
            "outcomes": outcome_counts(plan),
            "audit": plan.audit is not None,
            "model_id": plan.model,
            "prompt_version": plan.body.prompt_version,
            "knobs": plan.body.knobs,
            **hashes(plan.body),
        }
        if args.execute:
            if args.actor is None:
                raise ValueError("EVAL_IMPORT_ACTOR_REQUIRED")
            settings = Settings()
            dsn = settings.database_url.get_secret_value() if settings.database_url else ""
            check_dsn(dsn, args.allow_remote)
            experiment_id, admissible, reason = register(
                settings, EvaluationAccessSettings(), args.actor, plan.body
            )
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                run_id = insert_run(conn, plan, experiment_id, args.actor, args.scoring_version)
                stored = store_metrics(conn, run_id, golds, args.scoring_version, locks)
            summary |= {
                "status": "EXISTS"
                if admissible is None and all(value == "EXISTS" for value in stored.values())
                else "IMPORTED",
                "experiment_id": str(experiment_id),
                "run_id": str(run_id),
                "admissible": admissible,
                "blocked_reason": reason,
                "locked_metric_rows": stored,
            }
    except SplitBlocked:
        print("CS_RUN_TEST_SPLIT: test 분할은 최종 1회 사용 원칙이라 실행하지 않습니다.")
        return 2
    except ApplicationError as error:
        print(f"EVAL_IMPORT_FAILED: {error.code}")
        return 1
    except (ValueError, OSError, KeyError, TypeError, psycopg.Error) as error:
        code = str(error) if str(error).startswith(("EVAL_", "CS_")) else type(error).__name__
        print(f"EVAL_IMPORT_FAILED: {code}")
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
