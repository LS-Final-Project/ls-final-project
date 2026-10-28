"""받은 메일을 업무(문의 등)에 대한 회신으로 연결한다(KAN-75).

연결 근거는 메일의 기술적 표시뿐이다. 우선순위는 헤더(`In-Reply-To`·`References`가 우리가 보낸
메일이나 업무에 연결된 메일의 `internetMessageId`를 가리킴), 다음이 Graph 스레드
(`conversationId`가 같은 메일이 업무에 연결됨)다. 제목이나 AI 분류만으로는 연결하지 않는다
([데이터 계약](../../../../docs/data-contract.md#메일과-파일-연결)).

- 한 업무로만 맞으면 자동 연결(`REPLY`, 근거 `HEADER`/`THREAD`).
- 후보가 여러 업무이거나, 헤더와 스레드가 다른 업무를 가리키거나, 회신 표시가 있는데 맞는 업무가
  없거나, 분류(KAN-73)가 기존 스레드가 아니라고 보면 연결하지 않고 사람 검토로 보낸다.
- 메일을 읽었는지(isRead)는 보지 않는다. 연결은 "회신이 왔다"는 기록일 뿐 업무 상태를 바꾸지
  않는다. 문의를 다시 여는 일은 업무 쪽(KAN-110)이 연결을 읽고 한다.
"""

import hashlib
import re
from datetime import datetime
from email import policy
from email.parser import BytesParser
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from backend.api.platform.auth.http import operational_identity
from backend.api.platform.auth.mailbox_http import mailbox_provider, mutation_token
from backend.api.platform.auth.microsoft import PersonalMicrosoftOAuth
from backend.api.platform.mail.send import BUSINESS_REF
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse
from backend.shared.settings import Settings

OwnerService = Literal["cs", "platform"]
Method = Literal["HEADER", "THREAD", "HUMAN", "BUSINESS"]
Reason = Literal["AMBIGUOUS", "UNMATCHED_REPLY", "CONFLICT", "TRIAGE_DISAGREES"]
# Message-ID 하나(꺾쇠 포함). 헤더 값에서 이것만 뽑고 나머지 글자는 버린다.
MESSAGE_ID = re.compile(r"<[^<>\s]{1,512}>")
MAX_REFERENCES = 50


class Candidate(BaseModel):
    owner_service: OwnerService
    business_ref: str
    method: Method


class ReplyResult(BaseModel):
    """회신 판정 결과."""

    message_id: UUID
    # LINKED: 자동 또는 이미 연결, REVIEW: 사람 검토 대기, NONE: 회신 아님(새 메일).
    outcome: Literal["LINKED", "REVIEW", "NONE"]
    link: Candidate | None = None
    reason: Reason | None = None
    candidates: list[Candidate] = Field(default_factory=list)


def not_found(code: str = "MAIL_MESSAGE_NOT_FOUND") -> ApplicationError:
    return ApplicationError(404, code, "Mail message was not found.")


def reply_headers(raw: bytes) -> list[str]:
    """원문에서 회신 헤더가 가리키는 Message-ID를 순서대로(중복 없이)꺼낸다.

    Args:
        raw: 원문 MIME 바이트.

    Returns:
        list[str]: `In-Reply-To` 먼저, 그다음 `References`의 Message-ID. 없으면 빈 목록.
    """
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
        values = [str(message.get("In-Reply-To") or ""), str(message.get("References") or "")]
    except (ValueError, LookupError, TypeError):
        raise ApplicationError(
            422, "MAIL_LINK_INVALID_INPUT", "The raw message is invalid."
        ) from None
    found: list[str] = []
    for value in values:
        for item in MESSAGE_ID.findall(value):
            if item not in found:
                found.append(item)
    return found[:MAX_REFERENCES]


def keys(rows: list[dict[str, Any]], method: Method) -> list[Candidate]:
    seen: dict[tuple[str, str], Candidate] = {}
    for r in rows:
        seen.setdefault(
            (r["owner_service"], r["business_ref"]),
            Candidate(
                owner_service=r["owner_service"], business_ref=r["business_ref"], method=method
            ),
        )
    return sorted(seen.values(), key=lambda c: (c.owner_service, c.business_ref))


