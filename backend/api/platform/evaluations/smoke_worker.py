"""로컬 전용 합성 worker. 외부 모델·파일·최종 시험을 실행하지 않는다."""

import argparse
import asyncio
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import (
    admission_available,
    check_actor,
    validate_smoke_experiment,
)
from backend.api.platform.evaluations.cs_smoke import run_case
from backend.engine.cs.classification_call import ClassificationAttempt
from backend.shared.ai import estimate_cost, load_price_table
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


def attempt_cost(attempt: ClassificationAttempt) -> tuple[Decimal | None, str | None]:
    """시도 사용량을 저장소 단가표로 계산해 cost_usd·pricing_version 값을 만든다.

    Args:
        attempt: 요청 모델과 사용량을 가진 분류 시도.

    Returns:
        tuple[Decimal | None, str | None]: total이 있고 통화가 USD일 때만 (금액, '모델@as_of').
            단가가 없거나 USD가 아니면 (None, None)이다. 금액은 DB numeric(14,6)에 저장되며
            소수 6자리로 반올림된다.
    """
    if attempt.requested_model is None:
        return None, None
    cost = estimate_cost(attempt.usage, attempt.requested_model, load_price_table())
    if cost.total is None or cost.currency != "USD":
        return None, None
    return cost.total, f"{attempt.requested_model}@{cost.price_as_of}"


@dataclass(frozen=True)
class SmokeLease:
    """오래된 실행자의 저장을 차단하는 DB 소유권 표식."""

    run_id: UUID
    holder_id: UUID
    generation: int


