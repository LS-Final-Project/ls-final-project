"""로컬 전용 합성 worker. 외부 모델·파일·최종 시험을 실행하지 않는다."""

import argparse
import asyncio
import hashlib
from dataclasses import dataclass
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
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings
from eval.smoke import FakeProvider, Prediction


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
    cursor = await conn.execute(
        "SELECT c.*,r.status,r.job_id,r.requested_count,r.requested_by_user_id,r.provider,"
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
            if row["status"] not in {"QUEUED", "RUNNING"} or row["lease_live"]:
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
                or not row["lease_live"]
                or row["holder_id"] != lease.holder_id
                or row["generation"] != lease.generation
            ):
                raise ApplicationError(409, "STALE_WORKER", "Worker ownership has expired.")
            cursor = await conn.execute(
                "SELECT id FROM evaluation.cases WHERE run_id=%s AND status='PENDING' "
                "ORDER BY id LIMIT 1 FOR UPDATE",
                (lease.run_id,),
            )
            case = await cursor.fetchone()
            if case is not None:
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
                await conn.execute(
                    "UPDATE evaluation.cases SET status='VALID',final_output=%s,completed_at=now() "
                    "WHERE id=%s",
                    (Jsonb(parsed), case["id"]),
                )
                await conn.execute(
                    "UPDATE evaluation.runs SET valid_output_count=valid_output_count+1 WHERE id=%s",
                    (lease.run_id,),
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
                or counts["valid"] != counts["total"]
            ):
                raise ApplicationError(409, "SMOKE_INCOMPLETE", "Smoke cases are incomplete.")
            await conn.execute(
                "UPDATE evaluation.runs SET status='SUCCEEDED',finished_at=now() WHERE id=%s",
                (lease.run_id,),
            )
            await conn.execute(
                "UPDATE runtime.jobs SET status='SUCCEEDED' WHERE id=%s", (row["job_id"],)
            )
            await conn.execute(
                "UPDATE evaluation.run_control SET holder_id=NULL,lease_expires_at=NULL,version=version+1 "
                "WHERE run_id=%s",
                (lease.run_id,),
            )
            await append_event(
                conn, row["job_id"], "job.completed", {"state": "SUCCEEDED", "synthetic": True}
            )
            return True


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
            "WHERE r.status IN ('QUEUED','RUNNING') AND "
            "(c.holder_id IS NULL OR c.lease_expires_at<=clock_timestamp()) "
            "AND r.requested_by_user_id=ANY(%s) AND e.config_hash=ANY(%s) "
            "ORDER BY o.created_at,o.job_id LIMIT 1",
            (list(access.evaluator_user_ids), list(access.approved_config_hashes)),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    lease = await claim_run(settings, access, row["id"])
    if lease is None:
        return None
    while not await advance_run(settings, access, lease):
        pass
    return lease.run_id


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
