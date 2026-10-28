"""승인 메일 발송 입구와 실행(KAN-74). 업무 서비스가 요청하고 Cloud Tasks가 보낸다.

| 경로 (`/internal/v1/platform/mail/` 아래) | 호출자 | 하는 일 |
| --- | --- | --- |
| `POST send-intents` | 업무 서비스(C/S 실행 계정) | 승인된 발송 의도를 만들고 전송 작업을 등록 |
| `GET send-intents/{id}` | 같은 업무 서비스 | 상태·이력(본문·수신자 제외) |
| `POST send` | Cloud Tasks | 의도 하나를 한 번 보낸다. 접수되면 사본 확인 작업을 등록 |
| `POST send-confirm` | Cloud Tasks | 보낸편지함 사본 대조 |
| `POST send-sweep` | Cloud Scheduler | 멈춘 전송 정리, 등록이 빠진 대기 의도 재등록, 결과 불명 의도 대조 등록 |

모두 기본 비활성(404)이며 OpenAPI에 넣지 않는다. 호출자는 Authorization의 Google ID 토큰을
고정 Google 서명키·`MAIL_SEND_SERVICE_URL` audience·지정 계정으로 검증한다. 업무 서비스는
본문에서 자기 서비스를 고를 수 없고, 검증된 호출 계정이 `owner_service`를 정한다.
"""

import asyncio
import base64
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self
from uuid import UUID

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import JSONResponse

from backend.api.platform.mail import send
from backend.api.platform.mail.send import Attachment, SendRequest
from backend.api.platform.mail.storage import (
    BUCKET_NAME,
    GcsObjectStore,
    GoogleAccessToken,
    StoredObject,
)
from backend.api.platform.mail.worker import CLOUD_PLATFORM, QUEUE, SERVICE_ACCOUNT, TASKS_API
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ResponseMeta, SuccessResponse
from backend.shared.service_auth import PlatformCaller

PREFIX = "/internal/v1/platform/mail"
CONFIRM_DELAY = timedelta(seconds=60)
STALE_QUEUED = timedelta(minutes=2)
STUCK_SUBMITTING = timedelta(minutes=15)


