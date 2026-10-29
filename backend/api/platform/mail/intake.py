"""받은 메일을 C/S 문의로 접수한다(KAN-110). 사람(cs_agent)이 확인한 뒤에만 한다.

AI 분류(KAN-73)는 추천일 뿐이고, 접수는 메일함 주인인 cs_agent가 고객·제품 코드를 확인해 요청한다.
플랫폼은 원문에서 제목·본문·보낸 사람을 꺼내 C/S 내부 경로(`from-mail`)로 넘기고, 만들어진 문의를
원 메일에 `ORIGIN`으로 연결해 이후 회신이 그 문의로 연결되게 한다.

기본 비활성: 사서함 연결(`MAILBOX_AUTH_ENABLED`)·업무 중계(C/S 주소·토큰)·원문 저장소가 모두
있어야 한다. 그렇지 않으면 404다. 웹 중계(frontend/server.mjs)는 아직 이 경로를 넘기지 않는다
(플랫폼 자체 경로에 JSON 본문을 넘기는 유형이 없음, KAN-132에서 승인자 API와 함께).
"""

from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.platform.auth.context import load_session_principal
from backend.api.platform.auth.http import SESSION_COOKIE, operational_identity
from backend.api.platform.auth.mailbox_http import mailbox_provider, mutation_token
from backend.api.platform.auth.microsoft import PersonalMicrosoftOAuth
from backend.api.platform.mail.business import FROM_MAIL_PATH, cs_caller
from backend.api.platform.mail.links import link_origin, not_found
from backend.api.platform.mail.storage import GcsObjectStore, StoredObject
from backend.engine.mail_triage.extract import mail_text
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import CUSTOMER_ID, ErrorResponse, ResponseMeta, SuccessResponse

ACTIVATION = {"x-implementation-status": "partial", "x-activation": "MAILBOX_AUTH_ENABLED"}
router = APIRouter(
    prefix="/api/v1/platform/mail/messages",
    tags=["mail-intake"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
Code = Annotated[str, Field(min_length=1, max_length=64)]


class RegisterTicket(BaseModel):
    """접수 담당자가 메일을 읽고 확인한 값. 제목·본문·보낸 사람은 원문에서 가져온다."""

    model_config = ConfigDict(extra="forbid")

    customer_external_id: Annotated[str, Field(pattern=f"^{CUSTOMER_ID}$")]
    product_code: Code
    body_language: Annotated[str, Field(min_length=2, max_length=16)]
    fw_version_reported: Code | None = None
    error_code_reported: Code | None = None


class RegisteredTicket(BaseModel):
    message_id: UUID
    ticket_id: UUID
    # False면 이 메일은 이미 접수돼 있던 문의에 다시 연결만 했다.
    created: bool


async def read_raw(request: Request, row: dict[str, Any]) -> bytes:
    """원문을 객체 저장소에서 읽는다. 검사 주입(`mail_intake_store`)이 없으면 수집 버킷을 쓴다."""
    store = getattr(request.app.state, "mail_intake_store", None)
    stored = StoredObject(row["raw_storage_key"], row["raw_sha256"], row["raw_size"])
    if store is not None:
        return await store.get(stored)
    sync = request.app.state.mail_sync
    if not sync.enabled:
        raise HTTPException(404)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60, connect=10),
        follow_redirects=False,
        transport=request.app.state.mail_sync_transport,
    ) as http:
        gcs = GcsObjectStore(http, sync.object_bucket, request.app.state.mail_sync_storage_token)
        return await gcs.get(stored)


@router.post(
    "/{message_id}/register-ticket",
    response_model=SuccessResponse[RegisteredTicket],
    status_code=201,
    operation_id="platform_register_mail_ticket",
    openapi_extra=ACTIVATION,
)
async def register_ticket(
    message_id: UUID,
    payload: RegisterTicket,
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[RegisteredTicket]:
    """메일함 주인인 cs_agent가 받은 메일 한 통을 C/S 문의로 접수한다.

    다른 사람 메일함·보낸편지함·지워진 메일은 404, 이미 이 메일로 접수한 문의가 있으면 그 문의에
    다시 연결하고 200을 돌려준다(두 번 접수하지 않음). 업무 중계가 꺼져 있으면 404다.
    """
    mutation_token(request)
    user = await operational_identity(request, response)
    caller = cs_caller(request)
    if caller is None:
        raise HTTPException(404)
    principal = await load_session_principal(
        request.app.state.settings, request.cookies.get(SESSION_COOKIE)
    )
    if not principal.has("cs_agent"):
        raise HTTPException(403)
    settings = request.app.state.settings
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT m.id, m.mailbox_id, m.graph_id, m.from_address, m.received_at, "
            "m.raw_storage_key, m.raw_sha256, m.raw_size FROM integration.mail_messages m "
            "JOIN mailbox_auth.connections c ON c.id=m.mailbox_id "
            "WHERE m.id=%s AND c.owner_user_id=%s AND m.folder='inbox' AND m.removed_at IS NULL",
            (message_id, user.id),
        )
        row = await cursor.fetchone()
    if row is None:
        raise not_found()
    raw = await read_raw(request, row)
    try:
        subject, body, parsed_sender = mail_text(raw)
    except ValueError:
        raise ApplicationError(422, "MAIL_INVALID_INPUT", "The raw message is invalid.") from None
    sender = row["from_address"] or parsed_sender
    if not sender:
        raise ApplicationError(422, "MAIL_SENDER_UNKNOWN", "The mail has no sender address.")
    if not body:
        raise ApplicationError(422, "MAIL_BODY_EMPTY", "The mail has no text body.")
    outgoing = {
        "message_id": str(message_id),
        "mailbox_id": str(row["mailbox_id"]),
        "external_id": row["graph_id"],
        "sender_address": sender,
        "received_at": row["received_at"].isoformat(),
        "subject": subject[:500] if subject else None,
        "body": body[:50_000],
        **payload.model_dump(),
    }
    try:
        reply = await caller.post(
            FROM_MAIL_PATH, outgoing, principal=principal, request_id=request.state.request_id
        )
        data = reply.json()
    except (httpx.HTTPError, ValueError):
        raise ApplicationError(503, "CS_UNAVAILABLE", "The C/S service did not answer.") from None
    if reply.status_code == 201:
        ticket_id, created = UUID(str(data["data"]["id"])), True
    elif (
        reply.status_code == 409
        and data.get("error", {}).get("code") == "CS_MAIL_ALREADY_REGISTERED"
    ):
        details = {d.get("field"): d.get("reason") for d in data["error"].get("details", [])}
        ticket_id, created = UUID(str(details["ticket_id"])), False
    elif reply.status_code in (401, 403):
        raise HTTPException(403)
    elif reply.status_code == 422:
        raise ApplicationError(422, "VALIDATION_ERROR", "The C/S service refused the ticket.")
    else:
        raise ApplicationError(503, "CS_UNAVAILABLE", "The C/S service did not accept the ticket.")
    # 이 메일의 스레드·헤더로 오는 회신이 이 문의로 연결된다(KAN-75). 같은 연결은 한 번만.
    await link_origin(
        settings, message_id=message_id, owner_service="cs", business_ref=f"cs_ticket:{ticket_id}"
    )
    response.status_code = 201 if created else 200
    return SuccessResponse(
        data=RegisteredTicket(message_id=message_id, ticket_id=ticket_id, created=created),
        meta=ResponseMeta(request_id=request.state.request_id),
    )
