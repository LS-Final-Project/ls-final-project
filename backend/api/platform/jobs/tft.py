"""RFP 자사 대응(owner=tft) 작업의 플랫폼 내부 접수(KAN-182, 명세 v1.1 §4.3).

tft 서비스가 프로젝트 접근 검사와 본문 검증을 마친 뒤 `POST /internal/v1/platform/jobs/tft`로
작업을 등록한다. tft_app은 runtime 권한이 없고 플랫폼(platform_app)이 저장한다.

- 기본 비활성(404). 켜면 Authorization의 Google ID 토큰을 플랫폼 audience·tft 실행 계정으로
  다시 검증한다(Cloud Run IAM과 별도).
- 같은 사용자·종류 경로·멱등 키의 재전송은 기존 작업을 돌려준다. 내용이 다르면 409.
- 같은 프로젝트에 진행 중 작업이 있으면 409 `TFT_JOB_IN_PROGRESS`.
- 처리 worker 연결(`TFT_JOBS_PROCESSOR_CONNECTED`) 전에는 저장하지 않고 503
  `TFT_PROCESSOR_NOT_CONNECTED`다(명세 §4.3).
- 전송 의도(dispatch_outbox)는 만들지 않는다. 평가 전송기는 outbox의
  모든 행을 평가 실행으로 보내므로, 연결 전에 tft 행을 넣으면 안 된다.
"""

import asyncio
import re
from typing import Any, Literal, Self
from uuid import UUID, uuid4

import httpx
import psycopg
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from psycopg.pq import TransactionStatus
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ResponseMeta, SuccessResponse
from backend.shared.service_auth import PlatformCaller

PREFIX = "/internal/v1/platform/jobs"
SERVICE_ACCOUNT = re.compile(r"[a-z][a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com")
TftJobKind = Literal["rfp.process", "rfp.export", "rfp.annotate"]
# 멱등 범위에 쓰는 원래 tft 경로. 마이그레이션의 CHECK와 같은 값이다.
ROUTES: dict[str, str] = {
    "rfp.process": "/api/v1/tft/projects/{project_id}/process",
    "rfp.export": "/api/v1/tft/projects/{project_id}/draft/exports",
    "rfp.annotate": "/api/v1/tft/projects/{project_id}/annotations/process",
}


