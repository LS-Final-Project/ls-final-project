"""RFP로 보이는 메일 알아보기와 담당자 확인 후 등록(KAN-73, 2026-11-05 사용자 결정 "확인 후 등록").

1. 수집 워커가 수집 뒤 `triage_new_rfp_mails`를 부른다. 아직 분류가 없는 받은 메일 가운데 제목이나 첨부
   이름에 RFP 표현이 있고 DOCX·PDF 첨부가 있는 메일만 원문을 읽어, 규칙으로 NEW_RFP 분류 판본을 남긴다.
   모델을 부르지 않는다(비용 없음). 근거는 마스킹한 제목·본문에서 표현이 나온 글자 구간이다.
   ponytail: 규칙은 제목·첨부 이름의 표현만 본다. 놓치는 메일이 보이면 모델 분류(worker 작업)를 붙인다.
2. 담당 영업은 `GET /rfp-candidates`로 아직 프로젝트에 연결하지 않은 NEW_RFP 메일을 보고, 첨부를 받아
   화면에서 TFT 프로젝트를 만들고 문서를 올린 뒤 `POST /messages/{id}/rfp-project`로 메일을 프로젝트에
   연결한다. 분석 시작은 하지 않는다. 자동 접수는 하지 않는다(분류는 추천).
3. [RFP 아님]은 기존 사람 재분류(triage POST)를 쓴다.

연결은 `mail_links`(owner_service='platform', business_ref='rfp_project:<프로젝트 ID>', ORIGIN)다.
플랫폼은 tft 스키마를 읽지 않으므로 프로젝트 존재는 화면이 만든 직후의 ID를 믿는다.
"""

import json
import re
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel, ConfigDict

from backend.api.platform.auth.context import load_session_principal
from backend.api.platform.auth.http import SESSION_COOKIE, operational_identity
from backend.api.platform.auth.mailbox_http import mailbox_provider, mutation_token
from backend.api.platform.auth.microsoft import PersonalMicrosoftOAuth
from backend.api.platform.mail.intake import raw_store
from backend.api.platform.mail.links import not_found
from backend.api.platform.mail.storage import StoredObject
from backend.api.platform.mail.triage import classify_message
from backend.engine.mail_triage.extract import TriageInput, build_input
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse
from backend.shared.settings import Settings

