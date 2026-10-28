"""수집한 메일의 업무 유형 분류 저장·조회·사람 재분류(KAN-73).

분류는 추천일 뿐 문의·프로젝트에 자동으로 연결하거나 접수하지 않는다. 판본마다 새 행을 쓰고
고치거나 지우지 않는다. 메일 원문·본문·주소는 저장하거나 응답하지 않는다. 메일함 소유자만 본다.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.platform.auth.http import operational_identity
from backend.api.platform.auth.mailbox_http import mailbox_provider, mutation_token
from backend.api.platform.auth.microsoft import PersonalMicrosoftOAuth
from backend.engine.mail_triage.extract import AttachmentSummary, TriageInput, build_input
from backend.engine.mail_triage.triage import (
    ALLOWED_LINKS,
    Evidence,
    LinkSuggestion,
    MailType,
    triage,
)
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse
from backend.shared.settings import Settings

ACTIVATION = {"x-implementation-status": "partial", "x-activation": "MAILBOX_AUTH_ENABLED"}
router = APIRouter(
    prefix="/api/v1/platform/mail/messages/{message_id}/triage",
    tags=["mail-triage"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Unknown message or not the mailbox owner's"},
        409: {"model": ErrorResponse, "description": "Stale triage version"},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
Row = dict[str, Any]


class MailTriage(BaseModel):
    """메일 한 통의 분류 판본. 원문·본문·주소는 담지 않는다."""

    message_id: UUID
    version: int
    latest_version: int
    is_latest: bool
    mail_type: MailType
    needs_review: bool
    attachments_pending: bool
    evidence: list[Evidence]
    link_suggestion: LinkSuggestion | None
    provenance_kind: Literal["ai_draft", "human_input"]
    reason: str | None
    created_at: datetime


class Reclassification(BaseModel):
    """메일함 소유자의 재분류. 읽은 판본과 사유가 필요하다."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, description="The triage version the user decided on.")
    mail_type: MailType
    link_suggestion: LinkSuggestion | None = None
    needs_review: bool = False
    reason: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def link_fits_type(self) -> "Reclassification":
        """연결 추천이 유형에 허용된 값인지 확인한다.

        Returns:
            Reclassification: 검사를 통과한 본문.
        """
        if self.link_suggestion and self.link_suggestion not in ALLOWED_LINKS[self.mail_type]:
            raise ValueError("the link suggestion does not fit the mail type")
        return self


def not_found(code: str = "MAIL_MESSAGE_NOT_FOUND") -> ApplicationError:
    """메일이 없거나 다른 사람 메일함이거나 분류가 없을 때의 404를 만든다.

    Args:
        code: MAIL_MESSAGE_NOT_FOUND 또는 MAIL_TRIAGE_NOT_FOUND.

    Returns:
        ApplicationError: 존재를 숨기는 404.
    """
    return ApplicationError(404, code, "The mail triage was not found.")


