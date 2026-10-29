"""메일 수집 워커. Cloud Scheduler가 등록을, Cloud Tasks가 사서함별 수집을 호출한다.

1. Scheduler가 `POST /internal/v1/platform/mail/dispatch`를 주기적으로 부른다. 연결된 사서함마다
   Cloud Tasks 작업 하나를 등록한다. 작업 이름에 사서함 ID와 시간 구간을 넣어, 같은 구간에
   다시 불려도 같은 사서함 작업이 두 번 생기지 않는다.
2. Tasks가 `POST /internal/v1/platform/mail/sync`를 사서함 하나로 부른다. 같은 사서함의 동시
   실행은 KAN-71 실행 표의 `RUNNING` 부분 유일 인덱스가 막는다.
3. 두 경로 모두 기본 비활성(404)이다. 활성이면 Authorization의 Google ID 토큰을 고정 Google
   서명키·수신 서비스 audience·지정 호출 계정으로 다시 검증한다(Cloud Run IAM과 별도).
"""

import asyncio
import base64
import json
import re
import time
from typing import Any, Literal, Self
from uuid import UUID

import httpx
import psycopg
from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import JSONResponse

from backend.api.platform.mail.links import judge_new_replies
from backend.api.platform.mail.storage import BUCKET_NAME, GcsObjectStore, GoogleAccessToken
from backend.api.platform.mail.sync import SyncResult, sync_mailbox
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ResponseMeta, SuccessResponse
from backend.shared.service_auth import PlatformCaller

PREFIX = "/internal/v1/platform/mail"
TASKS_API = "https://cloudtasks.googleapis.com/v2"
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
QUEUE = re.compile(
    r"projects/[a-z][a-z0-9-]{4,61}[a-z0-9]/locations/[a-z0-9-]+/queues/[A-Za-z0-9-]+"
)
SERVICE_ACCOUNT = re.compile(r"[a-z][a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com")
# 큐가 다시 시도하면 나아질 수 있는 실패. 그 밖의 실패는 다음 주기에 맡기고 2xx로 끝낸다.
TRANSIENT = {
    "GRAPH_THROTTLED",
    "GRAPH_UNAVAILABLE",
    "MAILBOX_TOKEN_UNAVAILABLE",
    "MAIL_STORAGE_UNAVAILABLE",
    "LEASE_EXPIRED",
    "SYNC_CANCELLED",
}
# 실행을 만들기 전에 거절된 경우. 다시 시도해도 같으므로 2xx로 끝낸다.
SKIPPED = {
    "MAIL_SYNC_IN_PROGRESS",
    "MAILBOX_NOT_CONNECTED",
    "MAILBOX_ACCESS_DENIED",
    "MAILBOX_RECONNECT_REQUIRED",
}


