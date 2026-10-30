"""C/S·RFP 작업 전송 트리거(KAN-159). 실제 전송은 cloud_run.dispatch_one이 한다.

Cloud Scheduler가 `POST /internal/v1/platform/jobs/dispatch`를 주기적으로 부르고, 접수 직후에도 한 건을 먼저
보낸다(시작 지연을 줄인다). 접수 직후 전송은 실패해도 접수를 실패시키지 않고 다음 주기가 맡는다.
평가 작업은 이 경로로도 local/test 밖에서 전송되지 않는다(admission_available).
"""

import asyncio
import logging
from typing import Any, Self

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.platform.jobs.cloud_run import (
    CloudRunJobsSettings,
    CsWorkerJobSettings,
    TftWorkerJobSettings,
    dispatch_one,
)
from backend.api.platform.jobs.dispatch import DispatchSettings
from backend.api.platform.jobs.tft import SERVICE_ACCOUNT
from backend.api.platform.mail.storage import GoogleAccessToken
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
    data = SweepData(sent=await sweep(request.app, BATCH))
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))