async def header_candidates(conn: Any, mailbox_id: UUID, ids: list[str]) -> list[Candidate]:
    """헤더가 가리키는 메일이 업무에 속하는지 찾는다(우리가 보낸 메일 또는 업무에 연결된 메일)."""
    if not ids:
        return []
    cursor = await conn.execute(
        "SELECT owner_service, business_ref FROM integration.mail_send_intents "
        "WHERE mailbox_id=%s AND sent_internet_message_id = ANY(%s) "
        "UNION SELECT l.owner_service, l.business_ref FROM integration.mail_links l "
        "JOIN integration.mail_messages m ON m.id=l.message_id "
        "WHERE m.mailbox_id=%s AND m.internet_message_id = ANY(%s)",
        (mailbox_id, ids, mailbox_id, ids),
    )
    return keys(await cursor.fetchall(), "HEADER")


async def thread_candidates(
    conn: Any, mailbox_id: UUID, conversation_id: str | None, message_id: UUID
) -> list[Candidate]:
    """같은 Graph 스레드의 메일 중 업무에 속한 것을 찾는다."""
    if not conversation_id:
        return []
    cursor = await conn.execute(
        "SELECT i.owner_service, i.business_ref FROM integration.mail_send_intents i "
        "JOIN integration.mail_messages m ON m.mailbox_id=i.mailbox_id AND m.graph_id=i.sent_graph_id "
        "WHERE i.mailbox_id=%s AND m.conversation_id=%s "
        "UNION SELECT l.owner_service, l.business_ref FROM integration.mail_links l "
        "JOIN integration.mail_messages m ON m.id=l.message_id "
        "WHERE m.mailbox_id=%s AND m.conversation_id=%s AND m.id<>%s",
        (mailbox_id, conversation_id, mailbox_id, conversation_id, message_id),
    )
    return keys(await cursor.fetchall(), "THREAD")


def decide(
    header: list[Candidate], thread: list[Candidate], is_reply: bool, triage_link: str | None
) -> tuple[Candidate | None, Reason | None, list[Candidate]]:
    """후보로 자동 연결할지, 검토로 보낼지, 회신이 아닌지 정한다(순수 함수).

    Args:
        header: 헤더 근거 후보.
        thread: 스레드 근거 후보.
        is_reply: 회신 헤더가 있었는지.
        triage_link: KAN-73 최신 분류의 연결 추천(없으면 None).

    Returns:
        tuple: (연결할 후보, 검토 이유, 검토에 보일 후보). 둘 다 None이면 회신이 아니다.
    """
    if header:
        if len(header) > 1:
            return None, "AMBIGUOUS", header
        other = [
            t
            for t in thread
            if (t.owner_service, t.business_ref)
            != (header[0].owner_service, header[0].business_ref)
        ]
        if other:
            return None, "CONFLICT", header + other
        chosen = header[0]
    elif thread:
        if len(thread) > 1:
            return None, "AMBIGUOUS", thread
        chosen = thread[0]
    else:
        return (None, "UNMATCHED_REPLY", []) if is_reply else (None, None, [])
    # 분류가 "새 문의·새 프로젝트"로 본 메일은 다른 업무에 대한 회신일 수 있어 사람이 본다.
    if triage_link is not None and triage_link != "EXISTING_THREAD":
        return None, "TRIAGE_DISAGREES", [chosen]
    return chosen, None, [chosen]