class MailSyncSettings(BaseSettings):
    """운영자가 큐·호출 계정·버킷을 준비한 뒤 별도로 켤 메일 수집 워커 설정."""

    model_config = SettingsConfigDict(
        env_prefix="MAIL_SYNC_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )
    enabled: bool = False
    # Cloud Tasks 큐 전체 이름: projects/<p>/locations/<l>/queues/<q>
    queue: str = ""
    # 플랫폼 Cloud Run 서비스 주소(https origin). 작업 대상 주소와 ID 토큰 audience로 쓴다.
    service_url: str = ""
    # Scheduler·Tasks가 ID 토큰을 받는 호출 전용 서비스 계정.
    invoker_email: str = ""
    interval_seconds: int = Field(default=300, ge=60, le=3600)
    object_bucket: str = Field(default="", validation_alias="MAIL_OBJECT_BUCKET")

    @model_validator(mode="after")
    def validate_enabled(self) -> Self:
        """켰을 때만 큐·주소·호출 계정·버킷 형식을 확인한다.

        Returns:
            Self: 비활성이거나 필요한 값이 모두 맞는 설정.
        """
        if not self.enabled:
            return self
        origin = httpx.URL(self.service_url) if self.service_url else None
        if (
            not QUEUE.fullmatch(self.queue)
            or origin is None
            or origin.scheme != "https"
            or not origin.host
            or origin.raw_path not in (b"", b"/")
            or origin.query
            or origin.userinfo
            or self.service_url.endswith("/")
            or not SERVICE_ACCOUNT.fullmatch(self.invoker_email)
            or not BUCKET_NAME.fullmatch(self.object_bucket)
        ):
            raise ValueError("메일 수집 워커에는 큐·서비스 주소·호출 계정·버킷이 필요합니다.")
        return self


class DispatchData(BaseModel):
    mailboxes: int
    created: int
    existing: int
    failed: int


class SyncRequest(BaseModel):
    mailbox_id: UUID


class SyncData(BaseModel):
    mailbox_id: UUID
    outcome: Literal["SUCCEEDED", "FAILED", "SKIPPED"]
    code: str | None
    messages_stored: int
    # 수집 뒤 회신 판정(KAN-75). 실패한 메일은 다음 실행에서 다시 판정한다.
    replies_linked: int = 0
    replies_review: int = 0
    replies_failed: int = 0


def dispatch_unavailable() -> ApplicationError:
    """원시 응답을 담지 않는 작업 등록 실패.

    Returns:
        ApplicationError: Cloud Tasks 토큰을 받지 못한 경우의 오류.
    """
    return ApplicationError(503, "MAIL_DISPATCH_UNAVAILABLE", "Mail sync dispatch is unavailable.")


def configure_mail_sync(app: FastAPI, value: MailSyncSettings, **overrides: Any) -> None:
    """워커 설정과 의존성을 앱에 둔다. 비활성이면 경로가 404로 닫힌다.

    Args:
        app: 플랫폼 앱. 사서함 토큰 제공자 구성을 먼저 마쳐야 한다.
        value: 워커 설정.
        **overrides: 검사용 주입(`caller`, `tasks_token`, `storage_token`, `transport`).

    Returns:
        None.
    """
    app.state.mail_sync = value
    app.state.mail_sync_caller = None
    if not value.enabled:
        return
    if app.state.mailbox_tokens is None:
        raise ValueError(
            "메일 수집 워커에는 사서함 토큰 제공자(MAILBOX_AUTH_ENABLED)가 필요합니다."
        )
    app.state.mail_sync_caller = overrides.get("caller") or PlatformCaller(
        value.service_url, value.invoker_email
    )
    app.state.mail_sync_tasks_token = overrides.get("tasks_token") or GoogleAccessToken(
        scope=CLOUD_PLATFORM, error=dispatch_unavailable
    )
    app.state.mail_sync_storage_token = overrides.get("storage_token") or GoogleAccessToken()
    # 검사에서만 바깥 호출을 가짜 응답으로 바꾼다. 운영은 기본 HTTP 전송을 쓴다.
    app.state.mail_sync_transport = overrides.get("transport")


async def verify_caller(request: Request) -> None:
    """비활성이면 404, 지정 호출 계정의 ID 토큰이 아니면 401로 닫는다.

    Args:
        request: 내부 호출 요청.

    Returns:
        None. 통과하지 못하면 HTTPException을 낸다.
    """
    caller = request.app.state.mail_sync_caller
    if caller is None:
        raise HTTPException(404)
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer "):
        raise HTTPException(401)
    # 서명키 조회가 동기 HTTP라 이벤트 루프를 막지 않도록 스레드에서 검증한다.
    await asyncio.to_thread(caller.verify, values[0].removeprefix("Bearer "))


def client(request: Request, timeout: httpx.Timeout) -> httpx.AsyncClient:
    """리디렉션을 따르지 않는 바깥 호출 클라이언트.

    Args:
        request: 앱 상태를 가진 요청.
        timeout: 제한 시간.

    Returns:
        httpx.AsyncClient: 새 클라이언트. 호출자가 닫는다.
    """
    return httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, transport=request.app.state.mail_sync_transport
    )


async def connected_mailboxes(request: Request) -> list[UUID]:
    """연결됐고 재연결 표시가 없는 사서함 ID를 읽는다. 토큰·소유자 열은 읽지 않는다.

    재연결이 필요한 사서함(KAN-81 `reconnect_required_at`)은 토큰 요청이 실패할 것이 정해져 있어
    작업을 만들지 않는다. 소유자가 다시 연결하면 다음 주기부터 다시 들어온다.

    Args:
        request: 앱 설정을 가진 요청.

    Returns:
        list[UUID]: 연결 상태 사서함 ID.
    """
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT id FROM mailbox_auth.connections "
            "WHERE state='connected' AND reconnect_required_at IS NULL ORDER BY id"
        )
        return [row["id"] for row in await cursor.fetchall()]


def task_body(value: MailSyncSettings, mailbox_id: UUID, slot: int) -> dict[str, Any]:
    """사서함 하나를 수집하는 Cloud Tasks HTTP 작업.

    Args:
        value: 워커 설정.
        mailbox_id: 사서함 연결 ID.
        slot: 등록 시간 구간 번호. 같은 구간의 같은 사서함은 같은 작업 이름이다.

    Returns:
        dict[str, Any]: Cloud Tasks `tasks.create` 요청 본문.
    """
    payload = json.dumps({"mailbox_id": str(mailbox_id)}).encode()
    return {
        "task": {
            "name": f"{value.queue}/tasks/mail-{mailbox_id}-{slot}",
            "httpRequest": {
                "url": f"{value.service_url}{PREFIX}/sync",
                "httpMethod": "POST",
                "headers": {"Content-Type": "application/json"},
                "body": base64.b64encode(payload).decode(),
                "oidcToken": {
                    "serviceAccountEmail": value.invoker_email,
                    "audience": value.service_url,
                },
            },
        }
    }