async def _insert(conn: Any, message_id: UUID, values: Row, expected: int | None) -> int:
    """메일의 최신 판본을 잠그고 다음 판본을 추가한 뒤 포인터를 옮긴다.

    Args:
        conn: 플랫폼 DB 트랜잭션.
        message_id: 메일 ID.
        values: 판본 열(유형·검토·첨부 대기·근거·추천·주체·판본·사유·판정자).
        expected: 사람 재분류가 읽은 판본. 모델 분류는 None(항상 다음 판본).

    Returns:
        int: 새 판본 번호. 읽은 판본이 최신이 아니면 409 MAIL_TRIAGE_VERSION_CONFLICT.
    """
    cursor = await conn.execute(
        "SELECT latest_version FROM integration.mail_triage_latest WHERE message_id=%s FOR UPDATE",
        (message_id,),
    )
    row = await cursor.fetchone()
    latest = row["latest_version"] if row else 0
    if expected is not None and expected != latest:
        raise ApplicationError(409, "MAIL_TRIAGE_VERSION_CONFLICT", "The triage has changed.")
    version = latest + 1
    # 첫 분류가 동시에 둘이면 잠글 포인터가 없다. 같은 판본을 먼저 쓴 쪽만 남고 나머지는 409다.
    cursor = await conn.execute(
        "INSERT INTO integration.mail_triage (message_id, version, mail_type, needs_review, "
        "attachments_pending, evidence, link_suggestion, provenance_kind, input_version, "
        "output_version, reason, decided_by) VALUES (%(message_id)s, %(version)s, %(mail_type)s, "
        "%(needs_review)s, %(attachments_pending)s, %(evidence)s, %(link_suggestion)s, "
        "%(provenance_kind)s, %(input_version)s, %(output_version)s, %(reason)s, %(decided_by)s) "
        "ON CONFLICT (message_id, version) DO NOTHING RETURNING version",
        values
        | {"message_id": message_id, "version": version, "evidence": Jsonb(values["evidence"])},
    )
    if await cursor.fetchone() is None:
        raise ApplicationError(409, "MAIL_TRIAGE_VERSION_CONFLICT", "The triage has changed.")
    if row:
        await conn.execute(
            "UPDATE integration.mail_triage_latest SET latest_version=%s WHERE message_id=%s",
            (version, message_id),
        )
    else:
        await conn.execute(
            "INSERT INTO integration.mail_triage_latest (message_id, latest_version) "
            "VALUES (%s, %s)",
            (message_id, version),
        )
    return version


async def classify_message(
    settings: Settings, *, message_id: UUID, raw: bytes, provider: Callable[[TriageInput], str]
) -> int:
    """저장된 메일 한 통을 분류해 모델 판본(ai_draft)으로 저장한다. 수집 코드와는 연결하지 않는다.

    원문은 호출자가 객체 저장소에서 읽어 넘기고, 저장된 SHA-256과 같아야 한다. 제공자 호출은
    트랜잭션 밖에서 한다.

    Args:
        settings: 플랫폼 DB 설정.
        message_id: 수집 표의 메일 ID.
        raw: 그 메일의 원문 MIME 바이트.
        provider: 입력을 받아 JSON 문자열을 돌려주는 주입 제공자(현재는 합성만).

    Returns:
        int: 새 판본 번호. 메일이 없으면 404, 원문이 다르면 409 MAIL_TRIAGE_RAW_MISMATCH,
        같은 메일의 첫 분류가 동시에 들어와 진 쪽은 409 MAIL_TRIAGE_VERSION_CONFLICT.
    """
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT folder, raw_sha256 FROM integration.mail_messages WHERE id=%s", (message_id,)
        )
        message = await cursor.fetchone()
        if message is None:
            raise not_found()
        cursor = await conn.execute(
            "SELECT kind, content_type, is_inline FROM integration.mail_attachments "
            "WHERE message_id=%s ORDER BY id",
            (message_id,),
        )
        attachments = tuple(AttachmentSummary(**a) for a in await cursor.fetchall())
    if hashlib.sha256(raw).hexdigest() != message["raw_sha256"]:
        raise ApplicationError(409, "MAIL_TRIAGE_RAW_MISMATCH", "The raw message does not match.")
    result = triage(build_input(raw, folder=message["folder"], attachments=attachments), provider)
    values = result.model_dump(mode="json") | {
        "provenance_kind": "ai_draft",
        "reason": None,
        "decided_by": None,
    }
    async with connection(settings) as conn:
        return await _insert(conn, message_id, values, None)


async def _owned(conn: Any, message_id: UUID, owner: UUID) -> None:
    """메일이 현재 사용자 메일함의 것인지 확인한다(다른 사람 것은 존재를 숨긴다).

    Args:
        conn: 플랫폼 DB 연결.
        message_id: 메일 ID.
        owner: 세션 사용자 ID.

    Returns:
        None: 아니면 404 MAIL_MESSAGE_NOT_FOUND.
    """
    cursor = await conn.execute(
        "SELECT 1 FROM integration.mail_messages m JOIN mailbox_auth.connections c "
        "ON c.id=m.mailbox_id WHERE m.id=%s AND c.owner_user_id=%s",
        (message_id, owner),
    )
    if await cursor.fetchone() is None:
        raise not_found()