ACTIVATION = {"x-implementation-status": "partial", "x-activation": "MAILBOX_AUTH_ENABLED"}
# 제안요청서·입찰 메일에 흔한 표현(대소문자 무시). 제목·첨부 이름·본문에서 찾는다.
RFP_WORDS = r"RFP|RFQ|제안\s*요청|입찰|기술\s*규격|규격서|견적\s*요청|request\s+for\s+(?:proposal|quotation)|tender"
RFP_PATTERN = re.compile(RFP_WORDS, re.IGNORECASE)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FILE_TYPES = {DOCX: ".docx", "application/pdf": ".pdf"}
REF_PREFIX = "rfp_project:"
router = APIRouter(
    prefix="/api/v1/platform/mail",
    tags=["mail-rfp-intake"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)


def rfp_file(name: str | None, content_type: str | None) -> bool:
    """첨부가 RFP 문서로 올릴 수 있는 DOCX·PDF인지 형식이나 확장자로 본다."""
    lowered = (name or "").lower()
    return (content_type or "").split(";", 1)[0].strip().lower() in FILE_TYPES or lowered.endswith(
        (".docx", ".pdf")
    )


def rule_output(triage_input: TriageInput) -> str:
    """규칙 분류 출력(JSON). 후보는 DB가 이미 골랐으므로 언제나 판본을 남겨 다시 보지 않게 한다.

    회신이면 CANDIDATE_REPLY(회신 헤더 근거), 아니면 NEW_RFP다. 표현이 첨부 이름에만 있으면 제목·본문
    근거가 없어 엔진이 사람 검토 필요로 둔다.

    Args:
        triage_input: 마스킹한 분류 입력.

    Returns:
        str: 엔진 출력 계약(mail_type·needs_review·evidence·link_suggestion) JSON.
    """
    if triage_input.is_reply:
        return json.dumps(
            {
                "mail_type": "CANDIDATE_REPLY",
                "needs_review": True,
                "evidence": [{"source": "reply_header"}],
                "link_suggestion": "EXISTING_THREAD",
            }
        )
    evidence = [
        {"source": source, "start": match.start(), "end": match.end()}
        for source, text in (("subject", triage_input.subject), ("body", triage_input.body))
        for match in list(RFP_PATTERN.finditer(text))[:2]
    ]
    return json.dumps(
        {
            "mail_type": "NEW_RFP",
            "needs_review": False,
            "evidence": evidence[:10],
            "link_suggestion": "NEW_PROJECT",
        }
    )


async def triage_new_rfp_mails(
    settings: Settings, mailbox_id: UUID, store: Any, limit: int = 20
) -> dict[str, int]:
    """분류가 없는 받은 메일 중 RFP로 보이는 것만 규칙 분류 판본을 남긴다. 수집 워커가 수집 뒤 부른다.

    원문을 읽기 전에 DB에서 거른다: 제목이나 첨부 이름에 RFP 표현이 있고 DOCX·PDF 첨부가 있는 메일.
    걸러진 메일은 회신이면 CANDIDATE_REPLY, 아니면 NEW_RFP 판본이 남아 다시 보지 않는다(멱등).
    원문을 못 읽은 메일만 다음 수집에서 다시 본다. 한 통이 실패해도 나머지를 계속한다.

    Args:
        settings: 플랫폼 DB 설정.
        mailbox_id: 사서함 연결 ID.
        store: 원문 저장소(`ObjectReader`).
        limit: 한 번에 볼 최대 수.

    Returns:
        dict[str, int]: {"classified", "skipped", "failed"}.
    """
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT m.id, m.folder, m.raw_storage_key, m.raw_sha256, m.raw_size "
            "FROM integration.mail_messages m WHERE m.mailbox_id=%(mailbox)s AND m.folder='inbox' "
            "AND m.removed_at IS NULL AND NOT EXISTS (SELECT 1 FROM integration.mail_triage_latest t "
            "WHERE t.message_id=m.id) AND EXISTS (SELECT 1 FROM integration.mail_attachments a "
            "WHERE a.message_id=m.id AND a.kind='file' AND NOT a.is_inline AND (a.content_type = ANY("
            "%(types)s) OR lower(a.name) ~ '\\.(docx|pdf)$')) AND (m.subject ~* %(words)s OR EXISTS ("
            "SELECT 1 FROM integration.mail_attachments a WHERE a.message_id=m.id AND a.name ~* %(words)s)) "
            "ORDER BY m.received_at DESC, m.id LIMIT %(limit)s",
            {"mailbox": mailbox_id, "types": list(FILE_TYPES), "words": RFP_WORDS, "limit": limit},
        )
        pending = await cursor.fetchall()
    counts = {"classified": 0, "skipped": 0, "failed": 0}
    for row in pending:
        try:
            raw = await store.get(
                StoredObject(row["raw_storage_key"], row["raw_sha256"], row["raw_size"])
            )
            # 규칙 판단은 엔진과 같은 입력으로 한다(첨부 요약은 분류 저장 때 다시 읽는다).
            output = rule_output(build_input(raw, folder=row["folder"], attachments=()))
            await classify_message(
                settings, message_id=row["id"], raw=raw, provider=lambda _input: output
            )
            counts["classified"] += 1
        except Exception:  # noqa: BLE001 - 한 통의 실패(저장소 I/O 포함)가 수집 결과를 막지 않는다.
            counts["failed"] += 1
    return counts


class CandidateAttachment(BaseModel):
    id: UUID
    name: str | None
    content_type: str | None
    size: int | None


class RfpCandidate(BaseModel):
    """RFP로 보이는 메일 한 통. 본문은 담지 않는다."""

    message_id: UUID
    triage_version: int
    subject: str | None
    from_address: str | None
    received_at: Any
    attachments: list[CandidateAttachment]
    # 분류 근거 한 줄: 제목에서 찾은 표현, 없으면 첨부 이름.
    basis: str | None


class RfpCandidateList(BaseModel):
    items: list[RfpCandidate]


class LinkProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID


class LinkedProject(BaseModel):
    message_id: UUID
    project_id: UUID
    created: bool


async def _sales(request: Request) -> None:
    """RFP 등록은 영업(sales)만 한다. 아니면 403."""
    principal = await load_session_principal(
        request.app.state.settings, request.cookies.get(SESSION_COOKIE)
    )
    if not principal.has("sales"):
        raise HTTPException(403)