router = APIRouter(prefix=PREFIX, tags=["internal-mail"], include_in_schema=False)


@router.post("/dispatch", response_model=SuccessResponse[DispatchData])
async def dispatch(request: Request) -> JSONResponse:
    """연결된 사서함마다 이번 구간의 수집 작업을 등록한다.

    이미 같은 이름의 작업이 있으면(409) 등록된 것으로 센다. 하나라도 실패하면 503을 돌려
    Scheduler가 다시 부르게 한다. 다시 불려도 같은 구간 작업은 중복되지 않는다.

    Args:
        request: Scheduler 호출.

    Returns:
        JSONResponse: 사서함·등록·기존·실패 수. 실패가 있으면 503.
    """
    await verify_caller(request)
    value: MailSyncSettings = request.app.state.mail_sync
    mailboxes = await connected_mailboxes(request)
    slot = int(time.time()) // value.interval_seconds
    counts = {"created": 0, "existing": 0, "failed": 0}
    token = await request.app.state.mail_sync_tasks_token()
    async with client(request, httpx.Timeout(10)) as http:
        for mailbox_id in mailboxes:
            try:
                response = await http.post(
                    f"{TASKS_API}/{value.queue}/tasks",
                    json=task_body(value, mailbox_id, slot),
                    headers={"Authorization": f"Bearer {token}"},
                )
                status = response.status_code
            except httpx.HTTPError:
                status = 0
            key = "created" if status == 200 else "existing" if status == 409 else "failed"
            counts[key] += 1
    body = SuccessResponse[DispatchData](
        data=DispatchData(mailboxes=len(mailboxes), **counts),
        meta=ResponseMeta(request_id=request.state.request_id),
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=503 if counts["failed"] else 200)


@router.post("/sync", response_model=SuccessResponse[SyncData])
async def sync(request: Request) -> JSONResponse:
    """사서함 하나를 수집하고, 큐가 다시 시도할지를 응답 코드로 알린다.

    Args:
        request: Cloud Tasks 호출. 본문은 `{"mailbox_id": "<UUID>"}`.

    Returns:
        JSONResponse: 결과. 일시 실패는 503(큐 재시도), 나머지는 200.
    """
    # 본문은 호출자 확인 뒤에 읽는다. 비활성 404·인증 401이 본문 검증 422에 가려지지 않게 한다.
    await verify_caller(request)
    try:
        payload = SyncRequest.model_validate_json(await request.body())
    except ValidationError:
        raise ApplicationError(422, "VALIDATION_ERROR", "Request body is invalid.") from None
    value: MailSyncSettings = request.app.state.mail_sync
    async with (
        client(request, httpx.Timeout(30, connect=10)) as graph_client,
        client(request, httpx.Timeout(60, connect=10)) as storage_client,
    ):
        store = GcsObjectStore(
            storage_client, value.object_bucket, request.app.state.mail_sync_storage_token
        )
        judged = {"LINKED": 0, "REVIEW": 0, "failed": 0}
        try:
            result: SyncResult | None = await sync_mailbox(
                request.app.state.settings,
                payload.mailbox_id,
                get_token=request.app.state.mailbox_tokens.get_mailbox_access_token,
                client=graph_client,
                store=store,
            )
        except ApplicationError as error:
            # 실행을 만들기 전 거절. 진행 중·해제·없음은 다시 시도해도 같다.
            if error.code not in SKIPPED:
                raise
            result, code = None, error.code
        if result is not None:
            # 수집이 실패해도 앞서 기록된 페이지의 메일은 판정한다. 판정 실패는 수집 결과를
            # 바꾸지 않고 수만 알린다(표가 아직 없는 환경 포함).
            try:
                judged |= await judge_new_replies(
                    request.app.state.settings, payload.mailbox_id, store
                )
            except (psycopg.Error, ApplicationError, HTTPException):
                # 공용 DB 연결은 DB 오류를 HTTP 503 예외로 바꾼다. 그것도 수집 결과로 번지지 않게 한다.
                judged["failed"] += 1
    if result is None:
        data = SyncData(
            mailbox_id=payload.mailbox_id, outcome="SKIPPED", code=code, messages_stored=0
        )
        status = 200
    else:
        outcome: Literal["SUCCEEDED", "FAILED"] = (
            "SUCCEEDED" if result.status == "SUCCEEDED" else "FAILED"
        )
        data = SyncData(
            mailbox_id=payload.mailbox_id,
            outcome=outcome,
            code=result.error_code,
            messages_stored=result.messages_stored,
            replies_linked=judged["LINKED"],
            replies_review=judged["REVIEW"],
            replies_failed=judged["failed"],
        )
        status = 503 if result.error_code in TRANSIENT else 200
    body = SuccessResponse[SyncData](
        data=data, meta=ResponseMeta(request_id=request.state.request_id)
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status)