async def resolve_reply(settings: Settings, message_id: UUID, raw: bytes) -> ReplyResult:
    """받은 메일 한 통을 회신으로 판정해 연결하거나 검토로 보낸다. 다시 불러도 결과가 같다.

    원문은 호출자가 객체 저장소에서 읽어 넘기고, 저장된 SHA-256과 같아야 한다.

    Args:
        settings: 플랫폼 DB 설정.
        message_id: 수집 표의 메일 ID(받은편지함).
        raw: 그 메일의 원문 MIME.

    Returns:
        ReplyResult: 연결·검토·회신 아님. 메일이 없으면 404, 원문이 다르면 409.
    """
    async with connection(settings) as conn, conn.transaction():
        cursor = await conn.execute(
            "SELECT mailbox_id, folder, raw_sha256, conversation_id, removed_at "
            "FROM integration.mail_messages WHERE id=%s FOR UPDATE",
            (message_id,),
        )
        message = await cursor.fetchone()
        if message is None:
            raise not_found()
        if hashlib.sha256(raw).hexdigest() != message["raw_sha256"]:
            raise ApplicationError(409, "MAIL_LINK_RAW_MISMATCH", "The raw message does not match.")
        cursor = await conn.execute(
            "SELECT 1 FROM integration.mail_reply_checks WHERE message_id=%s", (message_id,)
        )
        if await cursor.fetchone() is not None:
            return await current(conn, message_id) or ReplyResult(
                message_id=message_id, outcome="NONE"
            )
        if message["folder"] != "inbox" or message["removed_at"] is not None:
            # 우리가 보낸 메일(발송 의도로 이미 업무와 묶임)과 사서함에서 지워진 메일은 판정하지 않는다.
            return await checked(conn, ReplyResult(message_id=message_id, outcome="NONE"))
        ids = reply_headers(raw)
        header = await header_candidates(conn, message["mailbox_id"], ids)
        thread = await thread_candidates(
            conn, message["mailbox_id"], message["conversation_id"], message_id
        )
        cursor = await conn.execute(
            "SELECT t.link_suggestion FROM integration.mail_triage_latest l "
            "JOIN integration.mail_triage t ON t.message_id=l.message_id "
            "AND t.version=l.latest_version WHERE l.message_id=%s",
            (message_id,),
        )
        triage = await cursor.fetchone()
        chosen, reason, shown = decide(
            header, thread, bool(ids), triage["link_suggestion"] if triage else None
        )
        if chosen is not None:
            await conn.execute(
                "INSERT INTO integration.mail_links (id, message_id, owner_service, business_ref, "
                "kind, method) VALUES (%s, %s, %s, %s, 'REPLY', %s)",
                (uuid4(), message_id, chosen.owner_service, chosen.business_ref, chosen.method),
            )
            return await checked(
                conn, ReplyResult(message_id=message_id, outcome="LINKED", link=chosen)
            )
        if reason is not None:
            await conn.execute(
                "INSERT INTO integration.mail_reply_reviews (message_id, reason, candidates) "
                "VALUES (%s, %s, %s)",
                (message_id, reason, Jsonb([c.model_dump() for c in shown])),
            )
            return await checked(
                conn,
                ReplyResult(
                    message_id=message_id, outcome="REVIEW", reason=reason, candidates=shown
                ),
            )
        return await checked(conn, ReplyResult(message_id=message_id, outcome="NONE"))


async def checked(conn: Any, result: ReplyResult) -> ReplyResult:
    """판정했다는 표시를 남기고 결과를 그대로 돌려준다(같은 트랜잭션)."""
    await conn.execute(
        "INSERT INTO integration.mail_reply_checks (message_id, outcome) VALUES (%s, %s)",
        (result.message_id, result.outcome),
    )
    return result


async def judge_new_replies(
    settings: Settings, mailbox_id: UUID, store: Any, limit: int = 50
) -> dict[str, int]:
    """판정하지 않은 받은 메일을 오래된 순으로 판정한다. 수집 워커가 수집 뒤 부른다.

    한 통이 실패해도(원문 읽기 등) 나머지를 계속하고, 실패한 메일은 표시가 없어 다음에 다시 한다.

    Args:
        settings: 플랫폼 DB 설정.
        mailbox_id: 사서함 연결 ID.
        store: 원문을 읽을 저장소(`ObjectReader`).
        limit: 한 번에 판정할 최대 수.

    Returns:
        dict[str, int]: 결과별 수와 실패 수.
    """
    from backend.api.platform.mail.storage import StoredObject

    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT m.id, m.raw_storage_key, m.raw_sha256, m.raw_size "
            "FROM integration.mail_messages m WHERE m.mailbox_id=%s AND m.folder='inbox' "
            "AND m.removed_at IS NULL AND NOT EXISTS (SELECT 1 FROM integration.mail_reply_checks c "
            "WHERE c.message_id=m.id) ORDER BY m.received_at, m.id LIMIT %s",
            (mailbox_id, limit),
        )
        pending = await cursor.fetchall()
    counts = {"LINKED": 0, "REVIEW": 0, "NONE": 0, "failed": 0}
    for row in pending:
        try:
            raw = await store.get(
                StoredObject(row["raw_storage_key"], row["raw_sha256"], row["raw_size"])
            )
            counts[(await resolve_reply(settings, row["id"], raw)).outcome] += 1
        except (ApplicationError, HTTPException):
            # 원문 읽기·DB 일시 장애. 표시를 남기지 않아 다음 실행에서 다시 한다.
            counts["failed"] += 1
    return counts


