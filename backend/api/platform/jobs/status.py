"""플랫폼 작업 상태 조회. 작업 owner마다 접근 검사기를 따로 둔다(KAN-194 9차 2/3).

- platform(evaluation.run): 기존 평가 규칙. 지정 평가자가 아니면 403, 평가 조건(본인·시험 제외 등)이 안 맞으면 404.
- cs(cs.analyze): 요청 사용자(actor_id) 본인 또는 cs_agent 역할만. 아니면 404.
- 그 밖의 owner(tft 등)와 없는 작업: 404. 검사기 표(CHECKERS)에 owner를 더해 확장한다.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import psycopg
from fastapi import HTTPException

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import check_actor
from backend.api.platform.jobs.schemas import CsJobStatus, EvaluationJobStatus
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

JobStatus = EvaluationJobStatus | CsJobStatus


async def get_evaluation_job(
    settings: Settings, access: EvaluationAccessSettings, actor: UUID, job_id: UUID
) -> EvaluationJobStatus:
    """현재 평가자의 자기 작업 상태와 이벤트 위치를 단일 SQL 스냅샷에서 읽는다.

    Args:
        settings: 플랫폼 DB 연결과 실행 환경.
        access: 현재 지정 평가자와 로컬 실행 설정.
        actor: 유효한 세션에서 얻은 사용자 UUID.
        job_id: URL에 지정한 작업 UUID.

    Returns:
        EvaluationJobStatus: 원문·정답·내부 전송 정보를 제외한 상태와 이벤트 위치.
    """
    check_actor(settings, access, actor)
    async with connection(settings) as conn:
        await conn.execute("SET LOCAL statement_timeout = '5s'")
        return await read_evaluation_job(conn, actor, job_id)


async def read_evaluation_job(
    conn: psycopg.AsyncConnection[dict[str, Any]], actor: UUID, job_id: UUID
) -> EvaluationJobStatus:
    """호출자의 읽기 트랜잭션에서 동일한 소유권·시험 차단 SQL을 재사용한다.

    Args:
        conn: 호출자가 관리하는 플랫폼 DB 연결.
        actor: 현재 지정 평가자 세션의 사용자 UUID.
        job_id: 조회할 작업 UUID.

    Returns:
        EvaluationJobStatus: 해당 SQL 스냅샷의 제한 상태.
    """
    cursor = await conn.execute(
        "SELECT j.id AS job_id,r.id AS run_id,r.status AS run_status,"
        "CASE WHEN r.status='TIMED_OUT' THEN 'FAILED' ELSE r.status END AS status,"
        "c.version,r.needs_review,"
        "(SELECT coalesce(max(sequence),0) FROM runtime.job_events "
        "WHERE job_id=j.id) AS last_event_sequence "
        "FROM runtime.jobs j "
        "JOIN evaluation.runs r ON r.id=j.business_id AND r.job_id=j.id "
        "JOIN evaluation.experiments e ON e.id=r.experiment_id "
        "JOIN evaluation.admission_receipts a ON a.run_id=r.id "
        "JOIN evaluation.run_control c ON c.run_id=r.id "
        "WHERE j.id=%s AND j.owner='platform' AND j.kind='evaluation.run' "
        "AND j.actor_id=%s AND r.requested_by_user_id=%s AND e.created_by_user_id=%s "
        "AND e.data_split IS DISTINCT FROM 'test'",
        (job_id, actor, actor, actor),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ApplicationError(404, "NOT_FOUND", "Job is unavailable.")
    return EvaluationJobStatus.model_validate(row)


async def read_cs_job(
    conn: psycopg.AsyncConnection[dict[str, Any]], principal: Principal, job_id: UUID
) -> CsJobStatus:
    """요청 사용자 본인 또는 cs_agent에게만 C/S 작업 상태를 같은 스냅샷에서 읽는다.

    Args:
        conn: 호출자가 관리하는 플랫폼 DB 연결.
        principal: 현재 세션 사용자.
        job_id: 조회할 작업 UUID.

    Returns:
        CsJobStatus: 상태·판본·마지막 이벤트 sequence. 권한이 없거나 없는 작업은 404다.
    """
    cursor = await conn.execute(
        "SELECT j.id AS job_id,j.subject_id AS ticket_id,j.status,c.version,"
        "(SELECT coalesce(max(sequence),0) FROM runtime.job_events "
        "WHERE job_id=j.id) AS last_event_sequence "
        "FROM runtime.jobs j JOIN runtime.job_control c ON c.job_id=j.id "
        "WHERE j.id=%s AND j.owner='cs' AND j.kind='cs.analyze' AND (j.actor_id=%s OR %s)",
        (job_id, principal.user_id, principal.has("cs_agent")),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ApplicationError(404, "NOT_FOUND", "Job is unavailable.")
    return CsJobStatus.model_validate(row)


async def read_platform_job(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    settings: Settings,
    access: EvaluationAccessSettings,
    principal: Principal,
    job_id: UUID,
) -> EvaluationJobStatus:
    """평가 작업은 기존 규칙(지정 평가자 403·평가 조건 404)으로 읽는다.

    Args:
        conn: 호출자가 관리하는 플랫폼 DB 연결.
        settings: 실행 환경.
        access: 지정 평가자·로컬 실행 설정.
        principal: 현재 세션 사용자.
        job_id: 작업 UUID.

    Returns:
        EvaluationJobStatus: 평가 작업 상태.
    """
    if principal.user_id not in access.evaluator_user_ids:
        raise HTTPException(403)
    check_actor(settings, access, principal.user_id)
    return await read_evaluation_job(conn, principal.user_id, job_id)


Checker = Callable[
    [psycopg.AsyncConnection[dict[str, Any]], Settings, EvaluationAccessSettings, Principal, UUID],
    Awaitable[JobStatus],
]


async def _cs_checker(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    settings: Settings,
    access: EvaluationAccessSettings,
    principal: Principal,
    job_id: UUID,
) -> JobStatus:
    """C/S 검사기(설정은 쓰지 않는다).

    Args:
        conn: DB 연결.
        settings: 실행 환경.
        access: 평가 설정.
        principal: 세션 사용자.
        job_id: 작업 UUID.

    Returns:
        JobStatus: C/S 작업 상태.
    """
    return await read_cs_job(conn, principal, job_id)


# owner별 접근 검사기. 여기 없는 owner(tft 등)는 404다.
CHECKERS: dict[str, Checker] = {"platform": read_platform_job, "cs": _cs_checker}


async def read_job(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    settings: Settings,
    access: EvaluationAccessSettings,
    principal: Principal,
    job_id: UUID,
) -> JobStatus:
    """작업 owner를 보고 그 owner의 검사기로 상태를 읽는다(같은 연결·스냅샷).

    Args:
        conn: 호출자가 관리하는 플랫폼 DB 연결.
        settings: 실행 환경.
        access: 지정 평가자·로컬 실행 설정.
        principal: 현재 세션 사용자.
        job_id: 작업 UUID.

    Returns:
        JobStatus: owner별 상태. 없는 작업·검사기 없는 owner는 404다.
    """
    cursor = await conn.execute("SELECT owner FROM runtime.jobs WHERE id=%s", (job_id,))
    row = await cursor.fetchone()
    checker = CHECKERS.get(row["owner"]) if row else None
    if checker is None:
        raise ApplicationError(404, "NOT_FOUND", "Job is unavailable.")
    return await checker(conn, settings, access, principal, job_id)


async def get_job(
    settings: Settings, access: EvaluationAccessSettings, principal: Principal, job_id: UUID
) -> JobStatus:
    """연결을 열어 owner별 검사기로 작업 상태를 읽는다.

    Args:
        settings: 플랫폼 DB 연결과 실행 환경.
        access: 지정 평가자·로컬 실행 설정.
        principal: 현재 세션 사용자.
        job_id: 작업 UUID.

    Returns:
        JobStatus: owner별 상태.
    """
    async with connection(settings) as conn:
        await conn.execute("SET LOCAL statement_timeout = '5s'")
        return await read_job(conn, settings, access, principal, job_id)
