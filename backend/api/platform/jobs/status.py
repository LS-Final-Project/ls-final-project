"""평가 실행 권한을 유지하는 작업 상태 조회."""

from typing import Any
from uuid import UUID

import psycopg

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import check_actor
from backend.api.platform.jobs.schemas import EvaluationJobStatus
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


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
