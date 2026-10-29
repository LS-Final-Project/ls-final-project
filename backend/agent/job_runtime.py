"""플랫폼 작업 worker 공통부(KAN-194 C/S, KAN-199 RFP). runtime.job_control 소유권과 이벤트 기록만 다룬다.

worker 이미지(C/S·tft)에는 `backend.api.platform`이 없으므로 여기서도 import하지 않는다.

- 연결은 worker 로그인(그 owner의 worker 역할 멤버) 하나다. 역할 검사는 평가 worker와 같은 규칙이다.
- 인수: QUEUED이거나 lease가 끊긴 RUNNING이고 취소 요청이 없을 때만. generation+1·holder·lease·RUNNING·job.started.
- 진행: 소유권 재검사·lease 연장·job.progress. 취소 요청이면 쓰지 않고 False.
- 종료: 소유권을 놓고 상태·종료 이벤트를 남긴다. 실패는 고정 failure_code만. 소유권을 잃었으면 LeaseLost.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


class LeaseLost(Exception):
    """다른 worker가 소유권을 가져갔거나 lease가 만료됐다."""


@dataclass(frozen=True)
class Lease:
    """이 worker가 인수한 작업과 소유권."""

    job_id: UUID
    kind: str
    # C/S는 문의, RFP는 프로젝트 ID다.
    subject_id: UUID
    input_version: str
    actor_id: UUID
    holder_id: UUID
    generation: int


def parse_job_id(value: str | None) -> UUID:
    """표준 하이픈 형식 UUID만 받는다.

    Args:
        value: CS_JOB_ID·TFT_JOB_ID 환경 변수.

    Returns:
        UUID: 작업 ID. 없거나 형식이 다르면 ApplicationError INVALID_JOB_ID다.
    """
    try:
        job_id = UUID(value or "")
        if str(job_id) != value:
            raise ValueError
        return job_id
    except ValueError:
        raise ApplicationError(400, "INVALID_JOB_ID", "Job ID is required.") from None


def connect(settings: Settings) -> psycopg.Connection[dict[str, Any]]:
    """worker 로그인으로 연결한다.

    Args:
        settings: database_url이 있는 설정.

    Returns:
        psycopg.Connection: dict_row 연결. DSN이 없으면 ApplicationError WORKER_DATABASE_REQUIRED다.
    """
    if settings.database_url is None:
        raise ApplicationError(503, "WORKER_DATABASE_REQUIRED", "Database is required.")
    return psycopg.connect(settings.database_url.get_secret_value(), row_factory=dict_row)


def check_role(conn: psycopg.Connection[dict[str, Any]], role: str) -> None:
    """로그인 역할이 그 worker 역할 멤버이고 앱·마이그레이터·특권 역할이 아닌지 본다(평가 worker와 같은 규칙).

    Args:
        conn: worker 연결.
        role: cs_worker 또는 tft_worker.

    Returns:
        None. 아니면 ApplicationError WORKER_ROLE_REQUIRED다.
    """
    row = conn.execute(
        "SELECT current_user=session_user AND pg_has_role(current_user,%s,'USAGE') AND NOT "
        "(rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls OR rolreplication) AND NOT EXISTS ("
        "SELECT 1 FROM pg_roles p WHERE p.rolname IN "
        "('platform_app','cs_app','tft_app','platform_migrator','cs_migrator','tft_migrator') "
        "AND pg_has_role(current_user,p.oid,'MEMBER')) AS allowed "
        "FROM pg_roles WHERE rolname=current_user",
        (role,),
    ).fetchone()
    if row is None or not row["allowed"]:
        raise ApplicationError(403, "WORKER_ROLE_REQUIRED", "Worker role is required.")


def append(
    conn: psycopg.Connection[dict[str, Any]], job_id: UUID, kind: str, payload: dict[str, Any]
) -> None:
    """다음 sequence로 이벤트를 남긴다(호출자가 job_control 행을 잠근 상태).

    Args:
        conn: 진행 중 트랜잭션.
        job_id: 작업 ID.
        kind: 이벤트 종류(job.started 등).
        payload: 허용 키만 담은 payload.

    Returns:
        None.
    """
    conn.execute(
        "INSERT INTO runtime.job_events(job_id,sequence,type,payload) VALUES (%s,"
        "(SELECT coalesce(max(sequence),0)+1 FROM runtime.job_events WHERE job_id=%s),%s,%s)",
        (job_id, job_id, kind, Jsonb(payload)),
    )


def claim(settings: Settings, owner: str, lease_seconds: int, job_id: UUID) -> Lease | None:
    """그 owner의 대기 중이거나 lease가 끊긴 실행 중 작업을 인수한다.

    Args:
        settings: worker DB 설정.
        owner: cs 또는 tft(종류는 DB CHECK가 owner별로 묶는다).
        lease_seconds: lease 길이.
        job_id: 작업 ID.

    Returns:
        Lease | None: 새 소유권. 없는 작업·다른 owner·종료·취소 요청·살아 있는 다른 소유자면 None이다.
    """
    with connect(settings) as conn, conn.transaction():
        row = conn.execute(
            "SELECT j.kind,j.status,j.subject_id,j.input_version,j.actor_id,c.cancel_requested_at,"
            "coalesce(c.lease_expires_at>clock_timestamp(),false) AS lease_live "
            "FROM runtime.jobs j JOIN runtime.job_control c ON c.job_id=j.id "
            "WHERE j.id=%s AND j.owner=%s FOR UPDATE OF j,c",
            (job_id, owner),
        ).fetchone()
        if (
            row is None
            or row["status"] not in {"QUEUED", "RUNNING"}
            or row["cancel_requested_at"] is not None
            or row["lease_live"]
        ):
            return None
        holder = uuid4()
        claimed = conn.execute(
            "UPDATE runtime.job_control SET generation=generation+1,holder_id=%s,"
            "lease_expires_at=clock_timestamp()+make_interval(secs=>%s),version=version+1 "
            "WHERE job_id=%s RETURNING generation",
            (holder, lease_seconds, job_id),
        ).fetchone()
        assert claimed is not None
        conn.execute("UPDATE runtime.jobs SET status='RUNNING' WHERE id=%s", (job_id,))
        append(conn, job_id, "job.started", {"state": "RUNNING"})
    return Lease(
        job_id,
        row["kind"],
        row["subject_id"],
        row["input_version"],
        row["actor_id"],
        holder,
        claimed["generation"],
    )


def held(conn: psycopg.Connection[dict[str, Any]], lease: Lease) -> bool:
    """소유권을 잠그고 확인한다.

    Args:
        conn: 진행 중 트랜잭션.
        lease: 이 worker의 소유권.

    Returns:
        bool: 취소 요청이 있으면 True. 소유권을 잃었으면 LeaseLost다.
    """
    row = conn.execute(
        "SELECT holder_id,generation,coalesce(lease_expires_at>clock_timestamp(),false) AS live,"
        "cancel_requested_at FROM runtime.job_control WHERE job_id=%s FOR UPDATE",
        (lease.job_id,),
    ).fetchone()
    if (
        row is None
        or row["holder_id"] != lease.holder_id
        or row["generation"] != lease.generation
        or not row["live"]
    ):
        raise LeaseLost
    return row["cancel_requested_at"] is not None


def progress(settings: Settings, lease_seconds: int, lease: Lease, payload: dict[str, Any]) -> bool:
    """단계 하나가 끝났음을 남기고 lease를 연장한다.

    Args:
        settings: worker DB 설정.
        lease_seconds: lease 길이.
        lease: 소유권.
        payload: 단계 payload(허용 키만).

    Returns:
        bool: 계속하면 True, 취소 요청이 있으면 False(이벤트를 남기지 않는다).
    """
    with connect(settings) as conn, conn.transaction():
        if held(conn, lease):
            return False
        conn.execute(
            "UPDATE runtime.job_control SET "
            "lease_expires_at=clock_timestamp()+make_interval(secs=>%s) WHERE job_id=%s",
            (lease_seconds, lease.job_id),
        )
        append(conn, lease.job_id, "job.progress", payload)
    return True


def finish(
    settings: Settings,
    lease: Lease,
    state: str,
    payload: dict[str, Any],
    failure_code: str | None = None,
) -> None:
    """작업 상태를 확정하고 소유권을 놓는다.

    Args:
        settings: worker DB 설정.
        lease: 소유권(잃었으면 LeaseLost).
        state: SUCCEEDED·CANCELLED·FAILED.
        payload: 종료 이벤트의 추가 키.
        failure_code: FAILED일 때의 고정 코드.

    Returns:
        None.
    """
    with connect(settings) as conn, conn.transaction():
        held(conn, lease)
        conn.execute(
            "UPDATE runtime.job_control SET holder_id=NULL,lease_expires_at=NULL,"
            "version=version+1,failure_code=%s WHERE job_id=%s",
            (failure_code, lease.job_id),
        )
        conn.execute("UPDATE runtime.jobs SET status=%s WHERE id=%s", (state, lease.job_id))
        kind = "job.failed" if state == "FAILED" else "job.completed"
        append(conn, lease.job_id, kind, {"state": state, **payload})


def fail(settings: Settings, lease: Lease, code: str) -> None:
    """작업을 FAILED·고정 코드로 남긴다. 소유권을 이미 잃었으면 쓰지 않는다.

    Args:
        settings: worker DB 설정.
        lease: 소유권.
        code: failure_code.

    Returns:
        None.
    """
    try:
        finish(settings, lease, "FAILED", {}, failure_code=code)
    except LeaseLost:
        pass
