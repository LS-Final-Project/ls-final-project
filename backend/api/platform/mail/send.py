"""승인된 메일 발송(KAN-74). 발송 의도 하나에 외부 전송을 한 번 시도한다.

1. `create_intent`: 업무가 승인한 본문·수신자·첨부·판본을 저장한다. 승인 시점 payload 해시와
   서버가 다시 계산한 해시가 같아야 한다. 같은 업무 판본은 한 번만 만든다.
2. `submit_intent`: `QUEUED`를 `SUBMITTING`으로 잠그고 커밋한 뒤, DB 트랜잭션 밖에서 저장된
   내용과 해시·첨부 바이트를 다시 확인해 Graph `sendMail`을 한 번 부른다.
   202는 `ACCEPTED`(접수)다. 배달 완료가 아니다. 응답 전에 끊기면 `UNKNOWN`이고 자동으로 다시
   보내지 않는다. 명확한 거절은 `FAILED`, 사용량 제한(429)은 보내지 않은 것이라 `QUEUED`로 되돌린다.
3. `confirm_sent_copy`: 메일에 붙인 발송 의도 ID(숨은 확장 속성)로 보낸편지함 사본을 찾는다.
   찾으면 사본 확인 시각을 남기고, `UNKNOWN`이면 실제로 나간 것으로 `ACCEPTED`가 된다.
4. `authorize_resend`: `UNKNOWN`에서 보낸편지함에 사본이 없다는 확인 뒤에만, 승인자가 사유를
   남기고 `QUEUED`로 되돌린다. 시도는 최대 3회다.
"""

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from backend.api.platform.mail.graph import GRAPH_BASE
from backend.api.platform.mail.storage import ObjectReader, StoredObject, content_key
from backend.api.platform.mail.sync import Conn, MailboxTokenGetter, checked_token
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

