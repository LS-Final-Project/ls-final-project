"""현재 권한·판본·멱등 키로 합성 실행을 취소하거나 미종료 실행을 재개한다."""

from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import canonical_hash, check_actor
from backend.api.platform.evaluations.schemas import EvaluationRunStatus
from backend.api.platform.evaluations.smoke_worker import append_event, locked_run
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ResponseMeta, SuccessResponse
from backend.shared.settings import Settings


async def control_run(
    settings: Settings,
    access: EvaluationAccessSettings,
    actor: UUID,
    run_id: UUID,
    action: Literal["cancel", "resume"],
    key: str,
    if_match: str | None,
    request_id: UUID,
) -> SuccessResponse[EvaluationRunStatus]:
    """현재 소유자 권한으로 제어 행을 잠그고 요청을 한 번만 적용한다.

    Args:
        settings: 로컬 환경과 플랫폼 DB 설정.
        access: 현재 지정 평가자와 허용 구성 목록.
        actor: 현재 서버 세션 사용자.
        run_id: 제어할 실행 UUID.
        action: 취소 또는 미종료 실행 재개.
        key: 8~128자 요청 키.
        if_match: 조회 응답의 따옴표로 감싼 version.
        request_id: 최초 응답에 보존할 요청 ID.

    Returns:
        SuccessResponse: 저장된 최초 제어 결과. 종료된 실행은 재개하지 않는다.
    """
    check_actor(settings, access, actor)
    async with connection(settings) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='2s'")
            await conn.execute("SET LOCAL statement_timeout='5s'")
            # 잠금 대기 전에 읽힌 조인 상태를 사용하지 않도록 잠금 뒤 새 snapshot으로 조회한다.
            await conn.execute(
                "SELECT c.run_id FROM evaluation.run_control c "
                "JOIN evaluation.runs r ON r.id=c.run_id "
                "JOIN evaluation.experiments e ON e.id=r.experiment_id "
                "WHERE c.run_id=%s AND r.requested_by_user_id=%s AND e.created_by_user_id=%s "
                "FOR UPDATE OF c",
                (run_id, actor, actor),
            )
            cursor = await conn.execute(
                "SELECT r.id,r.job_id,r.status,r.requested_count,r.needs_review,r.failure_code,"
                "c.version,c.lease_expires_at>clock_timestamp() AS lease_live "
                "FROM evaluation.runs r JOIN evaluation.run_control c ON c.run_id=r.id "
                "JOIN evaluation.experiments e ON e.id=r.experiment_id "
                "JOIN evaluation.admission_receipts a ON a.run_id=r.id "
                "WHERE r.id=%s AND r.requested_by_user_id=%s AND e.created_by_user_id=%s "
                "FOR UPDATE OF c",
                (run_id, actor, actor),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ApplicationError(404, "NOT_FOUND", "Run is unavailable.")
            # 재개는 재전송도 현행 실행 허용 조건을 검사한다. 취소는 허용 hash 철회 후에도 가능하다.
            if action == "resume":
                await locked_run(conn, settings, access, run_id)
            digest = canonical_hash({"if_match": if_match})
            cursor = await conn.execute(
                "SELECT request_hash,response FROM evaluation.run_action_receipts "
                "WHERE run_id=%s AND actor_id=%s AND action=%s AND idempotency_key=%s",
                (run_id, actor, action, key),
            )
            old = await cursor.fetchone()
            if old is not None:
                if old["request_hash"] != digest:
                    raise ApplicationError(
                        409, "IDEMPOTENCY_CONFLICT", "Request key is already used."
                    )
                return SuccessResponse[EvaluationRunStatus].model_validate(old["response"])
            if if_match is None:
                raise ApplicationError(428, "VERSION_REQUIRED", "If-Match is required.")
            if if_match != f'"{row["version"]}"':
                raise ApplicationError(412, "VERSION_CONFLICT", "Run version has changed.")
            terminal = row["status"] not in {"QUEUED", "RUNNING"}
            if action == "resume":
                if terminal or (row["lease_live"] and not row["needs_review"]):
                    raise ApplicationError(409, "INVALID_TRANSITION", "Run cannot be resumed.")
                await conn.execute(
                    "UPDATE evaluation.runs SET needs_review=false,failure_code=NULL WHERE id=%s",
                    (run_id,),
                )
                row["needs_review"], row["failure_code"] = False, None
            elif not terminal:
                await conn.execute(
                    "UPDATE evaluation.runs SET status='CANCELLED',finished_at=now(),needs_review=false "
                    "WHERE id=%s",
                    (run_id,),
                )
                await conn.execute(
                    "UPDATE runtime.jobs SET status='CANCELLED' WHERE id=%s", (row["job_id"],)
                )
                await conn.execute(
                    "UPDATE evaluation.run_control SET cancel_requested_at=now(),cancel_requested_by=%s "
                    "WHERE run_id=%s",
                    (actor, run_id),
                )
                row["status"], row["needs_review"] = "CANCELLED", False
            if not terminal:
                await conn.execute(
                    "UPDATE evaluation.run_control SET generation=generation+1,holder_id=NULL,"
                    "lease_expires_at=NULL,version=version+1 WHERE run_id=%s",
                    (run_id,),
                )
                row["version"] += 1
                await append_event(
                    conn,
                    row["job_id"],
                    "job.completed" if action == "cancel" else "job.progress",
                    {"state": row["status"], "action": action},
                )
            result = SuccessResponse[EvaluationRunStatus](
                data=EvaluationRunStatus(
                    run_id=run_id,
                    job_id=row["job_id"],
                    status=row["status"],
                    requested_count=row["requested_count"],
                    version=row["version"],
                    needs_review=row["needs_review"],
                    failure_code=row["failure_code"],
                ),
                meta=ResponseMeta(request_id=request_id),
            )
            await conn.execute(
                "INSERT INTO evaluation.run_action_receipts "
                "(run_id,actor_id,action,idempotency_key,request_hash,response) VALUES (%s,%s,%s,%s,%s,%s)",
                (run_id, actor, action, key, digest, Jsonb(result.model_dump(mode="json"))),
            )
    return result