async def current(conn: Any, message_id: UUID) -> ReplyResult | None:
    """이미 연결됐거나 검토 중인 결과를 돌려준다."""
    cursor = await conn.execute(
        "SELECT owner_service, business_ref, method FROM integration.mail_links "
        "WHERE message_id=%s AND kind='REPLY' ORDER BY created_at LIMIT 1",
        (message_id,),
    )
    link = await cursor.fetchone()
    if link is not None:
        return ReplyResult(message_id=message_id, outcome="LINKED", link=Candidate(**link))
    cursor = await conn.execute(
        "SELECT reason, candidates, status FROM integration.mail_reply_reviews WHERE message_id=%s",
        (message_id,),
    )
    review = await cursor.fetchone()
    if review is None:
        return None
    candidates = [Candidate(**c) for c in review["candidates"]]
    if review["status"] == "DISMISSED":
        return ReplyResult(
            message_id=message_id, outcome="NONE", reason=review["reason"], candidates=candidates
        )
    return ReplyResult(
        message_id=message_id, outcome="REVIEW", reason=review["reason"], candidates=candidates
    )


async def link_origin(
    settings: Settings, *, message_id: UUID, owner_service: OwnerService, business_ref: str
) -> None:
    """업무를 만든 원 메일을 기록한다(예: 메일로 접수한 C/S 문의, KAN-110). 같은 연결은 한 번만.

    이 메일의 스레드·헤더로 오는 회신이 이 업무로 연결된다.

    Args:
        settings: 플랫폼 DB 설정.
        message_id: 받은 메일 ID.
        owner_service: 업무 서비스.
        business_ref: 업무 객체 참조.

    Returns:
        None. 형식이 틀리면 422, 메일이 없으면 404.
    """
    if not BUSINESS_REF.fullmatch(business_ref):
        raise ApplicationError(422, "MAIL_BUSINESS_REF_INVALID", "Business reference is invalid.")
    async with connection(settings) as conn, conn.transaction():
        cursor = await conn.execute(
            "SELECT 1 FROM integration.mail_messages WHERE id=%s", (message_id,)
        )
        if await cursor.fetchone() is None:
            raise not_found()
        await conn.execute(
            "INSERT INTO integration.mail_links (id, message_id, owner_service, business_ref, kind, "
            "method) VALUES (%s, %s, %s, %s, 'ORIGIN', 'BUSINESS') "
            "ON CONFLICT (message_id, owner_service, business_ref) DO NOTHING",
            (uuid4(), message_id, owner_service, business_ref),
        )


async def replies_for(
    settings: Settings, owner_service: OwnerService, business_ref: str
) -> list[dict[str, Any]]:
    """업무에 연결된 회신을 오래된 순으로 읽는다(KAN-110이 문의 재개에 쓴다). 본문은 없다."""
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT l.message_id, l.method, l.created_at AS linked_at, m.received_at "
            "FROM integration.mail_links l JOIN integration.mail_messages m ON m.id=l.message_id "
            "WHERE l.owner_service=%s AND l.business_ref=%s AND l.kind='REPLY' "
            "ORDER BY m.received_at, l.message_id",
            (owner_service, business_ref),
        )
        return [dict(r) for r in await cursor.fetchall()]


# --- 메일함 주인의 회신 검토 API -------------------------------------------------------------

