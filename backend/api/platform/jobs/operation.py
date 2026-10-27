"""보류된 전송의 저장된 operation을 읽기 전용으로 대조한다."""

import asyncio
import re

import httpx
from pydantic import SecretStr

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.jobs.cloud_run import (
    CloudRunJobsSettings,
    classify_response,
    require_transport,
)
from backend.api.platform.jobs.dispatch import DispatchLease, DispatchSettings
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


async def inspect_operation(
    settings: Settings,
    access: EvaluationAccessSettings,
    dispatch: DispatchSettings,
    jobs: CloudRunJobsSettings,
    attempt: DispatchLease,
    token: SecretStr,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """현재 UNKNOWN 시도에 저장된 operation의 접수 증거만 조회한다.

    Args:
        settings: 로컬 DB와 실행 환경.
        access: 합성 평가 허용 설정.
        dispatch: 전송 장부 설정.
        jobs: 고정 프로젝트·리전·Job과 제한 시간.
        attempt: 대조할 시도의 job·run·시도 번호·holder 식별자.
        token: 서버 OAuth access token. 호출자가 인증 공급자로 준비한다.
        transport: 검사 전용 합성 HTTP 전송기.

    Returns:
        bool: 같은 operation의 유효 접수 응답 확인 여부. False는 미접수 증거가 아니다.
    """
    require_transport(settings, access, dispatch, jobs)
    if (
        not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token.get_secret_value())
        or len(token.get_secret_value()) > 16384
    ):
        raise ApplicationError(503, "JOBS_AUTH_UNAVAILABLE", "Jobs authentication unavailable.")
    async with connection(settings) as conn:
        row = await (
            await conn.execute(
                "SELECT a.operation_name FROM runtime.dispatch_attempts a "
                "JOIN runtime.dispatch_outbox o ON o.job_id=a.job_id "
                "JOIN runtime.jobs j ON j.id=o.job_id "
                "WHERE a.job_id=%s AND a.attempt_no=%s AND a.holder_id=%s "
                "AND j.business_id=%s AND o.attempt_no=a.attempt_no "
                "AND o.state='UNKNOWN' AND a.state='UNKNOWN'",
                (attempt.job_id, attempt.attempt_no, attempt.holder_id, attempt.run_id),
            )
        ).fetchone()
    name = row["operation_name"] if row else None
    prefix = f"projects/{jobs.project_number}/locations/{jobs.region}/operations/"
    if not isinstance(name, str) or not re.fullmatch(
        re.escape(prefix) + r"[A-Za-z0-9_-]{1,128}", name
    ):
        return False
    # DB 연결을 닫은 뒤 조회한다. 조회 결과로 장부·실행 상태를 바꾸지 않는다.
    try:
        async with asyncio.timeout(jobs.timeout_seconds):
            async with httpx.AsyncClient(
                transport=transport,
                timeout=jobs.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "GET",
                    "https://run.googleapis.com/v2/" + name,
                    headers={
                        "Authorization": "Bearer " + token.get_secret_value(),
                        "Accept": "application/json",
                    },
                ) as response:
                    if response.status_code != 200 or (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        != "application/json"
                    ):
                        return False
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 65536:
                            return False
                        body.extend(chunk)
                    result = classify_response(200, bytes(body), jobs)
                    return result.outcome == "ACCEPTED" and result.operation_name == name
    except (httpx.HTTPError, TimeoutError):
        return False
