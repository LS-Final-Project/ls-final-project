"""운영자 명령으로만 쓰는 전송 장부 대조·재처리. 자동 실행·재시도는 없다."""

import argparse
import asyncio
import json
import sys
from typing import Any, Literal
from uuid import UUID

import httpx
from pydantic import SecretStr

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.jobs.cloud_run import CloudRunJobsSettings, require_transport
from backend.api.platform.jobs.dispatch import DispatchLease, DispatchSettings, require_local
from backend.api.platform.jobs.operation import inspect_operation
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

# 미전송이 확실한 상태만 다시 대기시킨다. UNKNOWN·MANUAL_CHECK·ACCEPTED는 재전송하지 않는다.
REQUEUE_STATES = ("BLOCKED", "EXHAUSTED")
# 외부로 보냈을 수 있는 시도. 하나라도 있으면 재처리하지 않는다.
SENT_ATTEMPT_STATES = ("SENDING", "ACCEPTED", "UNKNOWN", "MANUAL_CHECK")


async def ledger_status(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    job_id: UUID,
) -> dict[str, Any]:
    """전송 장부의 현재 상태와 시도별 상태를 operation 이름 원문 없이 읽는다.

    Args:
        settings: 로컬 DB와 실행 환경.
        access: 합성 평가 허용 설정.
        dispatch: 전송 장부 허용 설정.
        job_id: 조회할 작업 ID.

    Returns:
        dict: outbox 상태·시도 수·한도·재처리 기록과 시도별 상태·operation 보유 여부·대조 시각.
    """
    require_local(settings, access, dispatch)
    async with connection(settings) as conn:
        outbox = await (
            await conn.execute(
                "SELECT state,attempt_no,attempt_limit,requeue_count,requeued_at "
                "FROM runtime.dispatch_outbox WHERE job_id=%s",
                (job_id,),
            )
        ).fetchone()
        if outbox is None:
            raise ApplicationError(404, "NOT_FOUND", "Dispatch is unavailable.")
        attempts = await (
            await conn.execute(
                "SELECT attempt_no,state,operation_name IS NOT NULL AS has_operation,checked_at "
                "FROM runtime.dispatch_attempts WHERE job_id=%s ORDER BY attempt_no",
                (job_id,),
            )
        ).fetchall()
    return {
        "job_id": str(job_id),
        "state": outbox["state"],
        "attempt_no": outbox["attempt_no"],
        "attempt_limit": outbox["attempt_limit"],
        "requeue_count": outbox["requeue_count"],
        "requeued_at": outbox["requeued_at"].isoformat() if outbox["requeued_at"] else None,
        "attempts": [
            {
                "attempt_no": row["attempt_no"],
                "state": row["state"],
                "has_operation": row["has_operation"],
                "checked_at": row["checked_at"].isoformat() if row["checked_at"] else None,
            }
            for row in attempts
        ],
    }


