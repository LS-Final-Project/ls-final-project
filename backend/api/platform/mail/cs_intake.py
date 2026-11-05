"""C/S 문의로 보이는 새 메일과 담당자 확인 후 등록(KAN-237). KAN-73 RFP 후보와 같은 틀이다.

1. `GET /cs-candidates`: 내 받은편지함 메일 중 아직 어떤 업무에도 연결되지 않았고(`mail_links` 없음),
   회신이 아니라고 판정됐고(회신 판정 `NONE`, 또는 회신 표시 없이 주고받은 주소라 검토 중인 `ADDRESS_MATCH`),
   분류가 RFP·기타(`NEW_RFP`·`OTHER`)가 아닌 메일을 고른다. 보낸 주소가 C/S 고객 연락처(C33)에 있는
   메일만 남긴다. 플랫폼은 cs 스키마를 읽지 않으므로 연락처는 C/S 내부 경로(`sender-customers`)로 묻는다.
2. `GET /messages/{id}/content`: 확인 화면에 보일 제목·본문을 원문에서 꺼낸다(저장하지 않는다).
   KAN-238부터 역할과 무관하게 메일함 주인이면 읽는다(영업이 RFP 메일을 본다).
3. 등록은 기존 `register-ticket`(KAN-110), [문의 아님]은 기존 재분류(`OTHER`)를 쓴다.

4. `GET /messages`(KAN-238): 메일함 화면의 받은 메일 전체 목록과 연결 상태. 메타데이터만 DB에서 읽는다.

모델을 부르지 않는다. 자동 접수는 하지 않는다(후보는 추천).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from backend.api.platform.auth.context import load_session_principal
from backend.api.platform.auth.http import SESSION_COOKIE, operational_identity
from backend.api.platform.auth.mailbox_http import mailbox_provider
from backend.api.platform.auth.microsoft import PersonalMicrosoftOAuth
from backend.api.platform.mail.business import SENDER_CUSTOMERS_PATH, cs_caller
from backend.api.platform.mail.intake import BODY_LIMIT, read_raw
from backend.api.platform.mail.links import OwnerService, not_found
from backend.engine.mail_triage.extract import mail_text
from backend.engine.mail_triage.triage import MailType
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

ACTIVATION = {"x-implementation-status": "partial", "x-activation": "MAILBOX_AUTH_ENABLED"}
# 연락처를 물을 최근 메일 수. 연락처가 맞는 메일만 돌려준다.
SCAN_LIMIT = 100
router = APIRouter(
    prefix="/api/v1/platform/mail",
    tags=["mail-cs-intake"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)


class CsCandidate(BaseModel):
    """문의로 보이는 메일 한 통. 본문은 담지 않는다."""

    message_id: UUID
    # 최신 분류 판본(없으면 0). [문의 아님] 재분류가 읽은 판본으로 보낸다.
    triage_version: int
    subject: str | None
    from_address: str
    received_at: datetime
    has_attachments: bool
    # 고객 연락처로 찾은 고객 ID. 주소 일치 먼저, 그다음 도메인. 첫 값을 등록 기본값으로 쓴다.
    customer_external_ids: list[str]


class CsCandidateList(BaseModel):
    items: list[CsCandidate]


class MailLink(BaseModel):
    owner_service: OwnerService
    # 예: cs_ticket:<문의 ID>, rfp_project:<프로젝트 ID>
    business_ref: str


class InboxMessage(BaseModel):
    """받은 메일 한 통과 연결 상태(KAN-238). 본문은 담지 않는다."""

    message_id: UUID
    received_at: datetime
    from_address: str | None
    subject: str | None
    has_attachments: bool
    # 가장 먼저 붙은 업무 연결(문의·RFP 프로젝트). 없으면 null.
    link: MailLink | None
    # 사람이 어느 문의의 답장인지 정해야 하는 열린 회신 검토가 있는지.
    reply_review_open: bool
    # 최신 분류 유형(규칙·사람). 분류가 없으면 null.
    mail_type: MailType | None


class InboxMessageList(BaseModel):
    items: list[InboxMessage]


class MailContent(BaseModel):
    message_id: UUID
    subject: str | None
    from_address: str | None
    received_at: datetime
    body: str
    # 본문이 문의 한도(50,000자)를 넘어 잘라 보였는지. 넘으면 등록도 422다.
    truncated: bool


async def _cs_agent(request: Request) -> None:
    """메일 문의 등록은 C/S 담당자(cs_agent)만 한다. 아니면 403."""
    principal = await load_session_principal(
        request.app.state.settings, request.cookies.get(SESSION_COOKIE)
    )
    if not principal.has("cs_agent"):
        raise HTTPException(403)


async def ask_customers(request: Request, addresses: list[str]) -> dict[str, list[str]]:
    """보낸 주소의 고객을 C/S에 묻는다. 업무 중계가 꺼져 있으면 404, C/S가 실패하면 503."""
    caller = cs_caller(request)
    if caller is None:
        raise HTTPException(404)
    if not addresses:
        return {}
    try:
        reply = await caller.post(
            SENDER_CUSTOMERS_PATH, {"addresses": addresses}, request_id=request.state.request_id
        )
        items = reply.json()["data"]["items"] if reply.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        items = None
    if items is None:
        raise ApplicationError(503, "CS_UNAVAILABLE", "The C/S service did not answer.")
    return {i["address"]: list(i["customer_external_ids"]) for i in items}


@router.get(
    "/cs-candidates",
    response_model=SuccessResponse[CsCandidateList],
    operation_id="platform_list_mail_cs_candidates",
    openapi_extra=ACTIVATION,
)
async def list_candidates(
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[CsCandidateList]:
    """내 메일함에서 고객 연락처의 주소로 온, 아직 업무에 연결하지 않은 새 메일(최근 순, 최대 50통).

    Args:
        request: 운영 세션 요청.
        response: CSRF 헤더를 받을 응답.
        provider: 사서함 기능 비활성이면 404로 닫는 의존성.

    Returns:
        SuccessResponse[CsCandidateList]: 최근 받은 순. cs_agent가 아니면 403.
    """
    user = await operational_identity(request, response)
    await _cs_agent(request)
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT m.id, m.subject, m.from_address, m.received_at, m.has_attachments, "
            "coalesce(l.latest_version, 0) AS triage_version "
            "FROM integration.mail_messages m JOIN mailbox_auth.connections c ON c.id=m.mailbox_id "
            "LEFT JOIN integration.mail_triage_latest l ON l.message_id=m.id "
            "LEFT JOIN integration.mail_triage t ON t.message_id=l.message_id "
            "AND t.version=l.latest_version "
            "WHERE c.owner_user_id=%s AND m.folder='inbox' AND m.removed_at IS NULL "
            "AND m.from_address IS NOT NULL AND t.mail_type IS DISTINCT FROM 'NEW_RFP' "
            "AND t.mail_type IS DISTINCT FROM 'OTHER' "
            "AND NOT EXISTS (SELECT 1 FROM integration.mail_links k WHERE k.message_id=m.id) "
            "AND (EXISTS (SELECT 1 FROM integration.mail_reply_checks r WHERE r.message_id=m.id "
            "AND r.outcome='NONE') OR EXISTS (SELECT 1 FROM integration.mail_reply_reviews v "
            "WHERE v.message_id=m.id AND v.status='OPEN' AND v.reason='ADDRESS_MATCH')) "
            "ORDER BY m.received_at DESC, m.id LIMIT %s",
            (user.id, SCAN_LIMIT),
        )
        rows = await cursor.fetchall()
    addresses = list(dict.fromkeys(r["from_address"].strip().lower() for r in rows))
    customers = await ask_customers(request, addresses)
    items = [
        CsCandidate(
            message_id=r["id"],
            triage_version=r["triage_version"],
            subject=r["subject"],
            from_address=r["from_address"],
            received_at=r["received_at"],
            has_attachments=r["has_attachments"],
            customer_external_ids=customers[r["from_address"].strip().lower()],
        )
        for r in rows
        if r["from_address"].strip().lower() in customers
    ][:50]
    return SuccessResponse(
        data=CsCandidateList(items=items), meta=ResponseMeta(request_id=request.state.request_id)
    )


@router.get(
    "/messages",
    response_model=SuccessResponse[InboxMessageList],
    operation_id="platform_list_mail_messages",
    openapi_extra=ACTIVATION,
)
async def list_messages(
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[InboxMessageList]:
    """내 메일함의 받은 메일(최근 순 최대 100통)과 연결 상태. 역할과 무관하게 자기 메일함만 보인다.

    Args:
        request: 운영 세션 요청.
        response: CSRF 헤더를 받을 응답.
        provider: 사서함 기능 비활성이면 404로 닫는 의존성.

    Returns:
        SuccessResponse[InboxMessageList]: 받은 시각 내림차순. 본문 없음.
    """
    user = await operational_identity(request, response)
    # ponytail: 최근 100통 고정. 메일이 많아지면 received_at 커서를 붙인다.
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT m.id, m.received_at, m.from_address, m.subject, m.has_attachments, "
            "k.owner_service, k.business_ref, t.mail_type, EXISTS (SELECT 1 FROM "
            "integration.mail_reply_reviews v WHERE v.message_id=m.id AND v.status='OPEN') "
            "AS reply_review_open "
            "FROM integration.mail_messages m JOIN mailbox_auth.connections c ON c.id=m.mailbox_id "
            "LEFT JOIN LATERAL (SELECT owner_service, business_ref FROM integration.mail_links "
            "WHERE message_id=m.id ORDER BY created_at, id LIMIT 1) k ON true "
            "LEFT JOIN integration.mail_triage_latest l ON l.message_id=m.id "
            "LEFT JOIN integration.mail_triage t ON t.message_id=l.message_id "
            "AND t.version=l.latest_version "
            "WHERE c.owner_user_id=%s AND m.folder='inbox' AND m.removed_at IS NULL "
            "ORDER BY m.received_at DESC, m.id LIMIT 100",
            (user.id,),
        )
        rows = await cursor.fetchall()
    items = [
        InboxMessage(
            message_id=r["id"],
            received_at=r["received_at"],
            from_address=r["from_address"],
            subject=r["subject"],
            has_attachments=r["has_attachments"],
            link=MailLink(owner_service=r["owner_service"], business_ref=r["business_ref"])
            if r["business_ref"]
            else None,
            reply_review_open=r["reply_review_open"],
            mail_type=r["mail_type"],
        )
        for r in rows
    ]
    return SuccessResponse(
        data=InboxMessageList(items=items), meta=ResponseMeta(request_id=request.state.request_id)
    )


@router.get(
    "/messages/{message_id}/content",
    response_model=SuccessResponse[MailContent],
    operation_id="platform_get_mail_content",
    openapi_extra=ACTIVATION,
)
async def get_content(
    message_id: UUID,
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[MailContent]:
    """내 받은 메일 한 통의 제목·본문 텍스트(문의 등록 전 확인·메일함 보기). 원문에서 꺼내고 저장하지 않는다.

    역할과 무관하게 메일함 주인만 읽는다(KAN-238). 다른 사람 메일함·보낸편지함·지운 메일은 404,
    원문 저장소가 없으면 404.

    Args:
        message_id: 받은 메일 ID.
        request: 운영 세션 요청.
        response: CSRF 헤더를 받을 응답.
        provider: 사서함 기능 비활성이면 404로 닫는 의존성.

    Returns:
        SuccessResponse[MailContent]: 제목·보낸 사람·본문.
    """
    user = await operational_identity(request, response)
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT m.from_address, m.received_at, m.raw_storage_key, m.raw_sha256, m.raw_size "
            "FROM integration.mail_messages m JOIN mailbox_auth.connections c ON c.id=m.mailbox_id "
            "WHERE m.id=%s AND c.owner_user_id=%s AND m.folder='inbox' AND m.removed_at IS NULL",
            (message_id, user.id),
        )
        row: dict[str, Any] | None = await cursor.fetchone()
    if row is None:
        raise not_found()
    try:
        subject, body, sender = mail_text(await read_raw(request, row))
    except ValueError:
        raise ApplicationError(422, "MAIL_INVALID_INPUT", "The raw message is invalid.") from None
    return SuccessResponse(
        data=MailContent(
            message_id=message_id,
            subject=subject,
            from_address=row["from_address"] or sender,
            received_at=row["received_at"],
            body=body[:BODY_LIMIT],
            truncated=len(body) > BODY_LIMIT,
        ),
        meta=ResponseMeta(request_id=request.state.request_id),
    )