async def _read(conn: Any, message_id: UUID, version: int | None) -> MailTriage:
    """메일의 분류 판본 하나를 읽는다.

    Args:
        conn: 플랫폼 DB 연결.
        message_id: 메일 ID.
        version: 판본. None이면 최신.

    Returns:
        MailTriage: 그 판본. 분류나 판본이 없으면 404 MAIL_TRIAGE_NOT_FOUND.
    """
    cursor = await conn.execute(
        "SELECT t.*, l.latest_version FROM integration.mail_triage_latest l "
        "JOIN integration.mail_triage t ON t.message_id=l.message_id "
        "AND t.version=COALESCE(%s, l.latest_version) WHERE l.message_id=%s",
        (version, message_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise not_found("MAIL_TRIAGE_NOT_FOUND")
    return MailTriage(**row, is_latest=row["version"] == row["latest_version"])


@router.get(
    "",
    response_model=SuccessResponse[MailTriage],
    operation_id="platform_get_mail_triage",
    openapi_extra=ACTIVATION,
)
async def get_triage(
    message_id: UUID,
    request: Request,
    response: Response,
    version: Annotated[int | None, Query(ge=1)] = None,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[MailTriage]:
    """메일함 소유자가 자기 메일의 분류(최신 또는 지정 판본)를 읽는다.

    Args:
        message_id: 메일 ID.
        request: 운영 세션 쿠키를 포함한 요청.
        response: CSRF 헤더를 받을 응답.
        version: 판본. 생략하면 최신.
        provider: 사서함 기능 비활성이면 404로 닫는 의존성. 공급자를 부르지 않는다.

    Returns:
        SuccessResponse[MailTriage]: 분류 판본.
    """
    user = await operational_identity(request, response)
    async with connection(request.app.state.settings) as conn:
        await _owned(conn, message_id, user.id)
        data = await _read(conn, message_id, version)
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))


@router.post(
    "",
    response_model=SuccessResponse[MailTriage],
    operation_id="platform_reclassify_mail",
    openapi_extra=ACTIVATION,
)
async def reclassify(
    message_id: UUID,
    payload: Reclassification,
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[MailTriage]:
    """메일함 소유자가 유형을 다시 정한다. 사람 판본(human_input)으로 새로 쓴다.

    근거는 사람 판단이라 비우고, 첨부 처리 대기는 이전 판본 값을 잇는다. 문의 접수·업무
    연결은 하지 않는다.

    Args:
        message_id: 메일 ID.
        payload: 읽은 판본·유형·연결 추천·검토 필요·사유.
        request: 동일 출처·세션 CSRF를 확인할 요청.
        response: CSRF 헤더를 받을 응답.
        provider: 사서함 기능 비활성이면 404로 닫는 의존성.

    Returns:
        SuccessResponse[MailTriage]: 새 최신 판본. 읽은 판본이 아니면 409.
    """
    mutation_token(request)
    user = await operational_identity(request, response)
    async with connection(request.app.state.settings) as conn:
        await _owned(conn, message_id, user.id)
        previous = await _read(conn, message_id, None)
        version = await _insert(
            conn,
            message_id,
            {
                "mail_type": payload.mail_type,
                "needs_review": payload.needs_review,
                "attachments_pending": previous.attachments_pending,
                "evidence": [],
                "link_suggestion": payload.link_suggestion,
                "provenance_kind": "human_input",
                "input_version": None,
                "output_version": None,
                "reason": payload.reason,
                "decided_by": user.id,
            },
            payload.version,
        )
        data = await _read(conn, message_id, version)
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))
