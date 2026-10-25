"""호출자 트랜잭션에서 평가 작업·이벤트·전송 의도를 함께 저장한다."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.pq import TransactionStatus
from pydantic import BaseModel, ConfigDict, Field

from backend.shared.errors import ApplicationError


class EvaluationJobRequest(BaseModel):
    """권한·동결 실험 검증을 마친 서버 코드가 만드는 접수 입력."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: UUID
    run_id: UUID
    input_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class JobAdmission:
    """저장된 식별자와 신규 여부. 외부 실행 또는 HTTP 접수 성공을 뜻하지 않는다."""

    job_id: UUID
    run_id: UUID
    created: bool


async def admit_evaluation_job(
    conn: psycopg.AsyncConnection[dict[str, Any]], request: EvaluationJobRequest
) -> JobAdmission:
    """명시적으로 열린 트랜잭션 안에서 같은 의도의 작업을 한 번만 만든다.

    호출자가 먼저 현재 사용자·평가자·실험 접근·시험 차단을 검사해야 한다.
    반환 후 신규 접수에만 동일 연결로 run/cases/control을 저장하고 commit한다.
    내부에서 commit하거나 외부 큐를 호출하지 않는다. 오류는 호출자가 전체 rollback한다.
    READ COMMITTED 격리 수준을 사용하며 상위 요청 경계에서 DB 대기 시간을 제한한다.

    Args:
        conn: dict_row를 사용하는 호출자 소유의 진행 중인 PostgreSQL 트랜잭션.
        request: 정규화 본문·조건 헤더 hash와 동결 입력 판본, 서버 생성 run ID.

    Returns:
        JobAdmission: 신규 또는 이미 저장된 동일 요청의 job/run 식별자.
    """
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError("An active caller transaction is required")
    job_id = uuid4()
    cursor = await conn.execute(
        "INSERT INTO runtime.jobs "
        "(id,owner,kind,business_id,input_version,actor_id,method,route,idempotency_key,request_hash) "
        "VALUES (%s,'platform','evaluation.run',%s,%s,%s,'POST',"
        "'/api/v1/platform/evaluations/runs',%s,%s) "
        "ON CONFLICT (actor_id,owner,method,route,idempotency_key) DO NOTHING RETURNING id",
        (
            job_id,
            request.run_id,
            request.input_version,
            request.actor_id,
            request.idempotency_key,
            request.request_hash,
        ),
    )
    if await cursor.fetchone() is None:
        cursor = await conn.execute(
            "SELECT id,business_id,request_hash,input_version FROM runtime.jobs "
            "WHERE actor_id=%s AND owner='platform' AND method='POST' "
            "AND route='/api/v1/platform/evaluations/runs' AND idempotency_key=%s",
            (request.actor_id, request.idempotency_key),
        )
        existing = await cursor.fetchone()
        if existing is None:
            raise ApplicationError(503, "JOB_ADMISSION_UNAVAILABLE", "Retry with the same key.")
        if (
            existing["request_hash"] != request.request_hash
            or existing["input_version"] != request.input_version
        ):
            raise ApplicationError(409, "IDEMPOTENCY_CONFLICT", "The request key is already used.")
        return JobAdmission(existing["id"], existing["business_id"], False)
    await conn.execute(
        "INSERT INTO runtime.job_events(job_id,sequence,type) VALUES (%s,1,'job.queued')",
        (job_id,),
    )
    await conn.execute("INSERT INTO runtime.dispatch_outbox(job_id) VALUES (%s)", (job_id,))
    return JobAdmission(job_id, request.run_id, True)
