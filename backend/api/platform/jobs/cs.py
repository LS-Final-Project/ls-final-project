"""C/S 문의 처리(owner=cs, kind=cs.analyze) 작업의 플랫폼 내부 접수(KAN-194 9차 1/3).

cs 서비스(KAN-107)가 문의 접근 검사를 마친 뒤 `POST /internal/v1/platform/jobs/cs`로 작업을 등록한다. cs_app은
runtime 권한이 없고 플랫폼(platform_app)이 저장한다.

- 기본 비활성(404). 켜면 Authorization의 Google ID 토큰을 플랫폼 audience·cs 실행 계정으로 다시 검증한다.
- 같은 사용자·경로·멱등 키의 재전송은 기존 작업을 돌려준다. 문의·입력 판본이 다르면 409 IDEMPOTENCY_CONFLICT.
- 같은 문의에 진행 중(QUEUED·RUNNING) 작업이 있으면 409 `CS_JOB_IN_PROGRESS`.
- 처리 worker·전송 분기 연결(`CS_JOBS_PROCESSOR_CONNECTED`) 전에는 저장하지 않고 503 `CS_PROCESSOR_NOT_CONNECTED`다.
  켜지 않은 채 저장하면 전송 의도가 평가 경로로 가서 BLOCKED가 되고 그 문의의 다음 접수가 계속 409가 된다.
- 평가와 같이 접수 트랜잭션에서 job.queued 이벤트·전송 의도(dispatch_outbox)·작업 제어 행(runtime.job_control)을
  함께 만든다(tft는 전송 의도를 만들지 않는다).
"""

import asyncio
import hashlib
import json
from typing import Any, Literal, Self
from uuid import UUID, uuid4

import httpx
import psycopg
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from psycopg.pq import TransactionStatus
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.platform.jobs.tft import PREFIX, SERVICE_ACCOUNT
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ResponseMeta, SuccessResponse
from backend.shared.service_auth import PlatformCaller

# 멱등 범위에 쓰는 cs 분석 경로(KAN-107 /api/v1/cs/tickets/{ticket_id}/analysis). 마이그레이션 CHECK와 같다.
ROUTE = "/api/v1/cs/tickets/{ticket_id}/analysis"


