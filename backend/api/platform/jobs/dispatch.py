"""로컬 작업 전송 장부(평가 evaluation.run·C/S cs.analyze·RFP rfp.*). 외부 호출과 worker 실행 권한을 부여하지 않는다.

종류마다 따로 연다(KAN-194 9차 2/3, KAN-199). 평가 행은 기존 평가 local 접수 조건, C/S·RFP 행은
그 owner의 접수와 처리기 연결 설정(CsJobSettings·TftJobSettings)이 모두 켜졌을 때만 확보한다. 그 설정을 넘기지 않는
기존 호출자는 평가만 다룬다.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import admission_available
from backend.api.platform.evaluations.smoke_worker import locked_run
from backend.api.platform.jobs.cs import CsJobSettings
from backend.api.platform.jobs.tft import ROUTES as TFT_KINDS
from backend.api.platform.jobs.tft import TftJobSettings
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


class DispatchSettings(BaseSettings):
    """실제 전송 없이 장부 검증만 명시적으로 허용하는 설정."""

    model_config = SettingsConfigDict(env_prefix="EVALUATION_DISPATCH_", extra="ignore")
    enabled: bool = False
    lease_seconds: int = Field(default=30, ge=1, le=300)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_delay_seconds: int = Field(default=30, ge=1, le=3600)


@dataclass(frozen=True)
class DispatchLease:
    """평가 실행 generation과 별개인 전송 시도 식별자."""

    job_id: UUID
    # 평가는 실행 ID, C/S는 작업 자신(business_id)이다.
    run_id: UUID
    attempt_no: int
    holder_id: UUID
    kind: str = "evaluation.run"


def gate_open(owner: CsJobSettings | TftJobSettings | None) -> bool:
    """C/S·RFP 작업을 전송해도 되는지(그 owner의 접수·처리기 연결이 모두 켜짐, 기본 꺼짐).

    Args:
        owner: 그 owner의 접수 설정. None이면 닫힌다.

    Returns:
        bool: 열렸으면 True.
    """
    return owner is not None and owner.enabled and owner.processor_connected


def require_local(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    cs: CsJobSettings | None = None,
    tft: TftJobSettings | None = None,
) -> list[str]:
    """운영 또는 기본 비활성 상태에서 장부 변경을 거부하고, 전송할 수 있는 작업 종류를 돌려준다.

    Args:
        settings: 실행 환경.
        access: 합성 평가 접수 허용 설정.
        dispatch: 전송 장부 허용 설정.
        cs: C/S 접수 설정(없으면 C/S 행을 다루지 않는다).
        tft: RFP 접수 설정(없으면 rfp.* 행을 다루지 않는다).

    Returns:
        list[str]: 허용 종류(evaluation.run·cs.analyze·rfp.*). 하나도 없으면 403 DISPATCH_DISABLED다.
    """
    kinds = [kind for kind in ["evaluation.run"] if admission_available(settings, access)]
    kinds += ["cs.analyze"] if gate_open(cs) else []
    kinds += list(TFT_KINDS) if gate_open(tft) else []
    if not dispatch.enabled or not kinds:
        raise ApplicationError(403, "DISPATCH_DISABLED", "Dispatch is disabled.")
    return kinds


async def finish(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    row: dict[str, Any],
    state: str,
    delay: int = 0,
    operation_name: str | None = None,
) -> None:
    """잠긴 전송 시도와 outbox의 종료 상태를 함께 기록한다.

    Args:
        conn: outbox 잠금을 보유한 트랜잭션 연결.
        row: 현재 시도 번호를 가진 outbox 행.
        state: 원문이나 외부 오류를 포함하지 않는 고정 상태.
        delay: 명확한 거부 뒤 다음 확보까지의 초.
        operation_name: 검증된 Google operation 이름 또는 없음.

    Returns:
        None: 호출자의 commit으로 두 기록이 함께 확정된다.
    """
    await conn.execute(
        "UPDATE runtime.dispatch_attempts SET state=%s,finished_at=clock_timestamp(),operation_name=%s "
        "WHERE job_id=%s AND attempt_no=%s",
        (state, operation_name, row["job_id"], row["attempt_no"]),
    )
    await conn.execute(
        "UPDATE runtime.dispatch_outbox SET state=%s,holder_id=NULL,lease_expires_at=NULL,"
        "next_attempt_at=clock_timestamp()+make_interval(secs => %s) WHERE job_id=%s",
        (state, delay, row["job_id"]),
    )


async def claim_next(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    cs: CsJobSettings | None = None,
    tft: TftJobSettings | None = None,
) -> DispatchLease | None:
    """허용 종류의 전송 한 건을 확보하거나 만료된 전송의 불명확 상태를 보존한다.

    Args:
        settings: 로컬 DB 설정.
        access: 합성 접수 설정.
        dispatch: 전송 장부 허용·lease·시도 한도.
        cs: C/S 접수 설정(없으면 C/S 행은 집지 않고 PENDING으로 둔다).
        tft: RFP 접수 설정(없으면 rfp.* 행은 집지 않고 PENDING으로 둔다).

    Returns:
        DispatchLease | None: 새 전송 소유권. 만료된 SENDING은 UNKNOWN으로 보류한다.
    """
    kinds = require_local(settings, access, dispatch, cs, tft)
    async with connection(settings) as conn, conn.transaction():
        cursor = await conn.execute(
            "SELECT o.*,j.business_id,j.kind FROM runtime.dispatch_outbox o "
            "JOIN runtime.jobs j ON j.id=o.job_id "
            "WHERE ((o.state IN ('PENDING','REJECTED') AND o.next_attempt_at<=clock_timestamp()) "
            "OR (o.state IN ('CLAIMED','SENDING') AND o.lease_expires_at<=clock_timestamp())) "
            "AND j.kind = ANY(%s) "
            "ORDER BY o.created_at,o.job_id LIMIT 1 FOR UPDATE OF o SKIP LOCKED",
            (kinds,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if row["state"] == "SENDING":
            await finish(conn, row, "UNKNOWN")
            return None
        if row["state"] == "CLAIMED":
            await conn.execute(
                "UPDATE runtime.dispatch_attempts SET state='EXPIRED',finished_at=clock_timestamp() "
                "WHERE job_id=%s AND attempt_no=%s",
                (row["job_id"], row["attempt_no"]),
            )
        # 운영자 재처리(recovery.requeue)가 늘린 한도가 있으면 그 값을 쓴다.
        if row["attempt_no"] >= (row.get("attempt_limit") or dispatch.max_attempts):
            await conn.execute(
                "UPDATE runtime.dispatch_outbox SET state='EXHAUSTED',holder_id=NULL,"
                "lease_expires_at=NULL WHERE job_id=%s",
                (row["job_id"],),
            )
            return None
        lease = DispatchLease(
            row["job_id"], row["business_id"], row["attempt_no"] + 1, uuid4(), row["kind"]
        )
        await conn.execute(
            "UPDATE runtime.dispatch_outbox SET state='CLAIMED',attempt_no=%s,holder_id=%s,"
            "lease_expires_at=clock_timestamp()+make_interval(secs => %s) WHERE job_id=%s",
            (lease.attempt_no, lease.holder_id, dispatch.lease_seconds, lease.job_id),
        )
        await conn.execute(
            "INSERT INTO runtime.dispatch_attempts(job_id,attempt_no,holder_id,state) "
            "VALUES (%s,%s,%s,'CLAIMED')",
            (lease.job_id, lease.attempt_no, lease.holder_id),
        )
        return lease


async def current_attempt(
    conn: psycopg.AsyncConnection[dict[str, Any]], lease: DispatchLease, state: str
) -> dict[str, Any]:
    """전송 담당자·시도·상태·만료를 잠금 뒤 새 스냅샷으로 검증한다.

    Args:
        conn: 전송 트랜잭션 연결.
        lease: 호출자의 전송 소유권.
        state: 허용되는 현재 상태.

    Returns:
        dict: 검증된 행. 오래되거나 만료된 호출은 409다.
    """
    await conn.execute(
        "SELECT job_id FROM runtime.dispatch_outbox WHERE job_id=%s FOR UPDATE", (lease.job_id,)
    )
    cursor = await conn.execute(
        "SELECT o.*,j.business_id,j.actor_id,j.input_version FROM runtime.dispatch_outbox o "
        "JOIN runtime.jobs j ON j.id=o.job_id WHERE o.job_id=%s "
        "AND o.state=%s AND o.attempt_no=%s AND o.holder_id=%s "
        "AND o.lease_expires_at>clock_timestamp() AND j.business_id=%s",
        (lease.job_id, state, lease.attempt_no, lease.holder_id, lease.run_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ApplicationError(409, "STALE_DISPATCH", "Dispatch ownership expired.")
    return row


async def worker_sendable(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    settings: Settings,
    gate: CsJobSettings | TftJobSettings | None,
    owner: str,
    job_id: UUID,
) -> bool:
    """C/S·RFP 작업이 지금 전송해도 되는 상태인지 본다(게이트·진행 중·취소 없음·worker lease 없음).

    Args:
        conn: 전송 트랜잭션 연결.
        settings: 실행 환경.
        gate: 그 owner의 접수 설정.
        owner: cs 또는 tft.
        job_id: 작업 ID.

    Returns:
        bool: 게이트가 열렸고 작업이 QUEUED·RUNNING이며 취소 요청이 없고 살아 있는 worker lease가 없으면 True.
    """
    if not gate_open(gate):
        return False
    cursor = await conn.execute(
        "SELECT j.status,c.cancel_requested_at,"
        "coalesce(c.lease_expires_at>clock_timestamp(),false) AS lease_live "
        "FROM runtime.jobs j JOIN runtime.job_control c ON c.job_id=j.id "
        "WHERE j.id=%s AND j.owner=%s",
        (job_id, owner),
    )
    job = await cursor.fetchone()
    return (
        job is not None
        and job["status"] in {"QUEUED", "RUNNING"}
        and job["cancel_requested_at"] is None
        and not job["lease_live"]
    )


async def begin_send(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    lease: DispatchLease,
    cs: CsJobSettings | None = None,
    tft: TftJobSettings | None = None,
) -> bool:
    """현재 종류별 조건(평가 승인·C/S·RFP 게이트와 작업 상태)을 재검사하고 외부 전송 전에 SENDING을 commit한다.

    Args:
        settings: 로컬 DB 설정.
        access: 현재 지정 평가자·승인 구성.
        dispatch: 장부 허용 설정.
        lease: 확보한 전송 소유권.
        cs: C/S 접수 설정(C/S 작업 전송에 필요).
        tft: RFP 접수 설정(RFP 작업 전송에 필요).

    Returns:
        bool: True일 때만 호출자가 트랜잭션 밖에서 전송할 수 있다. 실행 권한은 별도다.
    """
    require_local(settings, access, dispatch, cs, tft)
    async with connection(settings) as conn, conn.transaction():
        row = await current_attempt(conn, lease, "CLAIMED")
        if lease.kind != "evaluation.run":
            owner, gate = ("cs", cs) if lease.kind == "cs.analyze" else ("tft", tft)
            if not await worker_sendable(conn, settings, gate, owner, lease.job_id):
                await finish(conn, row, "BLOCKED")
                return False
            await start_sending(conn, lease)
            return True
        try:
            run = await locked_run(conn, settings, access, lease.run_id)
        except ApplicationError:
            await finish(conn, row, "BLOCKED")
            return False
        cursor = await conn.execute(
            "SELECT config_hash FROM evaluation.experiments WHERE id=%s", (run["experiment_id"],)
        )
        experiment = await cursor.fetchone()
        assert experiment is not None
        if (
            run["status"] not in {"QUEUED", "RUNNING"}
            or run["needs_review"]
            or run["lease_live"]
            or run["job_id"] != lease.job_id
            or run["requested_by_user_id"] != row["actor_id"]
            or experiment["config_hash"] != row["input_version"]
        ):
            await finish(conn, row, "BLOCKED")
            return False
        await start_sending(conn, lease)
        return True


async def start_sending(
    conn: psycopg.AsyncConnection[dict[str, Any]], lease: DispatchLease
) -> None:
    """lease를 다시 확인하고 전송 시작(SENDING)을 기록한다.

    Args:
        conn: 전송 트랜잭션 연결.
        lease: 전송 소유권.

    Returns:
        None: 호출자의 commit으로 확정된다.
    """
    # 검사 중 lease가 만료될 수 있으므로 전송 시작 직전에 다시 검사한다.
    await current_attempt(conn, lease, "CLAIMED")
    await conn.execute(
        "UPDATE runtime.dispatch_outbox SET state='SENDING' WHERE job_id=%s", (lease.job_id,)
    )
    await conn.execute(
        "UPDATE runtime.dispatch_attempts SET state='SENDING',sent_at=clock_timestamp() "
        "WHERE job_id=%s AND attempt_no=%s",
        (lease.job_id, lease.attempt_no),
    )


async def record_result(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    lease: DispatchLease,
    result: Literal["ACCEPTED", "REJECTED", "UNKNOWN"],
    operation_name: str | None = None,
    cs: CsJobSettings | None = None,
    tft: TftJobSettings | None = None,
) -> None:
    """전송 결과만 저장하며 평가 상태·worker lease·이벤트는 변경하지 않는다.

    Args:
        settings: 로컬 DB 설정.
        access: 합성 접수 설정.
        dispatch: 장부 설정과 명확한 거부의 재시도 간격.
        lease: 전송을 시작한 소유권.
        result: 접수 확인, 명확한 미접수, 결과 불명확 중 하나.
        operation_name: 외부 본문 대신 보존할 operation 식별자.
        cs: C/S 접수 설정(C/S 작업 결과 기록에 필요).
        tft: RFP 접수 설정(RFP 작업 결과 기록에 필요).

    Returns:
        None: 전송 결과를 commit한다. 만료·중복·오래된 결과는 409다.
    """
    require_local(settings, access, dispatch, cs, tft)
    if result not in {"ACCEPTED", "REJECTED", "UNKNOWN"}:
        raise ValueError("Unsupported dispatch result")
    if operation_name is not None and not re.fullmatch(
        r"projects/[1-9][0-9]{5,19}/locations/asia-northeast3/operations/[A-Za-z0-9_-]{1,128}",
        operation_name,
    ):
        raise ValueError("Invalid operation name")
    async with connection(settings) as conn, conn.transaction():
        row = await current_attempt(conn, lease, "SENDING")
        await finish(
            conn,
            row,
            result,
            dispatch.retry_delay_seconds if result == "REJECTED" else 0,
            operation_name,
        )