async def locked_run(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    settings: Settings,
    access: EvaluationAccessSettings,
    run_id: UUID,
) -> dict[str, Any]:
    """제어 행을 잠그고 현재 권한과 합성 실행 조건을 다시 검사한다.

    Args:
        conn: 호출자 transaction의 DB 연결.
        settings: 환경 설정.
        access: 현재 서버 허용 설정.
        run_id: 대상 실행 UUID.

    Returns:
        dict: 잠긴 제어·실행 메타데이터. 미등록/허용 위반은 오류다.
    """
    await conn.execute("SET LOCAL lock_timeout='2s'")
    await conn.execute("SET LOCAL statement_timeout='5s'")
    # 제어 행 잠금을 먼저 얻고 새 명령에서 실행 상태를 읽어 동시 취소의 최신 상태를 본다.
    await conn.execute(
        "SELECT run_id FROM evaluation.run_control WHERE run_id=%s FOR UPDATE", (run_id,)
    )
    cursor = await conn.execute(
        "SELECT c.*,r.status,r.needs_review,r.job_id,r.requested_count,r.requested_by_user_id,r.provider,"
        "r.experiment_id,e.created_by_user_id,u.is_active,"
        "(c.lease_expires_at > clock_timestamp()) AS lease_live "
        "FROM evaluation.run_control c JOIN evaluation.runs r ON r.id=c.run_id "
        "JOIN evaluation.experiments e ON e.id=r.experiment_id "
        "JOIN identity.users u ON u.id=r.requested_by_user_id "
        "JOIN evaluation.admission_receipts a ON a.run_id=r.id "
        "WHERE c.run_id=%s FOR UPDATE OF c",
        (run_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ApplicationError(404, "NOT_FOUND", "Run is unavailable.")
    check_actor(settings, access, row["requested_by_user_id"])
    if (
        not row["is_active"]
        or row["requested_by_user_id"] != row["created_by_user_id"]
        or row["provider"] != "fake"
    ):
        raise ApplicationError(403, "SMOKE_ACCESS_DENIED", "Smoke access denied.")
    cursor = await conn.execute(
        "SELECT * FROM evaluation.experiments WHERE id=%s", (row["experiment_id"],)
    )
    experiment = await cursor.fetchone()
    assert experiment is not None
    validate_smoke_experiment(experiment, access)
    cursor = await conn.execute(
        "SELECT c.target_ref,t.data_origin,t.attributes FROM evaluation.cases c "
        "JOIN evaluation.experiment_targets t ON t.experiment_id=c.experiment_id "
        "AND t.target_ref=c.target_ref WHERE c.run_id=%s",
        (run_id,),
    )
    targets = await cursor.fetchall()
    refs = sorted(item["target_ref"] for item in targets)
    if (
        len(refs) != row["requested_count"]
        or not refs
        or any(item["data_origin"] != "synthetic" or item["attributes"] != {} for item in targets)
        or hashlib.sha256("\n".join(refs).encode()).hexdigest() != experiment["target_set_hash"]
    ):
        raise ApplicationError(409, "TARGET_SET_MISMATCH", "Target snapshot differs.")
    row["cs_profile"] = experiment["configuration"] != {}
    return row


async def append_event(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    job_id: UUID,
    kind: str,
    payload: dict[str, Any],
) -> None:
    """run 제어 잠금 아래에서 작업 이벤트의 다음 sequence를 추가한다.

    Args:
        conn: 제어 행 잠금을 가진 transaction 연결.
        job_id: 평가 작업 UUID.
        kind: 고정 이벤트 종류.
        payload: 원문·정답 없는 상태 메타데이터.

    Returns:
        None: 호출자 commit에 포함할 이벤트를 추가한다.
    """
    await conn.execute(
        "INSERT INTO runtime.job_events(job_id,sequence,type,payload) "
        "SELECT %s,coalesce(max(sequence),0)+1,%s,%s FROM runtime.job_events WHERE job_id=%s",
        (job_id, kind, Jsonb(payload), job_id),
    )


async def claim_run(
    settings: Settings,
    access: EvaluationAccessSettings,
    run_id: UUID,
) -> SmokeLease | None:
    """신규 또는 만료된 합성 실행을 한 worker만 소유하도록 인수한다.

    Args:
        settings: 로컬 DB·환경 설정.
        access: 허용 구성과 lease 시간.
        run_id: 실행 UUID.

    Returns:
        SmokeLease | None: 신규 generation 또는 활성 소유자/종료로 인한 미획득.
    """
    if not admission_available(settings, access):
        raise ApplicationError(503, "SMOKE_WORKER_DISABLED", "Smoke worker is disabled.")
    async with connection(settings) as conn:
        async with conn.transaction():
            row = await locked_run(conn, settings, access, run_id)
            if (
                row["status"] not in {"QUEUED", "RUNNING"}
                or row["lease_live"]
                or row["needs_review"]
            ):
                return None
            holder = uuid4()
            cursor = await conn.execute(
                "UPDATE evaluation.run_control SET generation=generation+1,holder_id=%s,"
                "lease_expires_at=clock_timestamp()+make_interval(secs=>%s),version=version+1 "
                "WHERE run_id=%s RETURNING generation",
                (holder, access.smoke_lease_seconds, run_id),
            )
            claimed = await cursor.fetchone()
            assert claimed is not None
            generation = claimed["generation"]
            await conn.execute(
                "UPDATE evaluation.runs SET status='RUNNING',started_at=coalesce(started_at,now()) "
                "WHERE id=%s",
                (run_id,),
            )
            await conn.execute(
                "UPDATE runtime.jobs SET status='RUNNING' WHERE id=%s", (row["job_id"],)
            )
            await append_event(
                conn,
                row["job_id"],
                "job.started",
                {"generation": generation, "resumed": row["status"] == "RUNNING"},
            )
    return SmokeLease(run_id, holder, generation)


async def advance_run(
    settings: Settings,
    access: EvaluationAccessSettings,
    lease: SmokeLease,
) -> bool:
    """합성 사례 하나를 원자적으로 완료하거나 전체 실행을 종료한다.

    외부 호출이 없는 FakeProvider만 transaction 안에서 호출한다. 이 구현을 실제
    모델 호출에 재사용하면 안 된다. 실제 모델은 별도 intent·UNKNOWN 복구가 필요하다.

    Args:
        settings: 로컬 DB 연결.
        access: 현재 허용 설정과 lease 시간.
        lease: 현재 worker의 generation·holder.

    Returns:
        bool: 전체 종료면 True, 사례 하나 완료 후 다음 단계가 남으면 False.
    """
    async with connection(settings) as conn:
        async with conn.transaction():
            row = await locked_run(conn, settings, access, lease.run_id)
            if (
                row["status"] != "RUNNING"
                or row["needs_review"]
                or not row["lease_live"]
                or row["holder_id"] != lease.holder_id
                or row["generation"] != lease.generation
            ):
                raise ApplicationError(409, "STALE_WORKER", "Worker ownership has expired.")
            cursor = await conn.execute(
                "SELECT id,target_ref FROM evaluation.cases WHERE run_id=%s AND status='PENDING' "
                "ORDER BY id LIMIT 1 FOR UPDATE",
                (lease.run_id,),
            )
            case = await cursor.fetchone()
            if case is not None:
                attempt = None
                if row["cs_profile"]:
                    attempt = run_case(case["target_ref"])
                    # 원시 검사 후 응답 존재만 보존한다. 임의 원문을 저장하지 않는다.
                    raw = "[REDACTED]" if attempt.raw_response is not None else None
                    parsed = attempt.prediction.model_dump() if attempt.prediction else None
                else:
                    # 운영 API 이미지는 eval 패키지를 포함하지 않는다.
                    from eval.smoke import FakeProvider, Prediction

                    prompt = 'Return JSON {"result":"ready"}.'
                    raw, _ = FakeProvider().generate(prompt)
                    parsed = Prediction.model_validate_json(raw).model_dump()
                cursor = await conn.execute(
                    "SELECT lease_expires_at>clock_timestamp() AS live "
                    "FROM evaluation.run_control WHERE run_id=%s",
                    (lease.run_id,),
                )
                current = await cursor.fetchone()
                if current is None or not current["live"]:
                    raise ApplicationError(409, "STALE_WORKER", "Worker ownership has expired.")
                if attempt is None:
                    await conn.execute(
                        "INSERT INTO evaluation.case_attempts "
                        "(id,case_id,step,attempt,is_final,outcome,provider,model_id,input_sha256,"
                        "raw_output,parsed_output,started_at,finished_at) "
                        "VALUES (%s,%s,'smoke',1,true,'VALID','fake','fake',%s,%s,%s,now(),now())",
                        (
                            uuid4(),
                            case["id"],
                            hashlib.sha256(prompt.encode()).hexdigest(),
                            raw,
                            Jsonb(parsed),
                        ),
                    )
                    case_status = "VALID"
                else:
                    cost_usd, pricing_version = attempt_cost(attempt)
                    await conn.execute(
                        "INSERT INTO evaluation.case_attempts "
                        "(id,case_id,step,attempt,is_final,outcome,provider,model_id,input_sha256,"
                        "raw_output,parsed_output,started_at,finished_at,model_version,finish_reasons,"
                        "error_code,http_status,latency_ms,input_tokens,output_tokens,thought_tokens,"
                        "cached_input_tokens,total_tokens,usage_raw,requested_model,call_attempts,"
                        "cost_usd,pricing_version) "
                        "VALUES (%s,%s,'classify',1,true,%s,'fake','fake',%s,%s,%s,now(),now(),"
                        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            uuid4(),
                            case["id"],
                            attempt.outcome,
                            attempt.input_sha256,
                            raw,
                            Jsonb(parsed) if parsed is not None else None,
                            attempt.model_version,
                            list(attempt.finish_reasons),
                            attempt.error_code,
                            attempt.http_status,
                            round(attempt.latency_ms) if attempt.latency_ms is not None else None,
                            attempt.usage.input_tokens,
                            attempt.usage.output_tokens,
                            attempt.usage.thought_tokens,
                            attempt.usage.cached_input_tokens,
                            attempt.usage.total_tokens,
                            Jsonb(attempt.usage.raw) if attempt.usage.raw is not None else None,
                            attempt.requested_model,
                            attempt.call_attempts,
                            cost_usd,
                            pricing_version,
                        ),
                    )
                    case_status = {
                        "VALID": "VALID",
                        "SCHEMA_VIOLATION": "INVALID_OUTPUT",
                        "TIMEOUT": "FAILED",
                        "TRANSPORT_FAILURE": "FAILED",
                        "HELD": "HELD",
                    }[attempt.outcome]
                await conn.execute(
                    "UPDATE evaluation.cases SET status=%s,final_output=%s,completed_at=now() "
                    "WHERE id=%s",
                    (case_status, Jsonb(parsed) if parsed is not None else None, case["id"]),
                )
                await conn.execute(
                    "UPDATE evaluation.runs SET valid_output_count=valid_output_count+%s,"
                    "held_count=held_count+%s,failed_count=failed_count+%s WHERE id=%s",
                    (
                        int(case_status == "VALID"),
                        int(case_status == "HELD"),
                        int(case_status in {"INVALID_OUTPUT", "FAILED"}),
                        lease.run_id,
                    ),
                )
                await append_event(conn, row["job_id"], "job.progress", {"step": "smoke"})
                await conn.execute(
                    "UPDATE evaluation.run_control SET lease_expires_at=clock_timestamp()+"
                    "make_interval(secs=>%s),version=version+1 WHERE run_id=%s",
                    (access.smoke_lease_seconds, lease.run_id),
                )
                return False
            cursor = await conn.execute(
                "SELECT count(*) AS total,count(*) FILTER (WHERE status='VALID') AS valid "
                "FROM evaluation.cases WHERE run_id=%s",
                (lease.run_id,),
            )
            counts = await cursor.fetchone()
            if (
                counts is None
                or counts["total"] != row["requested_count"]
                or (not row["cs_profile"] and counts["valid"] != counts["total"])
            ):
                raise ApplicationError(409, "SMOKE_INCOMPLETE", "Smoke cases are incomplete.")
            state = (
                "SUCCEEDED"
                if counts["valid"] == counts["total"]
                else "PARTIAL"
                if counts["valid"]
                else "FAILED"
            )
            await conn.execute(
                "UPDATE evaluation.runs SET status=%s,finished_at=now() WHERE id=%s",
                (state, lease.run_id),
            )
            await conn.execute(
                "UPDATE runtime.jobs SET status=%s WHERE id=%s", (state, row["job_id"])
            )
            await conn.execute(
                "UPDATE evaluation.run_control SET holder_id=NULL,lease_expires_at=NULL,version=version+1 "
                "WHERE run_id=%s",
                (lease.run_id,),
            )
            await append_event(
                conn,
                row["job_id"],
                "job.failed" if state == "FAILED" else "job.completed",
                {"state": state, "synthetic": True},
            )
            return True


REVIEW_CODES = frozenset(
    {
        "FORBIDDEN",
        "SMOKE_ACCESS_DENIED",
        "FINAL_TEST_LOCKED",
        "EXPERIMENT_NOT_APPROVED",
        "LOCAL_SMOKE_ONLY",
        "EXPERIMENT_HASH_MISMATCH",
        "TARGET_SET_MISMATCH",
        "SMOKE_INCOMPLETE",
    }
)


async def park_run(
    settings: Settings,
    access: EvaluationAccessSettings,
    run_id: UUID,
    code: str,
    lease: SmokeLease | None,
) -> None:
    """검증 위반 실행을 자동 재시도에서 제외하고 안전한 코드로 검토 필요를 기록한다.

    Args:
        settings: 로컬 DB 설정.
        access: worker 활성 환경 설정.
        run_id: 대상 실행 UUID.
        code: 명시 허용한 안전한 오류 코드.
        lease: 오류가 난 실행자의 소유권. 인수 전 실패면 None.

    Returns:
        None: 최신 소유권을 침범하지 않는 경우에만 보류를 commit한다.
    """
    if not admission_available(settings, access) or code not in REVIEW_CODES:
        return
    async with connection(settings) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='2s'")
            await conn.execute("SET LOCAL statement_timeout='5s'")
            await conn.execute(
                "SELECT run_id FROM evaluation.run_control WHERE run_id=%s FOR UPDATE", (run_id,)
            )
            cursor = await conn.execute(
                "SELECT c.*,r.status,r.job_id,r.needs_review,"
                "c.lease_expires_at>clock_timestamp() AS lease_live "
                "FROM evaluation.run_control c JOIN evaluation.runs r ON r.id=c.run_id "
                "JOIN evaluation.admission_receipts a ON a.run_id=r.id "
                "WHERE c.run_id=%s FOR UPDATE OF c",
                (run_id,),
            )
            row = await cursor.fetchone()
            if row is None or row["status"] not in {"QUEUED", "RUNNING"} or row["needs_review"]:
                return
            if lease is None:
                if row["lease_live"]:
                    return
            elif row["generation"] != lease.generation or row["holder_id"] != lease.holder_id:
                return
            await conn.execute(
                "UPDATE evaluation.runs SET needs_review=true,failure_code=%s WHERE id=%s",
                (code, run_id),
            )
            await conn.execute(
                "UPDATE evaluation.run_control SET generation=generation+1,holder_id=NULL,"
                "lease_expires_at=NULL,version=version+1 WHERE run_id=%s",
                (run_id,),
            )
            await append_event(
                conn, row["job_id"], "job.progress", {"needs_review": True, "code": code}
            )


async def dispatch_one(settings: Settings, access: EvaluationAccessSettings) -> UUID | None:
    """outbox에서 허용된 미완료 합성 작업 하나를 인수하여 끝까지 실행한다.

    Args:
        settings: 로컬 환경과 DB 연결 설정.
        access: 현재 명시적 허용 목록.

    Returns:
        UUID | None: 완료한 실행 UUID 또는 현재 실행할 작업 없음.
    """
    if not admission_available(settings, access):
        raise ApplicationError(503, "SMOKE_WORKER_DISABLED", "Smoke worker is disabled.")
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT r.id FROM runtime.dispatch_outbox o JOIN evaluation.runs r ON r.job_id=o.job_id "
            "JOIN evaluation.run_control c ON c.run_id=r.id "
            "JOIN evaluation.experiments e ON e.id=r.experiment_id "
            "WHERE r.status IN ('QUEUED','RUNNING') AND NOT r.needs_review AND "
            "(c.holder_id IS NULL OR c.lease_expires_at<=clock_timestamp()) "
            "ORDER BY o.created_at,o.job_id LIMIT 1",
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    lease = None
    try:
        lease = await claim_run(settings, access, row["id"])
        if lease is None:
            return None
        while not await advance_run(settings, access, lease):
            pass
        return lease.run_id
    except ApplicationError as error:
        if error.code in REVIEW_CODES:
            await park_run(settings, access, row["id"], error.code, lease)
        raise


def main() -> None:
    """서버 환경 설정으로 합성 outbox 작업 하나만 처리한다.

    Args:
        없음. 설정은 서버 환경 변수에서 읽는다.

    Returns:
        None: 처리한 식별자만 출력한다. 실패 시 안전한 코드와 비정상 종료를 반환한다.
    """
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        result = asyncio.run(dispatch_one(Settings(), EvaluationAccessSettings()))
    except HTTPException:
        raise SystemExit("DEPENDENCY_UNAVAILABLE") from None
    except ApplicationError as error:
        raise SystemExit(error.code) from None
    print(str(result) if result else "NO_ELIGIBLE_WORK")


if __name__ == "__main__":
    main()