async def reconcile_unknown(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    jobs: CloudRunJobsSettings,
    job_id: UUID,
    token: SecretStr,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Literal["ACCEPTED", "UNKNOWN", "MANUAL_CHECK"]:
    """UNKNOWN 전송 한 건을 저장된 operation으로 대조하고 양성 증거일 때만 ACCEPTED로 바꾼다.

    Args:
        settings: 로컬 DB와 실행 환경.
        access: 합성 평가 허용 설정.
        dispatch: 전송 장부 설정.
        jobs: 고정 프로젝트·리전·Job과 제한 시간.
        job_id: 대조할 작업 ID. outbox와 현재 시도가 모두 UNKNOWN이어야 한다.
        token: 운영자가 준비한 서버 OAuth access token.
        transport: 검사 전용 합성 HTTP 전송기.

    Returns:
        Literal: ACCEPTED(접수 확인), UNKNOWN(확인 못 함, 대조 시각만 기록), MANUAL_CHECK(operation
            이름이 없어 대조 불가, 종료 상태로 고정). 미접수로 판정하거나 재전송하지 않는다.
    """
    require_transport(settings, access, dispatch, jobs)
    async with connection(settings) as conn, conn.transaction():
        row = await _locked_unknown(conn, job_id)
        if row is None:
            raise ApplicationError(409, "INVALID_TRANSITION", "Dispatch is not UNKNOWN.")
        if row["operation_name"] is None:
            # 이름 없이는 외부 실행 내역과 맞출 근거가 없어 사람이 확인하도록 고정한다.
            await _set_state(conn, row, "MANUAL_CHECK")
            return "MANUAL_CHECK"
    lease = DispatchLease(job_id, row["business_id"], row["attempt_no"], row["holder_id"])
    # DB 연결을 닫은 뒤 조회한다. 조회 중 장부가 바뀌면 아래에서 다시 대조한다.
    accepted = await inspect_operation(
        settings, access, dispatch, jobs, lease, token, transport=transport
    )
    async with connection(settings) as conn, conn.transaction():
        current = await _locked_unknown(conn, job_id)
        if current is None or any(
            current[key] != row[key] for key in ("attempt_no", "holder_id", "operation_name")
        ):
            raise ApplicationError(409, "STALE_DISPATCH", "Dispatch changed during the check.")
        if accepted:
            await _set_state(conn, row, "ACCEPTED")
            return "ACCEPTED"
        await conn.execute(
            "UPDATE runtime.dispatch_attempts SET checked_at=clock_timestamp() "
            "WHERE job_id=%s AND attempt_no=%s",
            (job_id, row["attempt_no"]),
        )
    return "UNKNOWN"


async def requeue(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    job_id: UUID,
) -> dict[str, Any]:
    """미전송이 확실한 BLOCKED·EXHAUSTED 전송을 다시 대기시키고 시도 한도를 늘린다.

    Args:
        settings: 로컬 DB와 실행 환경.
        access: 합성 평가 허용 설정.
        dispatch: 전송 장부 설정. 늘어나는 한도는 현재 시도 수 + max_attempts다.
        job_id: 재처리할 작업 ID.

    Returns:
        dict: 재처리 후 장부 상태. 다른 상태나 외부로 보냈을 수 있는 시도가 있으면 409다.
            재전송 전 평가 승인·소유권 재검사는 기존 begin_send가 다시 한다.
    """
    require_local(settings, access, dispatch)
    async with connection(settings) as conn, conn.transaction():
        row = await (
            await conn.execute(
                "SELECT state,attempt_no FROM runtime.dispatch_outbox WHERE job_id=%s FOR UPDATE",
                (job_id,),
            )
        ).fetchone()
        if row is None:
            raise ApplicationError(404, "NOT_FOUND", "Dispatch is unavailable.")
        sent = await (
            await conn.execute(
                "SELECT 1 FROM runtime.dispatch_attempts WHERE job_id=%s AND state=ANY(%s) LIMIT 1",
                (job_id, list(SENT_ATTEMPT_STATES)),
            )
        ).fetchone()
        if row["state"] not in REQUEUE_STATES or sent is not None:
            raise ApplicationError(409, "INVALID_TRANSITION", "Dispatch cannot be requeued.")
        await conn.execute(
            "UPDATE runtime.dispatch_outbox SET state='PENDING',attempt_limit=attempt_no+%s,"
            "requeue_count=requeue_count+1,requeued_at=clock_timestamp(),"
            "next_attempt_at=clock_timestamp() WHERE job_id=%s",
            (dispatch.max_attempts, job_id),
        )
    return await ledger_status(settings, access, dispatch, job_id)


async def _locked_unknown(conn: Any, job_id: UUID) -> dict[str, Any] | None:
    """outbox와 현재 시도가 모두 UNKNOWN인 행을 잠가 읽는다.

    Args:
        conn: 트랜잭션 연결.
        job_id: 작업 ID.

    Returns:
        dict | None: 시도 번호·holder·operation 이름·실행 ID. 조건이 맞지 않으면 None.
    """
    result: dict[str, Any] | None = await (
        await conn.execute(
            "SELECT o.job_id,o.attempt_no,a.holder_id,a.operation_name,j.business_id "
            "FROM runtime.dispatch_outbox o JOIN runtime.jobs j ON j.id=o.job_id "
            "JOIN runtime.dispatch_attempts a ON a.job_id=o.job_id AND a.attempt_no=o.attempt_no "
            "WHERE o.job_id=%s AND o.state='UNKNOWN' AND a.state='UNKNOWN' FOR UPDATE OF o,a",
            (job_id,),
        )
    ).fetchone()
    return result


async def _set_state(conn: Any, row: dict[str, Any], state: str) -> None:
    """잠근 시도와 outbox를 같은 종료 상태로 바꾸고 대조 시각을 남긴다.

    Args:
        conn: 행 잠금을 보유한 트랜잭션 연결.
        row: _locked_unknown 결과.
        state: ACCEPTED 또는 MANUAL_CHECK.

    Returns:
        None: 호출자의 commit으로 두 기록이 함께 확정된다.
    """
    await conn.execute(
        "UPDATE runtime.dispatch_attempts SET state=%s,checked_at=clock_timestamp() "
        "WHERE job_id=%s AND attempt_no=%s",
        (state, row["job_id"], row["attempt_no"]),
    )
    await conn.execute(
        "UPDATE runtime.dispatch_outbox SET state=%s WHERE job_id=%s", (state, row["job_id"])
    )


def main(argv: list[str] | None = None) -> int:
    """운영자가 한 작업의 전송 장부를 조회·대조·재처리하는 명령.

    Args:
        argv: 명령행 인자. reconcile의 access token은 stdin 한 줄로만 받는다.

    Returns:
        int: 성공 0(JSON 한 줄), 오류 1(코드 한 줄). 토큰·operation 이름 원문은 출력하지 않는다.
    """
    parser = argparse.ArgumentParser(
        prog="python -m backend.api.platform.jobs.recovery",
        description="전송 장부 운영자 명령; 자동 재시도 없음",
    )
    parser.add_argument("command", choices=("status", "reconcile", "requeue"))
    parser.add_argument("--job-id", type=UUID, required=True)
    args = parser.parse_args(argv)
    try:
        settings, access, dispatch = Settings(), EvaluationAccessSettings(), DispatchSettings()
        if args.command == "status":
            result: Any = asyncio.run(ledger_status(settings, access, dispatch, args.job_id))
        elif args.command == "requeue":
            result = asyncio.run(requeue(settings, access, dispatch, args.job_id))
        else:
            token = sys.stdin.readline().strip()
            if not token:
                raise ApplicationError(503, "JOBS_AUTH_UNAVAILABLE", "Token is required.")
            outcome = asyncio.run(
                reconcile_unknown(
                    settings,
                    access,
                    dispatch,
                    CloudRunJobsSettings(),
                    args.job_id,
                    SecretStr(token),
                )
            )
            result = {"job_id": str(args.job_id), "state": outcome}
    except ApplicationError as error:
        print(error.code, file=sys.stderr)
        return 1
    except Exception:
        # 설정 오류·DB 장애의 원문(DSN·토큰 포함 가능)을 출력하지 않는다.
        print("DISPATCH_RECOVERY_FAILED", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