ACTIVATION = {"x-implementation-status": "partial", "x-activation": "MAILBOX_AUTH_ENABLED"}
router = APIRouter(
    prefix="/api/v1/platform/mail/reply-reviews",
    tags=["mail-links"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)


class ReplyReview(BaseModel):
    message_id: UUID
    received_at: datetime
    reason: Reason
    candidates: list[Candidate]
    created_at: datetime


class ReplyReviewList(BaseModel):
    items: list[ReplyReview]


class LinkDecision(BaseModel):
    owner_service: OwnerService
    business_ref: str
    note: str = Field(min_length=1, max_length=500)


class DismissDecision(BaseModel):
    note: str = Field(min_length=1, max_length=500)


async def open_review(conn: Any, message_id: UUID, owner: UUID) -> dict[str, Any]:
    """현재 사용자 메일함의 열린 검토를 잠근다. 다른 사람 것은 404, 이미 결정했으면 409."""
    cursor = await conn.execute(
        "SELECT r.candidates, r.status FROM integration.mail_reply_reviews r "
        "JOIN integration.mail_messages m ON m.id=r.message_id "
        "JOIN mailbox_auth.connections c ON c.id=m.mailbox_id "
        "WHERE r.message_id=%s AND c.owner_user_id=%s FOR UPDATE OF r",
        (message_id, owner),
    )
    row = await cursor.fetchone()
    if row is None:
        raise not_found("MAIL_REPLY_REVIEW_NOT_FOUND")
    if row["status"] != "OPEN":
        raise ApplicationError(409, "MAIL_REPLY_REVIEW_DECIDED", "The review was already decided.")
    return dict(row)


@router.get(
    "",
    response_model=SuccessResponse[ReplyReviewList],
    operation_id="platform_list_mail_reply_reviews",
    openapi_extra=ACTIVATION,
)
async def list_reviews(
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[ReplyReviewList]:
    """내 메일함의 열린 회신 검토를 오래된 순으로 최대 100건 돌려준다. 제목·본문·주소는 없다."""
    user = await operational_identity(request, response)
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT r.message_id, m.received_at, r.reason, r.candidates, r.created_at "
            "FROM integration.mail_reply_reviews r "
            "JOIN integration.mail_messages m ON m.id=r.message_id "
            "JOIN mailbox_auth.connections c ON c.id=m.mailbox_id "
            "WHERE c.owner_user_id=%s AND r.status='OPEN' ORDER BY r.created_at, r.message_id "
            "LIMIT 100",
            (user.id,),
        )
        items = [ReplyReview(**r) for r in await cursor.fetchall()]
    return SuccessResponse(
        data=ReplyReviewList(items=items), meta=ResponseMeta(request_id=request.state.request_id)
    )


@router.post(
    "/{message_id}/link",
    response_model=SuccessResponse[ReplyResult],
    operation_id="platform_link_mail_reply",
    openapi_extra=ACTIVATION,
)
async def link_reply(
    message_id: UUID,
    payload: LinkDecision,
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[ReplyResult]:
    """메일함 주인이 검토 중인 회신을 후보 업무 중 하나에 연결한다. 후보 밖 업무는 422다."""
    mutation_token(request)
    user = await operational_identity(request, response)
    async with connection(request.app.state.settings) as conn, conn.transaction():
        review = await open_review(conn, message_id, user.id)
        allowed = {(c["owner_service"], c["business_ref"]) for c in review["candidates"]}
        if (payload.owner_service, payload.business_ref) not in allowed:
            # 근거 없이 아무 업무에나 붙이지 않는다. 근거가 있는 후보만 고를 수 있다.
            raise ApplicationError(
                422, "MAIL_REPLY_NOT_A_CANDIDATE", "Choose one of the candidates."
            )
        await conn.execute(
            "INSERT INTO integration.mail_links (id, message_id, owner_service, business_ref, kind, "
            "method, decided_by, note) VALUES (%s, %s, %s, %s, 'REPLY', 'HUMAN', %s, %s)",
            (
                uuid4(),
                message_id,
                payload.owner_service,
                payload.business_ref,
                user.id,
                payload.note,
            ),
        )
        await conn.execute(
            "UPDATE integration.mail_reply_reviews SET status='LINKED', decided_by=%s, "
            "decided_at=now(), note=%s WHERE message_id=%s",
            (user.id, payload.note, message_id),
        )
        await conn.execute(
            "UPDATE integration.mail_reply_checks SET outcome='LINKED', checked_at=now() "
            "WHERE message_id=%s",
            (message_id,),
        )
    chosen = Candidate(
        owner_service=payload.owner_service, business_ref=payload.business_ref, method="HUMAN"
    )
    return SuccessResponse(
        data=ReplyResult(message_id=message_id, outcome="LINKED", link=chosen),
        meta=ResponseMeta(request_id=request.state.request_id),
    )


@router.post(
    "/{message_id}/dismiss",
    response_model=SuccessResponse[ReplyResult],
    operation_id="platform_dismiss_mail_reply",
    openapi_extra=ACTIVATION,
)
async def dismiss_reply(
    message_id: UUID,
    payload: DismissDecision,
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[ReplyResult]:
    """메일함 주인이 이 메일은 업무 회신이 아니라고 닫는다. 사유를 남긴다."""
    mutation_token(request)
    user = await operational_identity(request, response)
    async with connection(request.app.state.settings) as conn, conn.transaction():
        await open_review(conn, message_id, user.id)
        await conn.execute(
            "UPDATE integration.mail_reply_reviews SET status='DISMISSED', decided_by=%s, "
            "decided_at=now(), note=%s WHERE message_id=%s",
            (user.id, payload.note, message_id),
        )
        await conn.execute(
            "UPDATE integration.mail_reply_checks SET outcome='NONE', checked_at=now() "
            "WHERE message_id=%s",
            (message_id,),
        )
    return SuccessResponse(
        data=ReplyResult(message_id=message_id, outcome="NONE"),
        meta=ResponseMeta(request_id=request.state.request_id),
    )