# TFT는 개발 중단(2026-10-28)이라 발송 주체에서 뺐다.
OwnerService = Literal["cs", "platform"]
# 보낸편지함에서 발송 의도를 찾는 숨은 확장 속성(이 서비스 전용 고정 GUID).
INTENT_PROPERTY = "String {6f1c2a9e-3b7d-4e58-9a41-74c0d2e8b5a3} Name Team4SendIntentId"
SEND_URL = f"{GRAPH_BASE}/me/sendMail"
SENT_ITEMS_URL = f"{GRAPH_BASE}/me/mailFolders/sentitems/messages"
MAX_ATTEMPTS = 3
MAX_RECIPIENTS = 50
# sendMail 한 요청은 약 4MB까지다. 첨부 원본 합계와 (본문 + base64 첨부 + 여유) 전체를 함께 제한한다.
MAX_ATTACHMENT_BYTES = 2_800_000
MAX_REQUEST_BYTES = 3_900_000
REQUEST_OVERHEAD = 64 * 1024
# 보낸편지함 반영이 늦을 수 있어, 전송 시작 뒤 이 시간이 지나야 "사본 없음"을 재발송 근거로 쓴다.
RESEND_GRACE = timedelta(minutes=10)
# 마이그레이션의 business_ref CHECK와 같은 형식.
BUSINESS_REF = re.compile(r"[a-z][a-z_]{1,31}:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
# 보내기 전에 난 일시 장애. 메일은 나가지 않았으므로 같은 의도를 나중에 다시 보낼 수 있다.
PRE_SEND_TRANSIENT = {"MAILBOX_TOKEN_UNAVAILABLE", "MAIL_STORAGE_UNAVAILABLE"}
# Graph가 요청을 거절했다고 확실히 말한 응답. 메일은 나가지 않았다.
REJECTED = {
    400: "GRAPH_BAD_REQUEST",
    403: "GRAPH_FORBIDDEN",
    404: "GRAPH_NOT_FOUND",
    413: "MAIL_TOO_LARGE",
    422: "GRAPH_BAD_REQUEST",
}


@dataclass(frozen=True)
class Attachment:
    """발송할 첨부. 바이트는 저장소에 있고 위치·해시·크기만 받는다."""

    name: str
    content_type: str
    stored: StoredObject


@dataclass(frozen=True)
class SendRequest:
    """업무가 승인한 발송 내용.

    `approved_payload_sha256`은 업무가 승인 기록에 묶은 해시다. 서버가 같은 규칙으로 다시
    계산한 값과 다르면 승인 뒤 내용이 바뀐 것으로 보고 거부한다.
    """

    mailbox_id: UUID
    owner_service: OwnerService
    business_ref: str
    business_version: int
    approved_by: UUID
    approved_at: datetime
    subject: str
    body_type: Literal["text", "html"]
    body: str
    to: tuple[str, ...]
    cc: tuple[str, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    approved_payload_sha256: str = ""


@dataclass
class SendResult:
    intent_id: UUID
    status: str
    error_code: str | None = None
    sent_copy_confirmed: bool = False
    events: list[str] = field(default_factory=list)


def payload_sha256(
    *,
    subject: str,
    body_type: str,
    body: str,
    to: list[str] | tuple[str, ...],
    cc: list[str] | tuple[str, ...],
    attachments: list[dict[str, Any]],
) -> str:
    """승인 대상 내용의 정규화 해시. 업무와 플랫폼이 같은 규칙으로 계산한다.

    Args:
        subject: 제목.
        body_type: `text` 또는 `html`.
        body: 본문.
        to: 받는 사람 주소(입력 순서 유지).
        cc: 참조 주소.
        attachments: 첨부(이름·형식·SHA-256·크기). 저장 키는 넣지 않는다.

    Returns:
        str: 소문자 16진수 SHA-256.
    """
    canonical = {
        "subject": subject,
        "body_type": body_type,
        "body": body,
        "to": list(to),
        "cc": list(cc),
        "attachments": [
            {k: a[k] for k in ("name", "content_type", "sha256", "size")} for a in attachments
        ],
    }
    text = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def invalid(code: str, message: str) -> ApplicationError:
    return ApplicationError(422, code, message)


def attachment_rows(attachments: tuple[Attachment, ...]) -> list[dict[str, Any]]:
    """첨부를 저장용 JSON으로 바꾼다.

    Args:
        attachments: 첨부 목록.

    Returns:
        list[dict[str, Any]]: 이름·형식·저장 키·해시·크기.
    """
    return [
        {
            "name": a.name,
            "content_type": a.content_type,
            "storage_key": a.stored.key,
            "sha256": a.stored.sha256,
            "size": a.stored.size,
        }
        for a in attachments
    ]


def validate(request: SendRequest) -> tuple[list[dict[str, Any]], str]:
    """발송 내용 형식을 확인하고 저장할 첨부 JSON과 payload 해시를 만든다.

    Args:
        request: 승인된 발송 내용.

    Returns:
        tuple[list[dict[str, Any]], str]: 첨부 JSON과 다시 계산한 payload 해시.
    """
    if (
        request.owner_service not in ("cs", "platform")
        or not BUSINESS_REF.fullmatch(request.business_ref)
        or request.business_version < 1
    ):
        raise invalid("MAIL_BUSINESS_REF_INVALID", "Business reference is invalid.")
    if request.body_type not in ("text", "html"):
        raise invalid("MAIL_CONTENT_INVALID", "Mail content is invalid.")
    addresses = [*request.to, *request.cc]
    if not request.to or len(addresses) > MAX_RECIPIENTS:
        raise invalid("MAIL_RECIPIENTS_INVALID", "Recipients are invalid.")
    if any(not EMAIL.fullmatch(a) or len(a) > 254 for a in addresses):
        raise invalid("MAIL_RECIPIENTS_INVALID", "Recipients are invalid.")
    if len({a.lower() for a in addresses}) != len(addresses):
        raise invalid("MAIL_RECIPIENTS_INVALID", "Recipients are invalid.")
    if not 1 <= len(request.subject) <= 255 or "\n" in request.subject or "\r" in request.subject:
        raise invalid("MAIL_CONTENT_INVALID", "Mail content is invalid.")
    if not 1 <= len(request.body) <= 1_000_000:
        raise invalid("MAIL_CONTENT_INVALID", "Mail content is invalid.")
    for a in request.attachments:
        if (
            not 1 <= len(a.name) <= 255
            or any(c in a.name for c in "/\\\r\n")
            or not re.fullmatch(r"[a-z0-9.+-]+/[A-Za-z0-9.+-]+", a.content_type)
            or not re.fullmatch(r"[0-9a-f]{64}", a.stored.sha256)
            or a.stored.size < 0
        ):
            raise invalid("MAIL_ATTACHMENT_INVALID", "Attachment is invalid.")
    for a in request.attachments:
        # 첨부는 발신 사서함 이름 공간의 내용 키여야 한다. 다른 사서함 파일을 붙이지 못하게 한다.
        if a.stored.key != content_key(request.mailbox_id, a.stored.sha256):
            raise invalid("MAIL_ATTACHMENT_INVALID", "Attachment is invalid.")
    if sum(a.stored.size for a in request.attachments) > MAX_ATTACHMENT_BYTES:
        raise invalid("MAIL_ATTACHMENT_TOO_LARGE", "Attachments are too large.")
    # 승인 단계에서 전송 요청 전체 크기를 어림해, 보낼 때 Graph 한도로 실패하지 않게 한다.
    encoded = sum((a.stored.size + 2) // 3 * 4 for a in request.attachments)
    # 제목·본문·수신자는 모든 글자가 이스케이프되는 최악의 JSON 길이로 센다.
    text = json.dumps(
        [
            request.subject,
            request.body,
            *request.to,
            *request.cc,
            *(a.name for a in request.attachments),
        ],
        ensure_ascii=True,
    )
    if len(text) + encoded + REQUEST_OVERHEAD > MAX_REQUEST_BYTES:
        raise invalid("MAIL_TOO_LARGE", "Mail is too large.")
    rows = attachment_rows(request.attachments)
    digest = payload_sha256(
        subject=request.subject,
        body_type=request.body_type,
        body=request.body,
        to=request.to,
        cc=request.cc,
        attachments=rows,
    )
    if request.approved_payload_sha256 != digest:
        # 승인 기록의 해시와 다르면 승인 뒤 내용이 바뀐 것이다.
        raise ApplicationError(409, "MAIL_APPROVAL_MISMATCH", "Mail differs from the approval.")
    return rows, digest


async def add_event(
    conn: Conn,
    intent_id: UUID,
    event: str,
    *,
    actor: UUID | None = None,
    code: str | None = None,
    note: str | None = None,
) -> None:
    """감사 이력 한 줄을 남긴다. 호출자의 트랜잭션 안에서 쓴다."""
    await conn.execute(
        "INSERT INTO integration.mail_send_events (id, intent_id, event, actor_user_id, code, note) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (uuid4(), intent_id, event, actor, code, note),
    )


async def create_intent(settings: Settings, request: SendRequest) -> UUID:
    """승인된 발송 의도를 만든다. 같은 업무 판본을 다시 보내면 기존 의도를 돌려준다.

    발신 사서함은 연결 상태여야 하고 승인자 본인의 연결이어야 한다.

    Args:
        settings: DB 설정.
        request: 승인된 발송 내용.

    Returns:
        UUID: 발송 의도 ID. 같은 판본이 다른 내용이면 409 `MAIL_SEND_CONFLICT`.
    """
    rows, digest = validate(request)
    async with connection(settings) as conn, conn.transaction():
        cursor = await conn.execute(
            "SELECT owner_user_id, state FROM mailbox_auth.connections WHERE id=%s FOR SHARE",
            (request.mailbox_id,),
        )
        mailbox = await cursor.fetchone()
        if mailbox is None or mailbox["owner_user_id"] != request.approved_by:
            raise ApplicationError(403, "MAILBOX_ACCESS_DENIED", "Mailbox access is not allowed.")
        if mailbox["state"] != "connected":
            raise ApplicationError(409, "MAILBOX_NOT_CONNECTED", "Mailbox is not connected.")
        intent_id = uuid4()
        cursor = await conn.execute(
            "INSERT INTO integration.mail_send_intents (id, mailbox_id, owner_service, "
            "business_ref, business_version, approved_by, approved_at, subject, body_type, body, "
            "recipients, attachments, payload_sha256) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (owner_service, business_ref, business_version) DO NOTHING RETURNING id",
            (
                intent_id,
                request.mailbox_id,
                request.owner_service,
                request.business_ref,
                request.business_version,
                request.approved_by,
                request.approved_at,
                request.subject,
                request.body_type,
                request.body,
                Jsonb({"to": list(request.to), "cc": list(request.cc)}),
                Jsonb(rows),
                digest,
            ),
        )
        if await cursor.fetchone() is None:
            cursor = await conn.execute(
                "SELECT id, payload_sha256, mailbox_id FROM integration.mail_send_intents "
                "WHERE owner_service=%s AND business_ref=%s AND business_version=%s",
                (request.owner_service, request.business_ref, request.business_version),
            )
            existing = await cursor.fetchone()
            assert existing is not None
            if existing["payload_sha256"] != digest or existing["mailbox_id"] != request.mailbox_id:
                raise ApplicationError(
                    409, "MAIL_SEND_CONFLICT", "This approval was already used for other mail."
                )
            return UUID(str(existing["id"]))
        await add_event(conn, intent_id, "QUEUED", actor=request.approved_by)
        return intent_id


async def claim(settings: Settings, intent_id: UUID) -> dict[str, Any]:
    """`QUEUED`를 `SUBMITTING`으로 바꾸고 커밋한다. 커밋을 확인한 호출자만 보낸다.

    Args:
        settings: DB 설정.
        intent_id: 발송 의도 ID.

    Returns:
        dict[str, Any]: 잠근 발송 의도 행. 보낼 수 없는 상태면 409.
    """
    async with connection(settings) as conn, conn.transaction():
        cursor = await conn.execute(
            "SELECT i.*, c.state AS mailbox_state, c.owner_user_id "
            "FROM integration.mail_send_intents i "
            "JOIN mailbox_auth.connections c ON c.id=i.mailbox_id "
            "WHERE i.id=%s FOR UPDATE OF i",
            (intent_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ApplicationError(404, "MAIL_SEND_NOT_FOUND", "Mail send intent was not found.")
        if row["status"] != "QUEUED" or row["attempts"] >= MAX_ATTEMPTS:
            raise ApplicationError(409, "MAIL_SEND_NOT_QUEUED", "Mail is not waiting to be sent.")
        if row["mailbox_state"] != "connected":
            raise ApplicationError(409, "MAILBOX_NOT_CONNECTED", "Mailbox is not connected.")
        await conn.execute(
            "UPDATE integration.mail_send_intents SET status='SUBMITTING', attempts=attempts+1, "
            "submitted_at=now(), error_code=NULL, updated_at=now() WHERE id=%s",
            (intent_id,),
        )
        await add_event(conn, intent_id, "SUBMITTING")
        return dict(row)


async def finish(
    settings: Settings, intent_id: UUID, status: str, event: str, code: str | None
) -> None:
    """`SUBMITTING` 결과를 기록한다. 다른 처리가 이미 바꿨으면 덮어쓰지 않는다."""
    async with connection(settings) as conn, conn.transaction():
        cursor = await conn.execute(
            "UPDATE integration.mail_send_intents SET status=%s, error_code=%s, "
            "accepted_at=CASE WHEN %s='ACCEPTED' THEN now() END, updated_at=now() "
            "WHERE id=%s AND status='SUBMITTING' RETURNING id",
            (status, code, status, intent_id),
        )
        if await cursor.fetchone() is not None:
            await add_event(conn, intent_id, event, code=code)


async def defer(settings: Settings, row: dict[str, Any], code: str) -> SendResult:
    """보내지 않은 시도를 대기로 되돌린다. 시도 한도를 다 썼으면 실패로 마감한다.

    Args:
        settings: DB 설정.
        row: claim이 잠근 행(시도 수는 증가 전 값).
        code: 미룬 이유.

    Returns:
        SendResult: `QUEUED` 또는 `FAILED`(`MAIL_SEND_ATTEMPTS_EXHAUSTED`).
    """
    intent_id = row["id"]
    if row["attempts"] + 1 >= MAX_ATTEMPTS:
        await finish(settings, intent_id, "FAILED", "FAILED", "MAIL_SEND_ATTEMPTS_EXHAUSTED")
        return SendResult(intent_id, "FAILED", "MAIL_SEND_ATTEMPTS_EXHAUSTED")
    await finish(settings, intent_id, "QUEUED", "DEFERRED", code)
    return SendResult(intent_id, "QUEUED", code)


async def message_body(row: dict[str, Any], store: ObjectReader) -> dict[str, Any]:
    """저장된 행을 다시 확인해 sendMail 본문을 만든다.

    Args:
        row: 잠근 발송 의도 행.
        store: 첨부 저장소.

    Returns:
        dict[str, Any]: sendMail 요청 본문. 해시가 다르면 409, 첨부가 다르면 503.
    """
    recipients = row["recipients"]
    digest = payload_sha256(
        subject=row["subject"],
        body_type=row["body_type"],
        body=row["body"],
        to=recipients["to"],
        cc=recipients["cc"],
        attachments=row["attachments"],
    )
    if digest != row["payload_sha256"]:
        raise ApplicationError(409, "MAIL_APPROVAL_MISMATCH", "Mail differs from the approval.")
    files = []
    for a in row["attachments"]:
        data = await store.get(
            StoredObject(key=a["storage_key"], sha256=a["sha256"], size=a["size"])
        )
        files.append(
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": a["name"],
                "contentType": a["content_type"],
                "contentBytes": base64.b64encode(data).decode(),
            }
        )
    return {
        "message": {
            "subject": row["subject"],
            "body": {
                "contentType": "HTML" if row["body_type"] == "html" else "Text",
                "content": row["body"],
            },
            "toRecipients": [{"emailAddress": {"address": a}} for a in recipients["to"]],
            "ccRecipients": [{"emailAddress": {"address": a}} for a in recipients["cc"]],
            "attachments": files,
            # 보낸편지함에서 이 의도를 정확히 찾기 위한 숨은 표시. 받는 사람에게 보이지 않는다.
            "singleValueExtendedProperties": [{"id": INTENT_PROPERTY, "value": str(row["id"])}],
        },
        "saveToSentItems": True,
    }


async def post_send(
    client: httpx.AsyncClient, token: SecretStr, body: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        SEND_URL,
        json=body,
        headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        follow_redirects=False,
    )


async def submit_intent(
    settings: Settings,
    intent_id: UUID,
    *,
    get_token: MailboxTokenGetter,
    client: httpx.AsyncClient,
    store: ObjectReader,
) -> SendResult:
    """발송 의도를 한 번 보낸다. 결과를 모르면 다시 보내지 않는다.

    Args:
        settings: DB 설정.
        intent_id: 발송 의도 ID.
        get_token: KAN-81 토큰 제공자.
        client: 제한 시간을 둔 Graph 클라이언트. 자동 재시도·리디렉션 없음.
        store: 첨부 저장소.

    Returns:
        SendResult: `ACCEPTED`·`UNKNOWN`·`FAILED`·`QUEUED`(사용량 제한) 중 하나.
    """
    row = await claim(settings, intent_id)
    # 여기부터는 SUBMITTING이 커밋돼 있다. 외부 호출 전에 실패하면 보내지 않은 것이다.
    try:
        token = checked_token(
            await get_token(mailbox_id=row["mailbox_id"], owner_user_id=row["owner_user_id"])
        )
        body = await message_body(row, store)
    except ApplicationError as error:
        # 아직 보내지 않았다. 일시 장애면 대기로 되돌리고, 그 밖에는 실패로 끝낸다.
        if error.code in PRE_SEND_TRANSIENT:
            return await defer(settings, row, error.code)
        await finish(settings, intent_id, "FAILED", "FAILED", error.code)
        return SendResult(intent_id, "FAILED", error.code)
    except Exception:
        # 아직 보내지 않았다. SUBMITTING에 남겨 결과 불명으로 오인되지 않게 실패로 마감한다.
        await finish(settings, intent_id, "FAILED", "FAILED", "MAIL_SEND_PREPARE_FAILED")
        raise
    try:
        response = await post_send(client, token, body)
        if response.status_code == 401:
            # 401은 처리 전에 거절된 것이라 새 토큰으로 한 번만 다시 보내도 안전하다.
            try:
                token = checked_token(
                    await get_token(
                        mailbox_id=row["mailbox_id"],
                        owner_user_id=row["owner_user_id"],
                        force_refresh=True,
                    )
                )
            except ApplicationError as error:
                await finish(settings, intent_id, "FAILED", "FAILED", error.code)
                return SendResult(intent_id, "FAILED", error.code)
            response = await post_send(client, token, body)
    except httpx.HTTPError:
        # 요청이 Graph에 닿았는지 모른다. 자동으로 다시 보내지 않는다.
        await finish(settings, intent_id, "UNKNOWN", "UNKNOWN", "GRAPH_NO_RESPONSE")
        return SendResult(intent_id, "UNKNOWN", "GRAPH_NO_RESPONSE")
    status = response.status_code
    if status == 202:
        await finish(settings, intent_id, "ACCEPTED", "ACCEPTED", None)
        return SendResult(intent_id, "ACCEPTED")
    if status == 429:
        # 사용량 제한은 보내지 않은 것이다. 같은 의도를 나중에 다시 보낼 수 있다.
        return await defer(settings, row, "GRAPH_THROTTLED")
    if status == 401 or status in REJECTED:
        code = "GRAPH_UNAUTHORIZED" if status == 401 else REJECTED[status]
        await finish(settings, intent_id, "FAILED", "FAILED", code)
        return SendResult(intent_id, "FAILED", code)
    # 5xx 등은 Graph가 처리했는지 알 수 없다.
    await finish(settings, intent_id, "UNKNOWN", "UNKNOWN", "GRAPH_UNAVAILABLE")
    return SendResult(intent_id, "UNKNOWN", "GRAPH_UNAVAILABLE")


async def confirm_sent_copy(
    settings: Settings,
    intent_id: UUID,
    *,
    get_token: MailboxTokenGetter,
    client: httpx.AsyncClient,
) -> SendResult:
    """보낸편지함에서 이 의도의 사본을 찾는다. 배달 확인이 아니다.

    Args:
        settings: DB 설정.
        intent_id: `ACCEPTED` 또는 `UNKNOWN` 발송 의도.
        get_token: KAN-81 토큰 제공자.
        client: Graph 클라이언트.

    Returns:
        SendResult: 사본을 찾았으면 `sent_copy_confirmed=True`.
    """
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT i.status, i.mailbox_id, i.sent_copy_confirmed_at, c.owner_user_id "
            "FROM integration.mail_send_intents i "
            "JOIN mailbox_auth.connections c ON c.id=i.mailbox_id WHERE i.id=%s",
            (intent_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise ApplicationError(404, "MAIL_SEND_NOT_FOUND", "Mail send intent was not found.")
    if row["sent_copy_confirmed_at"] is not None:
        return SendResult(intent_id, row["status"], sent_copy_confirmed=True)
    if row["status"] not in ("ACCEPTED", "UNKNOWN"):
        raise ApplicationError(409, "MAIL_SEND_NOT_SENT", "Mail has not been submitted.")
    token = checked_token(
        await get_token(mailbox_id=row["mailbox_id"], owner_user_id=row["owner_user_id"])
    )
    flt = (
        "singleValueExtendedProperties/Any(ep: ep/id eq '"
        + INTENT_PROPERTY
        + f"' and ep/value eq '{intent_id}')"
    )
    try:
        response = await client.get(
            SENT_ITEMS_URL,
            params={"$filter": flt, "$select": "id,internetMessageId,sentDateTime", "$top": "5"},
            headers={
                "Authorization": f"Bearer {token.get_secret_value()}",
                "Prefer": 'IdType="ImmutableId"',
            },
            follow_redirects=False,
        )
        found = response.json().get("value") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError):
        found = None
    if not isinstance(found, list):
        raise ApplicationError(503, "GRAPH_UNAVAILABLE", "Mail provider is unavailable.")
    async with connection(settings) as conn, conn.transaction():
        if not found or not isinstance(found[0], dict) or not found[0].get("id"):
            if row["status"] == "UNKNOWN":
                await add_event(conn, intent_id, "SENT_COPY_NOT_FOUND")
            return SendResult(intent_id, row["status"])
        copy = found[0]
        cursor = await conn.execute(
            "UPDATE integration.mail_send_intents SET status='ACCEPTED', "
            "accepted_at=coalesce(accepted_at, now()), error_code=NULL, "
            "sent_copy_confirmed_at=now(), sent_graph_id=%s, sent_internet_message_id=%s, "
            "updated_at=now() WHERE id=%s AND status IN ('ACCEPTED','UNKNOWN') RETURNING id",
            (str(copy["id"])[:512], copy.get("internetMessageId"), intent_id),
        )
        if await cursor.fetchone() is None:
            raise ApplicationError(409, "MAIL_SEND_NOT_SENT", "Mail has not been submitted.")
        await add_event(conn, intent_id, "SENT_COPY_CONFIRMED")
    return SendResult(intent_id, "ACCEPTED", sent_copy_confirmed=True)


async def authorize_resend(
    settings: Settings, intent_id: UUID, *, actor_user_id: UUID, reason: str
) -> SendResult:
    """결과 불명 발송을 사람이 확인하고 다시 보낼 수 있게 한다.

    마지막 전송 시작 뒤 `RESEND_GRACE`가 지난 다음 보낸편지함에 사본이 없다는 확인
    (`SENT_COPY_NOT_FOUND`)이 있어야 한다. 승인자 본인만 허가할 수 있다.

    Args:
        settings: DB 설정.
        intent_id: `UNKNOWN` 발송 의도.
        actor_user_id: 허가하는 사람(승인자).
        reason: 판단 근거. 1~500자.

    Returns:
        SendResult: `QUEUED`. 조건이 맞지 않으면 409.
    """
    if not 1 <= len(reason.strip()) <= 500:
        raise invalid("MAIL_RESEND_REASON_REQUIRED", "A reason is required.")
    async with connection(settings) as conn, conn.transaction():
        cursor = await conn.execute(
            "SELECT status, approved_by, attempts, submitted_at FROM integration.mail_send_intents "
            "WHERE id=%s FOR UPDATE",
            (intent_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ApplicationError(404, "MAIL_SEND_NOT_FOUND", "Mail send intent was not found.")
        if row["approved_by"] != actor_user_id:
            raise ApplicationError(403, "MAIL_RESEND_FORBIDDEN", "Only the approver can resend.")
        if row["status"] != "UNKNOWN" or row["attempts"] >= MAX_ATTEMPTS:
            raise ApplicationError(409, "MAIL_RESEND_NOT_ALLOWED", "Mail cannot be resent.")
        cursor = await conn.execute(
            "SELECT 1 FROM integration.mail_send_events WHERE intent_id=%s "
            "AND event='SENT_COPY_NOT_FOUND' AND at >= %s",
            (intent_id, row["submitted_at"] + RESEND_GRACE),
        )
        if await cursor.fetchone() is None:
            raise ApplicationError(
                409, "MAIL_RESEND_UNVERIFIED", "Check Sent Items before resending."
            )
        await conn.execute(
            "UPDATE integration.mail_send_intents SET status='QUEUED', updated_at=now() "
            "WHERE id=%s",
            (intent_id,),
        )
        await add_event(
            conn, intent_id, "RESEND_AUTHORIZED", actor=actor_user_id, note=reason.strip()
        )
    return SendResult(intent_id, "QUEUED")


async def expire_submitting(settings: Settings, older_than: timedelta) -> int:
    """오래 `SUBMITTING`에 남은 의도를 `UNKNOWN`으로 바꾼다. 나갔을 수 있으므로 다시 보내지 않는다.

    Args:
        settings: DB 설정.
        older_than: 전송 시작 뒤 이 시간이 지난 것만.

    Returns:
        int: 바꾼 수.
    """
    async with connection(settings) as conn, conn.transaction():
        cursor = await conn.execute(
            "UPDATE integration.mail_send_intents SET status='UNKNOWN', "
            "error_code='MAIL_SUBMIT_INTERRUPTED', updated_at=now() "
            "WHERE status='SUBMITTING' AND submitted_at < now() - %s RETURNING id",
            (older_than,),
        )
        ids = [r["id"] for r in await cursor.fetchall()]
        for intent_id in ids:
            await add_event(conn, intent_id, "UNKNOWN", code="MAIL_SUBMIT_INTERRUPTED")
    return len(ids)


__all__ = [
    "Attachment",
    "SendRequest",
    "SendResult",
    "authorize_resend",
    "confirm_sent_copy",
    "create_intent",
    "expire_submitting",
    "payload_sha256",
    "submit_intent",
]
