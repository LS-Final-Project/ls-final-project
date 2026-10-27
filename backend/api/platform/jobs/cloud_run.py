"""Cloud Run Jobs 한 건 전송. 호출자 토큰과 worker 실행 권한은 별개다."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.jobs.dispatch import (
    DispatchLease,
    DispatchSettings,
    begin_send,
    claim_next,
    record_result,
    require_local,
)
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


class CloudRunJobsSettings(BaseSettings):
    """호스트·프로젝트·리전·Job을 서버 설정으로 고정하며 기본 비활성이다."""

    model_config = SettingsConfigDict(env_prefix="EVALUATION_JOBS_", extra="ignore")
    enabled: bool = False
    project_number: str = Field(pattern=r"^[1-9][0-9]{5,19}$")
    region: Literal["asia-northeast3"] = "asia-northeast3"
    job: str = Field(pattern=r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")
    container: str = Field(default="worker", pattern=r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")
    timeout_seconds: float = Field(default=10, ge=0.1, le=60)


@dataclass(frozen=True)
class DeliveryResult:
    """외부 본문 대신 저장하는 전송 결과와 제한된 operation 식별자."""

    outcome: Literal["ACCEPTED", "REJECTED", "UNKNOWN"]
    operation_name: str | None = None


def require_transport(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    jobs: CloudRunJobsSettings,
) -> None:
    """운영 잠금과 전송 제한 시간을 확인한다.

    Args:
        settings: 실행 환경.
        access: 합성 평가 허용 설정.
        dispatch: 전송 장부 설정.
        jobs: 고정 Job과 전송 시간 제한.

    Returns:
        None: local/test의 명시적 허용과 lease보다 짧은 전송 제한일 때 반환한다.
    """
    require_local(settings, access, dispatch)
    if not jobs.enabled or jobs.timeout_seconds + 1 >= dispatch.lease_seconds:
        raise ApplicationError(403, "JOBS_TRANSPORT_DISABLED", "Jobs transport is disabled.")


def classify_response(status: int, body: bytes, jobs: CloudRunJobsSettings) -> DeliveryResult:
    """완전한 제한 크기 JSON만 해석하고 미확인 결과를 UNKNOWN으로 보존한다.

    Args:
        status: 고정 Google API의 HTTP 상태.
        body: 제한 크기로 읽은 응답.
        jobs: operation의 프로젝트·리전 검증 기준.

    Returns:
        DeliveryResult: 접수 확인·명확한 미접수·불명확 중 하나.
    """
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return DeliveryResult("UNKNOWN")
    if not isinstance(value, dict):
        return DeliveryResult("UNKNOWN")
    # 429/5xx/리디렉션은 서버에서 접수하지 않았다는 증거가 아니다.
    denied = {
        400: "INVALID_ARGUMENT",
        401: "UNAUTHENTICATED",
        403: "PERMISSION_DENIED",
        404: "NOT_FOUND",
    }
    error = value.get("error")
    if (
        status in denied
        and isinstance(error, dict)
        and type(error.get("code")) is int
        and error["code"] == status
        and error.get("status") == denied[status]
    ):
        return DeliveryResult("REJECTED")
    if status != 200:
        return DeliveryResult("UNKNOWN")
    name = value.get("name")
    prefix = f"projects/{jobs.project_number}/locations/{jobs.region}/operations/"
    if not isinstance(name, str) or not re.fullmatch(
        re.escape(prefix) + r"[A-Za-z0-9_-]{1,128}", name
    ):
        return DeliveryResult("UNKNOWN")
    # operation 실패는 실행 미생성의 증거가 아니므로 재전송하지 않는다.
    if "error" in value or type(value.get("done", False)) is not bool:
        return DeliveryResult("UNKNOWN", name)
    if value.get("done") is True:
        execution = value.get("response")
        execution_prefix = (
            f"projects/{jobs.project_number}/locations/{jobs.region}/jobs/{jobs.job}/executions/"
        )
        if (
            not isinstance(execution, dict)
            or execution.get("@type") != "type.googleapis.com/google.cloud.run.v2.Execution"
            or not isinstance(execution.get("name"), str)
            or not re.fullmatch(
                re.escape(execution_prefix) + r"[a-z0-9-]{1,128}", execution["name"]
            )
        ):
            return DeliveryResult("UNKNOWN", name)
    return DeliveryResult("ACCEPTED", name)


async def send_job(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    jobs: CloudRunJobsSettings,
    lease: DispatchLease,
    token: SecretStr,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DeliveryResult:
    """고정 Jobs API에 재시도 없이 한 번 POST하고 결과만 반환한다.

    Args:
        settings: local/test 실행 환경.
        access: 합성 평가 허용 설정.
        dispatch: 전송 장부 설정.
        jobs: 고정 Job과 전체 요청 시간 제한.
        lease: SENDING commit이 확인된 전송 식별자.
        token: 서버의 OAuth access token. 브라우저나 worker 토큰이 아니다.
        transport: 검사에서만 사용하는 합성 HTTP 전송기.

    Returns:
        DeliveryResult: timeout·단절·잘못된 응답은 UNKNOWN이며 자동 재시도하지 않는다.
    """
    require_transport(settings, access, dispatch, jobs)
    url = f"https://run.googleapis.com/v2/projects/{jobs.project_number}/locations/{jobs.region}/jobs/{jobs.job}:run"
    body = {
        "overrides": {
            "taskCount": 1,
            "containerOverrides": [
                {
                    "name": jobs.container,
                    "env": [{"name": "EVALUATION_RUN_ID", "value": str(lease.run_id)}],
                }
            ],
        }
    }
    try:
        async with asyncio.timeout(jobs.timeout_seconds):
            async with httpx.AsyncClient(
                transport=transport,
                timeout=jobs.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=body,
                    headers={
                        "Authorization": "Bearer " + token.get_secret_value(),
                        "Accept": "application/json",
                    },
                ) as response:
                    if (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        != "application/json"
                    ):
                        return DeliveryResult("UNKNOWN")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(chunks) + len(chunk) > 65536:
                            return DeliveryResult("UNKNOWN")
                        chunks.extend(chunk)
                    return classify_response(response.status_code, bytes(chunks), jobs)
    except (httpx.HTTPError, TimeoutError):
        return DeliveryResult("UNKNOWN")


async def dispatch_one(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    jobs: CloudRunJobsSettings,
    token_provider: Callable[[], Awaitable[SecretStr]],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DeliveryResult | None:
    """인증 준비 뒤 한 건을 확보하고 전송 시작 commit 뒤 DB 밖에서 전달한다.

    Args:
        settings: 로컬 DB·환경 설정.
        access: 현재 평가 승인 설정.
        dispatch: 전송 장부 허용·한도.
        jobs: 고정 Job과 요청 시간 제한.
        token_provider: 서버 OAuth 공급자. 실제 ADC 연결은 별도 운영 단위다.
        transport: 테스트의 합성 HTTP 전송기.

    Returns:
        DeliveryResult | None: 저장한 전송 결과 또는 확보/시작 가능한 작업 없음.
    """
    require_transport(settings, access, dispatch, jobs)
    # 인증 획득 실패는 전송 전이므로 장부를 확보하지 않는다.
    try:
        async with asyncio.timeout(5):
            token = await token_provider()
        if (
            not isinstance(token, SecretStr)
            or len(token.get_secret_value()) > 16384
            or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token.get_secret_value())
        ):
            raise ValueError("Invalid server token")
    except Exception:
        raise ApplicationError(
            503, "JOBS_AUTH_UNAVAILABLE", "Jobs authentication unavailable."
        ) from None
    lease = await claim_next(settings, access, dispatch)
    if lease is None or not await begin_send(settings, access, dispatch, lease):
        return None
    result = await send_job(settings, access, dispatch, jobs, lease, token, transport=transport)
    # 저장 실패는 호출자에게 전파하고 SENDING 만료 후 UNKNOWN으로 복구한다.
    await record_result(settings, access, dispatch, lease, result.outcome, result.operation_name)
    return result