class CsJobSettings(BaseSettings):
    """운영자가 cs 실행 계정을 정한 뒤 별도로 켤 내부 접수 설정."""

    model_config = SettingsConfigDict(
        env_prefix="CS_JOBS_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    enabled: bool = False
    # 플랫폼 Cloud Run 서비스 주소(https origin). cs가 받는 ID 토큰의 audience다.
    service_url: str = ""
    # 접수를 부를 수 있는 cs 실행 서비스 계정.
    invoker_email: str = ""
    # 전송 분기와 cs worker가 연결됐을 때만 켠다(KAN-194 9차 2/3·3/3). 꺼져 있으면 저장하지 않고 503이다.
    processor_connected: bool = False

    @model_validator(mode="after")
    def validate_enabled(self) -> Self:
        """켰을 때만 서비스 주소와 호출 계정 형식을 확인한다.

        Returns:
            Self: 비활성이거나 값이 맞는 설정.
        """
        if not self.enabled:
            return self
        origin = httpx.URL(self.service_url) if self.service_url else None
        if (
            origin is None
            or origin.scheme != "https"
            or not origin.host
            or origin.raw_path not in (b"", b"/")
            or origin.query
            or origin.userinfo
            or self.service_url.endswith("/")
            or not SERVICE_ACCOUNT.fullmatch(self.invoker_email)
        ):
            raise ValueError("cs 작업 접수에는 플랫폼 서비스 주소와 cs 호출 계정이 필요합니다.")
        return self


class CsJobRequest(BaseModel):
    """cs 서비스가 문의 접근 검사 뒤 보내는 접수 입력. 원문·프롬프트는 받지 않는다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_id: UUID
    # 처리할 문의 판본 등 동결 입력의 SHA-256. cs가 계산한다.
    input_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128)
    # 요청한 사용자(identity.users). 작업의 actor이며 상태 조회 권한의 기준이다.
    requested_by: UUID

    def request_hash(self) -> str:
        """멱등 비교에 쓰는 정규화 본문 hash(문의·입력 판본).

        Returns:
            str: 정렬 JSON의 SHA-256.
        """
        body = {"ticket_id": str(self.ticket_id), "input_version": self.input_version}
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


class CsJobAdmission(BaseModel):
    """저장된 작업. 실제 처리 시작을 뜻하지 않는다."""

    job_id: UUID
    ticket_id: UUID
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]
    status_url: str
    created: bool


def admission(row: dict[str, Any], created: bool) -> CsJobAdmission:
    """저장 행을 응답으로 바꾼다.

    Args:
        row: runtime.jobs 행(id·subject_id·status).
        created: 이번 요청이 만든 작업인지.

    Returns:
        CsJobAdmission: 플랫폼 상태 경로를 가리키는 접수 결과.
    """
    return CsJobAdmission(
        job_id=row["id"],
        ticket_id=row["subject_id"],
        status=row["status"],
        status_url=f"/api/v1/platform/jobs/{row['id']}",
        created=created,
    )


async def admit_cs_job(
    conn: psycopg.AsyncConnection[dict[str, Any]], request: CsJobRequest
) -> CsJobAdmission:
    """호출자 트랜잭션 안에서 같은 의도의 cs 작업을 한 번만 만든다.

    Args:
        conn: dict_row를 쓰는 호출자 소유의 진행 중 트랜잭션.
        request: 검증을 마친 접수 입력.

    Returns:
        CsJobAdmission: 새 작업(이벤트·전송 의도·제어 행 포함) 또는 같은 요청으로 이미 만든 작업.

    Raises:
        ApplicationError: 다른 내용의 같은 키 409, 진행 중 작업 409, 미등록 사용자 422, 재조회 실패 503.
    """
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError("An active caller transaction is required")
    job_id = uuid4()
    request_hash = request.request_hash()
    try:
        # 진행 중 작업 인덱스 위반이 바깥 트랜잭션을 깨지 않도록 savepoint 안에서 넣는다.
        async with conn.transaction():
            cursor = await conn.execute(
                "INSERT INTO runtime.jobs (id,owner,kind,business_id,subject_id,input_version,"
                "actor_id,method,route,idempotency_key,request_hash) "
                "VALUES (%s,'cs','cs.analyze',%s,%s,%s,%s,'POST',%s,%s,%s) "
                "ON CONFLICT (actor_id,owner,method,route,idempotency_key) DO NOTHING "
                "RETURNING id,subject_id,status",
                (
                    job_id,
                    job_id,
                    request.ticket_id,
                    request.input_version,
                    request.requested_by,
                    ROUTE,
                    request.idempotency_key,
                    request_hash,
                ),
            )
            row = await cursor.fetchone()
    except psycopg.errors.ForeignKeyViolation:
        # 등록되지 않은 사용자. 다시 보내도 같으므로 재시도 대상이 아니다.
        raise ApplicationError(422, "JOB_ACTOR_UNKNOWN", "The actor is not registered.") from None
    except psycopg.errors.UniqueViolation as error:
        if error.diag.constraint_name != "jobs_cs_one_active":
            raise
        raise ApplicationError(
            409, "CS_JOB_IN_PROGRESS", "The ticket already has a running job."
        ) from None
    if row is None:
        cursor = await conn.execute(
            "SELECT id,subject_id,status,request_hash FROM runtime.jobs "
            "WHERE actor_id=%s AND owner='cs' AND method='POST' AND route=%s "
            "AND idempotency_key=%s",
            (request.requested_by, ROUTE, request.idempotency_key),
        )
        existing = await cursor.fetchone()
        if existing is None:
            raise ApplicationError(503, "JOB_ADMISSION_UNAVAILABLE", "Retry with the same key.")
        if existing["request_hash"] != request_hash:
            raise ApplicationError(409, "IDEMPOTENCY_CONFLICT", "The request key is already used.")
        return admission(existing, False)
    await conn.execute(
        "INSERT INTO runtime.job_events(job_id,sequence,type) VALUES (%s,1,'job.queued')",
        (job_id,),
    )
    await conn.execute("INSERT INTO runtime.dispatch_outbox(job_id) VALUES (%s)", (job_id,))
    await conn.execute("INSERT INTO runtime.job_control(job_id) VALUES (%s)", (job_id,))
    return admission(row, True)


def configure_cs_jobs(app: FastAPI, value: CsJobSettings, **overrides: Any) -> None:
    """설정과 호출자 검증기를 앱에 둔다. 비활성이면 경로가 404로 닫힌다.

    Args:
        app: 플랫폼 앱.
        value: 내부 접수 설정.
        **overrides: 검사용 주입(`caller`).

    Returns:
        None.
    """
    app.state.cs_jobs = value
    app.state.cs_jobs_caller = None
    if value.enabled:
        app.state.cs_jobs_caller = overrides.get("caller") or PlatformCaller(
            value.service_url, value.invoker_email
        )


async def verify_caller(request: Request) -> None:
    """비활성이면 404, 지정 cs 실행 계정의 ID 토큰이 아니면 401로 닫는다.

    Args:
        request: 내부 호출 요청.

    Returns:
        None. 통과하지 못하면 HTTPException을 낸다.
    """
    caller = request.app.state.cs_jobs_caller
    if caller is None:
        raise HTTPException(404)
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer "):
        raise HTTPException(401)
    # 서명키 조회가 동기 HTTP라 이벤트 루프를 막지 않도록 스레드에서 검증한다.
    await asyncio.to_thread(caller.verify, values[0].removeprefix("Bearer "))


router = APIRouter(prefix=PREFIX, tags=["internal-jobs"], include_in_schema=False)


@router.post("/cs", status_code=201, dependencies=[Depends(verify_caller)])
async def admit(
    request: Request, response: Response, body: CsJobRequest
) -> SuccessResponse[CsJobAdmission]:
    """cs 작업을 저장한다. 새 작업은 201, 같은 요청의 재전송은 200이다.

    Args:
        request: 앱 설정을 가진 요청.
        response: 상태 코드를 바꿀 응답.
        body: 접수 입력.

    Returns:
        SuccessResponse[CsJobAdmission]: 작업 ID와 플랫폼 상태 경로.
    """
    if not request.app.state.cs_jobs.processor_connected:
        raise ApplicationError(
            503, "CS_PROCESSOR_NOT_CONNECTED", "The C/S job processor is not connected yet."
        )
    async with connection(request.app.state.settings) as conn, conn.transaction():
        await conn.execute("SET LOCAL statement_timeout = '5s'")
        data = await admit_cs_job(conn, body)
    if not data.created:
        response.status_code = 200
    else:
        await request.app.state.jobs_dispatch_soon(request.app)
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))
