"""C/S·RFP 작업 전송 트리거(KAN-159). 실제 전송은 cloud_run.dispatch_one이 한다.

Cloud Scheduler가 `POST /internal/v1/platform/jobs/dispatch`를 주기적으로 부르고, 접수 직후에도 한 건을 먼저
보낸다(시작 지연을 줄인다). 접수 직후 전송은 실패해도 접수를 실패시키지 않고 다음 주기가 맡는다.
평가 작업은 이 경로로도 local/test 밖에서 전송되지 않는다(admission_available).

주기마다 시작하지 못한 C/S·RFP 작업을 실패로 닫는다. 전송이 끝내 안 되거나 Job이 뜨지 못하거나 worker가
죽으면 작업이 QUEUED·RUNNING에 남아 그 프로젝트의 다음 접수가 계속 409가 되기 때문이다.
"""

import asyncio
import logging
from typing import Any, Self

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.platform.jobs.cloud_run import (
    CloudRunJobsSettings,
    CsWorkerJobSettings,
    TftWorkerJobSettings,
    dispatch_one,
)
from backend.api.platform.jobs.dispatch import DispatchSettings, gate_open
from backend.api.platform.jobs.tft import SERVICE_ACCOUNT
from backend.api.platform.mail.storage import GoogleAccessToken
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ResponseMeta, SuccessResponse
from backend.shared.service_auth import PlatformCaller

LOGGER = logging.getLogger(__name__)
PREFIX = "/internal/v1/platform/jobs"
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
# 호출 한 번에 보내는 최대 건수. 남은 건은 다음 주기가 보낸다.
BATCH = 5
CLOSED = {"DISPATCH_DISABLED", "JOBS_TRANSPORT_DISABLED"}