def _basis(subject: str | None, names: list[str | None]) -> str | None:
    found = RFP_PATTERN.search(subject or "")
    if found:
        return f"제목: {found.group(0)}"
    for name in names:
        if name and RFP_PATTERN.search(name):
            return f"첨부 이름: {name}"
    return None


@router.get(
    "/rfp-candidates",
    response_model=SuccessResponse[RfpCandidateList],
    operation_id="platform_list_mail_rfp_candidates",
    openapi_extra=ACTIVATION,
)
async def list_candidates(
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[RfpCandidateList]:
    """내 메일함에서 최신 분류가 NEW_RFP이고 아직 프로젝트에 연결하지 않은 받은 메일(최근 50통).

    Args:
        request: 운영 세션 요청.
        response: CSRF 헤더를 받을 응답.
        provider: 사서함 기능 비활성이면 404로 닫는 의존성.

    Returns:
        SuccessResponse[RfpCandidateList]: 최근 받은 순.
    """
    user = await operational_identity(request, response)
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT m.id, l.latest_version, m.subject, m.from_address, m.received_at "
            "FROM integration.mail_messages m JOIN mailbox_auth.connections c ON c.id=m.mailbox_id "
            "JOIN integration.mail_triage_latest l ON l.message_id=m.id "
            "JOIN integration.mail_triage t ON t.message_id=l.message_id AND t.version=l.latest_version "
            "WHERE c.owner_user_id=%s AND m.folder='inbox' AND m.removed_at IS NULL "
            "AND t.mail_type='NEW_RFP' AND NOT EXISTS (SELECT 1 FROM integration.mail_links k "
            "WHERE k.message_id=m.id AND k.owner_service='platform' AND k.business_ref LIKE %s) "
            "ORDER BY m.received_at DESC, m.id LIMIT 50",
            (user.id, REF_PREFIX + "%"),
        )
        rows = await cursor.fetchall()
        files: dict[UUID, list[CandidateAttachment]] = {r["id"]: [] for r in rows}
        if rows:
            cursor = await conn.execute(
                "SELECT id, message_id, name, content_type, size FROM integration.mail_attachments "
                "WHERE message_id = ANY(%s) AND kind='file' AND NOT is_inline ORDER BY name, id",
                ([r["id"] for r in rows],),
            )
            for a in await cursor.fetchall():
                if rfp_file(a["name"], a["content_type"]):
                    files[a["message_id"]].append(CandidateAttachment(**a))
    items = [
        RfpCandidate(
            message_id=r["id"],
            triage_version=r["latest_version"],
            subject=r["subject"],
            from_address=r["from_address"],
            received_at=r["received_at"],
            attachments=files[r["id"]],
            basis=_basis(r["subject"], [f.name for f in files[r["id"]]]),
        )
        for r in rows
    ]
    return SuccessResponse(
        data=RfpCandidateList(items=items), meta=ResponseMeta(request_id=request.state.request_id)
    )


@router.get(
    "/messages/{message_id}/attachments/{attachment_id}",
    operation_id="platform_get_mail_attachment",
    openapi_extra=ACTIVATION,
    response_class=RawResponse,
    responses={200: {"content": {DOCX: {}, "application/pdf": {}}}},
)
async def get_attachment(
    message_id: UUID,
    attachment_id: UUID,
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> RawResponse:
    """내 받은 메일의 DOCX·PDF 첨부를 내려준다(RFP 등록 화면이 프로젝트 문서로 올릴 때 쓴다).

    다른 사람 메일함·없는 첨부·DOCX·PDF가 아닌 첨부는 404다.

    Args:
        message_id: 메일 ID.
        attachment_id: 첨부 ID.
        request: 운영 세션 요청.
        response: CSRF 헤더를 받을 응답.
        provider: 사서함 기능 비활성이면 404로 닫는 의존성.

    Returns:
        RawResponse: 첨부 바이트(Content-Disposition attachment).
    """
    user = await operational_identity(request, response)
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT a.name, a.content_type, a.storage_key, a.sha256, a.size "
            "FROM integration.mail_attachments a JOIN integration.mail_messages m ON m.id=a.message_id "
            "JOIN mailbox_auth.connections c ON c.id=m.mailbox_id WHERE a.id=%s AND a.message_id=%s "
            "AND c.owner_user_id=%s AND m.removed_at IS NULL AND a.kind='file' AND NOT a.is_inline",
            (attachment_id, message_id, user.id),
        )
        row = await cursor.fetchone()
    if row is None or row["storage_key"] is None or not rfp_file(row["name"], row["content_type"]):
        raise not_found("MAIL_ATTACHMENT_NOT_FOUND")
    async with raw_store(request) as store:
        if store is None:
            raise HTTPException(404)
        data = await store.get(StoredObject(row["storage_key"], row["sha256"], row["size"]))
    name = row["name"] or "attachment"
    media = DOCX if name.lower().endswith(".docx") else "application/pdf"
    if (row["content_type"] or "").split(";", 1)[0].strip().lower() in FILE_TYPES:
        media = row["content_type"].split(";", 1)[0].strip().lower()
    disposition = "attachment; filename*=UTF-8''" + quote(name, safe="")
    return RawResponse(
        content=data,
        media_type=media,
        headers={"Content-Disposition": disposition, "Cache-Control": "no-store"},
    )