class MailSendSettings(BaseSettings):
    """운영자가 큐·호출 계정·버킷을 준비한 뒤 별도로 켤 메일 발송 설정."""

    model_config = SettingsConfigDict(
        env_prefix="MAIL_SEND_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )
    enabled: bool = False
    queue: str = ""
    service_url: str = ""
    # Scheduler·Tasks가 ID 토큰을 받는 호출 전용 계정(메일 수집과 같은 계정을 써도 된다).
    invoker_email: str = ""
    # 발송을 요청할 수 있는 C/S 실행 계정.
    cs_caller_email: str = ""
    object_bucket: str = Field(default="", validation_alias="MAIL_OBJECT_BUCKET")

    @model_validator(mode="after")
    def validate_enabled(self) -> Self:
        """켰을 때만 큐·주소·계정·버킷 형식을 확인한다.

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
            or not SERVICE_ACCOUNT.fullmatch(self.cs_caller_email)
            or self.invoker_email == self.cs_caller_email
            or not BUCKET_NAME.fullmatch(self.object_bucket)
        ):
            raise ValueError("메일 발송에는 큐·서비스 주소·호출 계정·C/S 계정·버킷이 필요합니다.")
        return self


class AttachmentIn(BaseModel):
    name: str
    content_type: str
    storage_key: str
    sha256: str
    size: int


class SendIntentIn(BaseModel):
    """업무 서비스가 승인 뒤 보내는 발송 요청. 승인 해시는 업무가 계산한다."""

    mailbox_id: UUID
    business_ref: str
    business_version: int
    approved_by: UUID
    approved_at: datetime
    subject: str
    body_type: Literal["text", "html"]
    body: str
    to: list[str]
    cc: list[str] = Field(default_factory=list)
    attachments: list[AttachmentIn] = Field(default_factory=list)
    approved_payload_sha256: str


class IntentRef(BaseModel):
    intent_id: UUID


class IntentAccepted(BaseModel):
    intent_id: UUID
    status: str
    task_registered: bool


class IntentEvent(BaseModel):
    event: str
    at: datetime
    code: str | None


class IntentStatus(BaseModel):
    intent_id: UUID
    business_ref: str
    business_version: int
    status: str
    error_code: str | None
    attempts: int
    created_at: datetime
    submitted_at: datetime | None
    accepted_at: datetime | None
    sent_copy_confirmed_at: datetime | None
    events: list[IntentEvent]


class WorkOutcome(BaseModel):
    intent_id: UUID
    status: str
    code: str | None


class SweepData(BaseModel):
    interrupted: int
    queued: int
    confirms: int
    failed: int


def configure_mail_send(app: FastAPI, value: MailSendSettings, **overrides: Any) -> None:
    """발송 설정과 의존성을 앱에 둔다. 비활성이면 경로가 404로 닫힌다.

    Args:
        app: 플랫폼 앱. 사서함 토큰 제공자 구성을 먼저 마쳐야 한다.
        value: 발송 설정.
        **overrides: 검사용 주입(`invoker`, `cs_caller`, `tasks_token`, `storage_token`,
            `transport`).

    Returns:
        None.
    """
    app.state.mail_send = value
    app.state.mail_send_invoker = None
    app.state.mail_send_cs_caller = None
    if not value.enabled:
        return
    if app.state.mailbox_tokens is None:
        raise ValueError("메일 발송에는 사서함 토큰 제공자(MAILBOX_AUTH_ENABLED)가 필요합니다.")
    app.state.mail_send_invoker = overrides.get("invoker") or PlatformCaller(
        value.service_url, value.invoker_email
    )
    app.state.mail_send_cs_caller = overrides.get("cs_caller") or PlatformCaller(
        value.service_url, value.cs_caller_email
    )
    app.state.mail_send_tasks_token = overrides.get("tasks_token") or GoogleAccessToken(
        scope=CLOUD_PLATFORM, error=dispatch_unavailable
    )
    app.state.mail_send_storage_token = overrides.get("storage_token") or GoogleAccessToken()
    app.state.mail_send_transport = overrides.get("transport")


def dispatch_unavailable() -> ApplicationError:
    return ApplicationError(503, "MAIL_DISPATCH_UNAVAILABLE", "Mail send dispatch is unavailable.")


async def verify(request: Request, role: Literal["invoker", "cs"]) -> None:
    """비활성이면 404, 지정 계정의 ID 토큰이 아니면 401로 닫는다.

    Args:
        request: 내부 호출.
        role: `invoker`(Scheduler·Tasks) 또는 `cs`(C/S 실행 계정).

    Returns:
        None. 통과하지 못하면 HTTPException을 낸다.
    """
    caller = getattr(
        request.app.state, "mail_send_invoker" if role == "invoker" else "mail_send_cs_caller"
    )
    if caller is None:
        raise HTTPException(404)
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer "):
        raise HTTPException(401)
    await asyncio.to_thread(caller.verify, values[0].removeprefix("Bearer "))


async def body_of(request: Request, model: type[Any]) -> Any:
    """호출자 확인 뒤 본문을 읽는다. 잘못된 본문은 422다."""
    try:
        return model.model_validate_json(await request.body())
    except ValidationError:
        raise ApplicationError(422, "VALIDATION_ERROR", "Request body is invalid.") from None


def client(request: Request, timeout: httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, transport=request.app.state.mail_send_transport
    )


async def enqueue(
    request: Request,
    http: httpx.AsyncClient,
    path: str,
    name: str,
    intent_id: UUID,
    delay: timedelta | None = None,
) -> bool:
    """Cloud Tasks에 작업 하나를 등록한다. 같은 이름이 이미 있으면 등록된 것으로 본다.

    Args:
        request: 앱 상태를 가진 요청.
        http: 바깥 호출 클라이언트.
        path: 대상 내부 경로(`send` 또는 `send-confirm`).
        name: 작업 이름 끝부분. 같은 일은 같은 이름이라 두 번 생기지 않는다.
        intent_id: 발송 의도 ID.
        delay: 늦춰 실행할 시간.

    Returns:
        bool: 등록됐거나 이미 있으면 True.
    """
    value: MailSendSettings = request.app.state.mail_send
    task: dict[str, Any] = {
        "name": f"{value.queue}/tasks/{name}",
        "httpRequest": {
            "url": f"{value.service_url}{PREFIX}/{path}",
            "httpMethod": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(json.dumps({"intent_id": str(intent_id)}).encode()).decode(),
            "oidcToken": {
                "serviceAccountEmail": value.invoker_email,
                "audience": value.service_url,
            },
        },
    }
    if delay is not None:
        task["scheduleTime"] = (datetime.now(UTC) + delay).isoformat().replace("+00:00", "Z")
    token = await request.app.state.mail_send_tasks_token()
    try:
        response = await http.post(
            f"{TASKS_API}/{value.queue}/tasks",
            json={"task": task},
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError:
        return False
    return response.status_code in (200, 409)


def send_task(intent_id: UUID, attempts: int) -> str:
    """전송 작업 이름. 시도 수가 같으면 같은 이름이라 같은 시도를 두 번 등록하지 않는다."""
    return f"mailsend-{intent_id}-a{attempts}"


def ok(request: Request, data: BaseModel, status: int = 200) -> JSONResponse:
    body = SuccessResponse[Any](data=data, meta=ResponseMeta(request_id=request.state.request_id))
    return JSONResponse(body.model_dump(mode="json"), status_code=status)


router = APIRouter(prefix=PREFIX, tags=["internal-mail-send"], include_in_schema=False)


@router.post("/send-intents")
async def create(request: Request) -> JSONResponse:
    """C/S가 승인한 발송 의도를 만들고 전송 작업을 등록한다.

    등록이 실패해도 의도는 `QUEUED`로 남고 정리 작업이 다시 등록한다.

    Args:
        request: C/S 실행 계정의 호출.

    Returns:
        JSONResponse: 202와 의도 ID·상태·작업 등록 여부.
    """
    await verify(request, "cs")
    payload: SendIntentIn = await body_of(request, SendIntentIn)
    if not re.fullmatch(r"[0-9a-f]{64}", payload.approved_payload_sha256):
        raise ApplicationError(422, "VALIDATION_ERROR", "Request body is invalid.")
    intent_id = await send.create_intent(
        request.app.state.settings,
        SendRequest(
            mailbox_id=payload.mailbox_id,
            owner_service="cs",
            business_ref=payload.business_ref,
            business_version=payload.business_version,
            approved_by=payload.approved_by,
            approved_at=payload.approved_at,
            subject=payload.subject,
            body_type=payload.body_type,
            body=payload.body,
            to=tuple(payload.to),
            cc=tuple(payload.cc),
            attachments=tuple(
                Attachment(a.name, a.content_type, StoredObject(a.storage_key, a.sha256, a.size))
                for a in payload.attachments
            ),
            approved_payload_sha256=payload.approved_payload_sha256,
        ),
    )
    row = await send.read_intent(request.app.state.settings, intent_id, "cs")
    registered = False
    if row["status"] == "QUEUED":
        async with client(request, httpx.Timeout(10)) as http:
            registered = await enqueue(
                request, http, "send", send_task(intent_id, row["attempts"]), intent_id
            )
    return ok(
        request,
        IntentAccepted(intent_id=intent_id, status=row["status"], task_registered=registered),
        202,
    )


@router.get("/send-intents/{intent_id}")
async def status(request: Request, intent_id: UUID) -> JSONResponse:
    """자기 서비스의 발송 의도 상태와 이력을 돌려준다. 다른 서비스 것은 404다.

    Args:
        request: C/S 실행 계정의 호출.
        intent_id: 발송 의도 ID.

    Returns:
        JSONResponse: 상태·코드·시도·시각·이력.
    """
    await verify(request, "cs")
    row = await send.read_intent(request.app.state.settings, intent_id, "cs")
    row["intent_id"] = row.pop("id")
    return ok(request, IntentStatus(**row))


@router.post("/send")
async def run_send(request: Request) -> JSONResponse:
    """의도 하나를 한 번 보낸다. 큐가 다시 시도할지는 응답 코드로 알린다.

    Args:
        request: Cloud Tasks 호출. 본문은 `{"intent_id"}`.

    Returns:
        JSONResponse: 미뤄졌으면 503(큐 재시도), 그 밖에는 200.
    """
    await verify(request, "invoker")
    ref: IntentRef = await body_of(request, IntentRef)
    value: MailSendSettings = request.app.state.mail_send
    settings = request.app.state.settings
    async with (
        client(request, httpx.Timeout(30, connect=10)) as graph_client,
        client(request, httpx.Timeout(60, connect=10)) as storage_client,
    ):
        try:
            result = await send.submit_intent(
                settings,
                ref.intent_id,
                get_token=request.app.state.mailbox_tokens.get_mailbox_access_token,
                client=graph_client,
                store=GcsObjectStore(
                    storage_client, value.object_bucket, request.app.state.mail_send_storage_token
                ),
            )
        except ApplicationError as error:
            if error.code == "MAILBOX_NOT_CONNECTED":
                # 연결이 끊긴 사서함으로는 보낼 수 없다. 대기에 남겨 헛돌게 하지 않는다.
                await send.fail_queued(settings, ref.intent_id, error.code)
            elif error.code not in ("MAIL_SEND_NOT_QUEUED", "MAIL_SEND_NOT_FOUND"):
                raise
            # 이미 처리됐거나 없는 의도의 중복 전달이다. 다시 시도해도 같다.
            return ok(
                request, WorkOutcome(intent_id=ref.intent_id, status="SKIPPED", code=error.code)
            )
        if result.status == "ACCEPTED":
            await enqueue(
                request,
                graph_client,
                "send-confirm",
                f"mailconfirm-{ref.intent_id}-s{await attempts_of(request, ref.intent_id)}",
                ref.intent_id,
                CONFIRM_DELAY,
            )
    outcome = WorkOutcome(intent_id=ref.intent_id, status=result.status, code=result.error_code)
    return ok(request, outcome, 503 if result.status == "QUEUED" else 200)


async def attempts_of(request: Request, intent_id: UUID) -> int:
    """현재 시도 수. 사본 확인 작업 이름을 시도마다 다르게 한다."""
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT attempts FROM integration.mail_send_intents WHERE id=%s", (intent_id,)
        )
        row = await cursor.fetchone()
    return int(row["attempts"]) if row else 0


@router.post("/send-confirm")
async def run_confirm(request: Request) -> JSONResponse:
    """보낸편지함 사본을 대조한다. 접수됐는데 아직 안 보이면 503으로 다시 확인한다.

    Args:
        request: Cloud Tasks 호출. 본문은 `{"intent_id"}`.

    Returns:
        JSONResponse: 사본 확인 결과. 결과 불명은 기록 뒤 200(사람이 판단).
    """
    await verify(request, "invoker")
    ref: IntentRef = await body_of(request, IntentRef)
    async with client(request, httpx.Timeout(30, connect=10)) as graph_client:
        try:
            result = await send.confirm_sent_copy(
                request.app.state.settings,
                ref.intent_id,
                get_token=request.app.state.mailbox_tokens.get_mailbox_access_token,
                client=graph_client,
            )
        except ApplicationError as error:
            if error.code in ("MAIL_SEND_NOT_SENT", "MAIL_SEND_NOT_FOUND"):
                return ok(
                    request, WorkOutcome(intent_id=ref.intent_id, status="SKIPPED", code=error.code)
                )
            raise
    code = None if result.sent_copy_confirmed else "MAIL_SENT_COPY_NOT_FOUND"
    outcome = WorkOutcome(intent_id=ref.intent_id, status=result.status, code=code)
    retry = result.status == "ACCEPTED" and not result.sent_copy_confirmed
    return ok(request, outcome, 503 if retry else 200)


@router.post("/send-sweep")
async def sweep(request: Request) -> JSONResponse:
    """멈춘 전송을 `UNKNOWN`으로, 등록이 빠진 대기 의도를 재등록, 결과 불명은 대조를 등록한다.

    Args:
        request: Cloud Scheduler 호출.

    Returns:
        JSONResponse: 처리 수. 등록 실패가 있으면 503(Scheduler 재시도).
    """
    await verify(request, "invoker")
    settings = request.app.state.settings
    interrupted = await send.expire_submitting(settings, STUCK_SUBMITTING)
    work = await send.pending_work(settings, stale_after=STALE_QUEUED)
    failed = 0
    async with client(request, httpx.Timeout(10)) as http:
        for intent_id, attempts in work["queued"]:
            failed += not await enqueue(
                request, http, "send", send_task(intent_id, attempts), intent_id
            )
        for intent_id, attempts in work["unknown"]:
            failed += not await enqueue(
                request, http, "send-confirm", f"mailconfirm-{intent_id}-u{attempts}", intent_id
            )
    data = SweepData(
        interrupted=interrupted,
        queued=len(work["queued"]),
        confirms=len(work["unknown"]),
        failed=failed,
    )
    return ok(request, data, 503 if failed else 200)