class JobsSweepSettings(BaseSettings):
    """운영자가 Job·호출 계정을 준비한 뒤 별도로 켤 전송 트리거 설정."""

    model_config = SettingsConfigDict(
        env_prefix="JOBS_SWEEP_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    enabled: bool = False
    # 플랫폼 Cloud Run 서비스 주소(https origin). Scheduler ID 토큰의 audience다.
    service_url: str = ""
    # Scheduler가 ID 토큰을 받는 호출 전용 서비스 계정.
    invoker_email: str = ""
    # 살아 있는 lease 없이 이 시간 동안 이벤트가 없으면 작업을 실패로 닫는다. 전송 재시도(3회×30초)와
    # Job 시작 시간보다 길어야 한다.
    stale_seconds: int = Field(default=900, ge=300, le=86400)

    @model_validator(mode="after")
    def validate_enabled(self) -> Self:
        """켰을 때만 서비스 주소와 호출 계정 형식을 확인한다.

        Returns:
            Self: 비활성이거나 값이 맞는 설정.
        """
        if self.enabled and (
            not self.service_url.startswith("https://")
            or self.service_url.endswith("/")
            or not SERVICE_ACCOUNT.fullmatch(self.invoker_email)
        ):
            raise ValueError("전송 트리거에는 플랫폼 서비스 주소와 호출 계정이 필요하다.")
        return self


class SweepData(BaseModel):
    sent: int
    reaped: int


def auth_unavailable() -> ApplicationError:
    """원시 응답을 담지 않는 토큰 실패.

    Returns:
        ApplicationError: 503 JOBS_AUTH_UNAVAILABLE.
    """
    return ApplicationError(503, "JOBS_AUTH_UNAVAILABLE", "Jobs authentication unavailable.")


def configure_jobs_sweep(app: FastAPI, value: JobsSweepSettings, **overrides: Any) -> None:
    """트리거 설정을 앱에 둔다. 비활성이면 경로가 404이고 접수 직후 전송도 하지 않는다.

    Args:
        app: 플랫폼 앱. 작업 접수 설정을 먼저 마쳐야 한다.
        value: 트리거 설정.
        **overrides: 검사용 주입(`caller`, `token`, `transport`).

    Returns:
        None.
    """
    app.state.jobs_sweep_caller = None
    app.state.jobs_sweep = value
    app.state.jobs_dispatch_soon = dispatch_soon
    app.state.jobs_sweep_transport = overrides.get("transport")
    if not value.enabled:
        return
    app.state.jobs_sweep_caller = overrides.get("caller") or PlatformCaller(
        value.service_url, value.invoker_email
    )
    google = overrides.get("token") or GoogleAccessToken(
        scope=CLOUD_PLATFORM, error=auth_unavailable
    )

    async def token() -> SecretStr:
        return SecretStr(await google())

    app.state.jobs_sweep_token = token
    # 잘못된 설정은 요청 때가 아니라 시작할 때 드러낸다.
    app.state.jobs_sweep_jobs = overrides.get("jobs") or CloudRunJobsSettings()


async def reap_stale(app: FastAPI) -> int:
    """시작하지 못한 C/S·RFP 작업을 FAILED로 닫아 다음 접수를 연다.

    늦게 뜬 worker는 작업이 끝난 상태라 인수하지 못한다(job_runtime.claim). 살아 있는 lease가 있으면 건드리지
    않는다. 전송 장부는 그대로 두며, 남은 전송은 begin_send가 BLOCKED로 막는다.

    Args:
        app: 설정을 가진 플랫폼 앱.

    Returns:
        int: 닫은 작업 수. 게이트가 열린 owner만 본다.
    """
    gates = {"cs": app.state.cs_jobs, "tft": app.state.tft_jobs}
    owners = [owner for owner, gate in gates.items() if gate_open(gate)]
    if not owners:
        return 0
    async with connection(app.state.settings) as conn, conn.transaction():
        cursor = await conn.execute(
            "SELECT j.id FROM runtime.jobs j JOIN runtime.job_control c ON c.job_id=j.id "
            "WHERE j.owner=ANY(%s) AND j.status IN ('QUEUED','RUNNING') "
            "AND coalesce(c.lease_expires_at<=clock_timestamp(),true) "
            "AND (SELECT max(e.occurred_at) FROM runtime.job_events e WHERE e.job_id=j.id) "
            "<clock_timestamp()-make_interval(secs=>%s) "
            "ORDER BY j.created_at LIMIT %s FOR UPDATE OF j,c SKIP LOCKED",
            (owners, app.state.jobs_sweep.stale_seconds, BATCH),
        )
        rows = await cursor.fetchall()
        for row in rows:
            await conn.execute(
                "UPDATE runtime.job_control SET holder_id=NULL,lease_expires_at=NULL,"
                "version=version+1,failure_code='JOB_NOT_STARTED' WHERE job_id=%s",
                (row["id"],),
            )
            await conn.execute("UPDATE runtime.jobs SET status='FAILED' WHERE id=%s", (row["id"],))
            await conn.execute(
                "INSERT INTO runtime.job_events(job_id,sequence,type,payload) VALUES "
                "(%s,(SELECT max(sequence)+1 FROM runtime.job_events WHERE job_id=%s),"
                "'job.failed',%s)",
                (row["id"], row["id"], Jsonb({"state": "FAILED"})),
            )
    return len(rows)


async def sweep(app: FastAPI, limit: int) -> int:
    """전송할 수 있는 작업을 최대 limit건 보낸다.

    Args:
        app: 설정을 가진 플랫폼 앱.
        limit: 최대 건수.

    Returns:
        int: 전송을 시도한 건수. 게이트가 닫혀 있으면 0이다.
    """
    sent = 0
    tft_worker, cs_worker = TftWorkerJobSettings(), CsWorkerJobSettings()
    try:
        for _ in range(limit):
            result = await dispatch_one(
                app.state.settings,
                app.state.evaluation_access,
                DispatchSettings(),
                app.state.jobs_sweep_jobs,
                app.state.jobs_sweep_token,
                transport=app.state.jobs_sweep_transport,
                cs=app.state.cs_jobs,
                cs_worker=cs_worker,
                tft=app.state.tft_jobs,
                tft_worker=tft_worker,
            )
            if result is None:
                break
            sent += 1
    except ApplicationError as error:
        if error.code not in CLOSED:
            raise
    return sent


async def dispatch_soon(app: FastAPI) -> None:
    """접수 직후 한 건을 보낸다. 실패는 기록만 하고 Scheduler 주기에 맡긴다.

    끊으면 장부가 SENDING에 남으므로 시간 제한을 두지 않는다. dispatch_one의 토큰·전송 제한이
    tft·cs의 접수 호출 제한(20초)보다 짧다.

    Args:
        app: 플랫폼 앱.

    Returns:
        None.
    """
    if app.state.jobs_sweep_caller is None:
        return
    try:
        await sweep(app, 1)
    except Exception as error:
        LOGGER.warning("JOBS_DISPATCH_SOON_FAILED", extra={"error_type": type(error).__name__})


async def verify_caller(request: Request) -> None:
    """비활성이면 404, 지정 호출 계정의 ID 토큰이 아니면 401로 닫는다.

    Args:
        request: 내부 호출 요청.

    Returns:
        None. 통과하지 못하면 HTTPException을 낸다.
    """
    caller = request.app.state.jobs_sweep_caller
    if caller is None:
        raise HTTPException(404)
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer "):
        raise HTTPException(401)
    await asyncio.to_thread(caller.verify, values[0].removeprefix("Bearer "))


router = APIRouter(prefix=PREFIX, tags=["internal-jobs"], include_in_schema=False)


@router.post("/dispatch", dependencies=[Depends(verify_caller)])
async def dispatch(request: Request) -> SuccessResponse[SweepData]:
    """Scheduler가 부르는 전송 주기. 남은 작업은 다음 주기가 보낸다.

    Args:
        request: 내부 호출 요청.

    Returns:
        SuccessResponse[SweepData]: 전송을 시도한 건수.
    """
    reaped = await reap_stale(request.app)
    data = SweepData(sent=await sweep(request.app, BATCH), reaped=reaped)
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))