@router.post(
    "/messages/{message_id}/rfp-project",
    response_model=SuccessResponse[LinkedProject],
    status_code=201,
    operation_id="platform_link_mail_rfp_project",
    openapi_extra=ACTIVATION,
)
async def link_project(
    message_id: UUID,
    payload: LinkProject,
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[LinkedProject]:
    """담당 영업이 이 메일로 만든 RFP 프로젝트를 메일에 연결한다(이후 회신이 이 프로젝트로 묶인다).

    같은 프로젝트로 다시 부르면 200(두 번 기록하지 않음), 이미 다른 프로젝트로 등록한 메일은 409
    MAIL_RFP_ALREADY_REGISTERED(details에 그 프로젝트 ID). 다른 사람 메일함·보낸편지함은 404.

    Args:
        message_id: 받은 메일 ID.
        payload: 화면이 방금 만든 TFT 프로젝트 ID.
        request: 동일 출처·세션 CSRF를 확인할 요청.
        response: 상태 코드를 바꿀 응답.
        provider: 사서함 기능 비활성이면 404로 닫는 의존성.

    Returns:
        SuccessResponse[LinkedProject]: 연결 결과.
    """
    mutation_token(request)
    user = await operational_identity(request, response)
    await _sales(request)
    settings = request.app.state.settings
    ref = f"{REF_PREFIX}{payload.project_id}"
    async with connection(settings) as conn, conn.transaction():
        # 같은 메일의 동시 등록(더블클릭·두 탭)을 직렬화해 메일당 프로젝트 하나를 지킨다.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(message_id),))
        cursor = await conn.execute(
            "SELECT 1 FROM integration.mail_messages m JOIN mailbox_auth.connections c "
            "ON c.id=m.mailbox_id WHERE m.id=%s AND c.owner_user_id=%s AND m.folder='inbox' "
            "AND m.removed_at IS NULL",
            (message_id, user.id),
        )
        if await cursor.fetchone() is None:
            raise not_found()
        cursor = await conn.execute(
            "SELECT business_ref FROM integration.mail_links WHERE message_id=%s "
            "AND owner_service='platform' AND business_ref LIKE %s ORDER BY created_at LIMIT 1",
            (message_id, REF_PREFIX + "%"),
        )
        existing = await cursor.fetchone()
        if existing is not None and existing["business_ref"] != ref:
            raise ApplicationError(
                409,
                "MAIL_RFP_ALREADY_REGISTERED",
                "The mail is already registered as an RFP project.",
                details=[
                    {"field": "project_id", "reason": existing["business_ref"][len(REF_PREFIX) :]}
                ],
            )
        if existing is None:
            await conn.execute(
                "INSERT INTO integration.mail_links (id, message_id, owner_service, business_ref, "
                "kind, method) VALUES (%s, %s, 'platform', %s, 'ORIGIN', 'BUSINESS') "
                "ON CONFLICT (message_id, owner_service, business_ref) DO NOTHING",
                (uuid4(), message_id, ref),
            )
    response.status_code = 201 if existing is None else 200
    return SuccessResponse(
        data=LinkedProject(
            message_id=message_id, project_id=payload.project_id, created=existing is None
        ),
        meta=ResponseMeta(request_id=request.state.request_id),
    )