class TftJobSettings(BaseSettings):
    """운영자가 tft 실행 계정을 정한 뒤 별도로 켤 내부 접수 설정."""

    model_config = SettingsConfigDict(
        env_prefix="TFT_JOBS_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    enabled: bool = False
    # 플랫폼 Cloud Run 서비스 주소(https origin). tft가 받는 ID 토큰의 audience다.
    service_url: str = ""
    # 접수를 부를 수 있는 tft 실행 서비스 계정.
    invoker_email: str = ""
    # tft 처리 worker가 작업을 RUNNING 이후로 옮길 수 있을 때만 켠다. 꺼져 있으면 저장하지 않고 503이다.
    # 켜지 않은 채 저장하면 QUEUED가 남아 그 프로젝트의 다음 접수가 계속 409가 된다.
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
            raise ValueError("tft 작업 접수에는 플랫폼 서비스 주소와 tft 호출 계정이 필요합니다.")
        return self


class TftJobRequest(BaseModel):
    """tft 서비스가 접근 검사·본문 검증 뒤 보내는 접수 입력. 원문·프롬프트는 받지 않는다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: UUID
    kind: TftJobKind
    project_id: UUID
    # 처리할 문서 판본·단계 등 동결 입력의 SHA-256. tft가 계산한다.
    input_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128)
    # 정규화한 원 요청 본문의 SHA-256. 브라우저 값을 그대로 쓰지 않는다.
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class TftJobAdmission(BaseModel):
    """저장된 작업. 실제 처리 시작을 뜻하지 않는다."""

    job_id: UUID
    kind: TftJobKind
    project_id: UUID
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]
    status_url: str
    created: bool


def admission(row: dict[str, Any], created: bool) -> TftJobAdmission:
    """저장 행을 응답으로 바꾼다.

    Args:
        row: runtime.jobs 행(id·kind·subject_id·status).
        created: 이번 요청이 만든 작업인지.

    Returns:
        TftJobAdmission: 플랫폼 상태 경로를 가리키는 접수 결과.
    """
    return TftJobAdmission(
        job_id=row["id"],
        kind=row["kind"],
        project_id=row["subject_id"],
        status=row["status"],
        status_url=f"/api/v1/platform/jobs/{row['id']}",
        created=created,
    )


async def admit_tft_job(
    conn: psycopg.AsyncConnection[dict[str, Any]], request: TftJobRequest
) -> TftJobAdmission:
    """호출자 트랜잭션 안에서 같은 의도의 tft 작업을 한 번만 만든다.

    Args:
        conn: dict_row를 쓰는 호출자 소유의 진행 중 트랜잭션.
        request: 검증을 마친 접수 입력.

    Returns:
        TftJobAdmission: 새 작업 또는 같은 요청으로 이미 만든 작업.

    Raises:
        ApplicationError: 다른 내용의 같은 키 409, 진행 중 작업 409, 미등록 사용자 422,
            재조회 실패 503.
    """
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError("An active caller transaction is required")
    job_id = uuid4()
    route = ROUTES[request.kind]
    try:
        # 진행 중 작업 인덱스 위반이 바깥 트랜잭션을 깨지 않도록 savepoint 안에서 넣는다.
        async with conn.transaction():
            cursor = await conn.execute(
                "INSERT INTO runtime.jobs (id,owner,kind,business_id,subject_id,input_version,"
                "actor_id,method,route,idempotency_key,request_hash) "
                "VALUES (%s,'tft',%s,%s,%s,%s,%s,'POST',%s,%s,%s) "
                "ON CONFLICT (actor_id,owner,method,route,idempotency_key) DO NOTHING "
                "RETURNING id,kind,subject_id,status",
                (
                    job_id,
                    request.kind,
                    job_id,
                    request.project_id,
                    request.input_version,
                    request.actor_id,
                    route,
                    request.idempotency_key,
                    request.request_hash,
                ),
            )
            row = await cursor.fetchone()
    except psycopg.errors.ForeignKeyViolation:
        # 등록되지 않은 사용자. 다시 보내도 같으므로 재시도 대상이 아니다.
        raise ApplicationError(422, "JOB_ACTOR_UNKNOWN", "The actor is not registered.") from None
    except psycopg.errors.UniqueViolation as error:
        if error.diag.constraint_name != "jobs_tft_one_active":
            raise
        raise ApplicationError(
            409, "TFT_JOB_IN_PROGRESS", "The project already has a running job."
        ) from None
    if row is None:
        cursor = await conn.execute(
            "SELECT id,kind,subject_id,status,request_hash,input_version FROM runtime.jobs "
            "WHERE actor_id=%s AND owner='tft' AND method='POST' AND route=%s "
            "AND idempotency_key=%s",
            (request.actor_id, route, request.idempotency_key),
        )
        existing = await cursor.fetchone()
        if existing is None:
            raise ApplicationError(503, "JOB_ADMISSION_UNAVAILABLE", "Retry with the same key.")
        if (
            existing["request_hash"] != request.request_hash
            or existing["input_version"] != request.input_version
            or existing["subject_id"] != request.project_id
        ):
            raise ApplicationError(409, "IDEMPOTENCY_CONFLICT", "The request key is already used.")
        return admission(existing, False)
    await conn.execute(
        "INSERT INTO runtime.job_events(job_id,sequence,type) VALUES (%s,1,'job.queued')",
        (job_id,),
    )
    return admission(row, True)


def configure_tft_jobs(app: FastAPI, value: TftJobSettings, **overrides: Any) -> None:
    """설정과 호출자 검증기를 앱에 둔다. 비활성이면 경로가 404로 닫힌다.

    Args:
        app: 플랫폼 앱.
        value: 내부 접수 설정.
        **overrides: 검사용 주입(`caller`).

    Returns:
        None.
    """
    app.state.tft_jobs = value
    app.state.tft_jobs_caller = None
    if value.enabled:
        app.state.tft_jobs_caller = overrides.get("caller") or PlatformCaller(
            value.service_url, value.invoker_email
        )


async def verify_caller(request: Request) -> None:
    """비활성이면 404, 지정 tft 실행 계정의 ID 토큰이 아니면 401로 닫는다.

    Args:
        request: 내부 호출 요청.

    Returns:
        None. 통과하지 못하면 HTTPException을 낸다.
    """
    caller = request.app.state.tft_jobs_caller
    if caller is None:
        raise HTTPException(404)
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer "):
        raise HTTPException(401)
    # 서명키 조회가 동기 HTTP라 이벤트 루프를 막지 않도록 스레드에서 검증한다.
    await asyncio.to_thread(caller.verify, values[0].removeprefix("Bearer "))


router = APIRouter(prefix=PREFIX, tags=["internal-jobs"], include_in_schema=False)


@router.post("/tft", status_code=201, dependencies=[Depends(verify_caller)])
async def admit(
    request: Request, response: Response, body: TftJobRequest
) -> SuccessResponse[TftJobAdmission]:
    """tft 작업을 저장한다. 새 작업은 201, 같은 요청의 재전송은 200이다.

    Args:
        request: 앱 설정을 가진 요청.
        response: 상태 코드를 바꿀 응답.
        body: 접수 입력.

    Returns:
        SuccessResponse[TftJobAdmission]: 작업 ID와 플랫폼 상태 경로.
    """
    if not request.app.state.tft_jobs.processor_connected:
        raise ApplicationError(
            503, "TFT_PROCESSOR_NOT_CONNECTED", "The RFP job processor is not connected yet."
        )
    async with connection(request.app.state.settings) as conn, conn.transaction():
        await conn.execute("SET LOCAL statement_timeout = '5s'")
        data = await admit_tft_job(conn, body)
    if not data.created:
        response.status_code = 200
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))
