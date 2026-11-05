"""승인자가 자기 발송 결과를 보고 결과 불명 발송을 처리하는 사용자 API(KAN-74).

기본 비활성(`MAIL_SEND_ENABLED`)이며 운영 세션으로만 쓴다. 변경 요청은 사서함 연결과 같은
동일 출처·세션 결속 CSRF 검사를 거친다. 승인자 본인의 발송만 보이고, 다른 사람 것은 404다.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from backend.api.platform.auth.http import configuration, operational_identity
from backend.api.platform.auth.mailbox_http import mutation_token
from backend.api.platform.auth.schemas import Identity
from backend.api.platform.mail import send
from backend.api.platform.mail.send_worker import client, enqueue, send_task
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

PREFIX = "/api/v1/platform/mail/send-intents"
ACTIVATION = {"x-implementation-status": "partial", "x-activation": "MAIL_SEND_ENABLED"}
SendStatus = Literal["QUEUED", "SUBMITTING", "ACCEPTED", "UNKNOWN", "FAILED"]


class SendIntentSummary(BaseModel):
    intent_id: UUID
    business_ref: str
    business_version: int
    subject: str
    status: SendStatus
    error_code: str | None
    attempts: int
    created_at: datetime
    submitted_at: datetime | None
    accepted_at: datetime | None
    # 보낸편지함 사본 확인 시각. 배달 확인이 아니다.
    sent_copy_confirmed_at: datetime | None
    # 결과 불명이고 유예 뒤 "사본 없음" 확인이 있어 재발송을 허가할 수 있는지.
    resend_ready: bool


class SendIntentList(BaseModel):
    items: list[SendIntentSummary]


class SentCopyCheck(BaseModel):
    intent_id: UUID
    status: SendStatus
    sent_copy_confirmed: bool


class ResendRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ResendResult(BaseModel):
    intent_id: UUID
    status: SendStatus
    task_registered: bool


def enabled(request: Request) -> None:
    """발송이 꺼져 있으면 DB 조회 전에 404로 닫는다."""
    if request.app.state.mail_send_invoker is None:
        raise HTTPException(404)
    configuration(request)


router = APIRouter(
    prefix=PREFIX,
    tags=["mail"],
    dependencies=[Depends(enabled)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)


@router.get(
    "",
    response_model=SuccessResponse[SendIntentList],
    operation_id="platform_list_my_mail_sends",
    openapi_extra=ACTIVATION,
)
async def list_mine(
    request: Request, response: Response, status: SendStatus | None = Query(default=None)
) -> SuccessResponse[SendIntentList]:
    """내가 승인한 발송을 최근 순으로 최대 50건 돌려준다.

    Args:
        request: 운영 세션 요청.
        response: CSRF 헤더를 받을 응답.
        status: 상태로 거를 때 그 값(예: `UNKNOWN`).

    Returns:
        SuccessResponse[SendIntentList]: 제목·상태·시각·재발송 가능 여부. 본문·수신자 제외.
    """
    user: Identity = await operational_identity(request, response)
    rows = await send.list_for_approver(request.app.state.settings, user.id, status)
    items = [SendIntentSummary(intent_id=r.pop("id"), **r) for r in rows]
    return SuccessResponse(
        data=SendIntentList(items=items), meta=ResponseMeta(request_id=request.state.request_id)
    )


@router.post(
    "/{intent_id}/check-sent-copy",
    response_model=SuccessResponse[SentCopyCheck],
    operation_id="platform_check_mail_sent_copy",
    openapi_extra=ACTIVATION,
)
async def check_sent_copy(
    intent_id: UUID, request: Request, response: Response
) -> SuccessResponse[SentCopyCheck]:
    """보낸편지함에서 이 발송의 사본을 다시 찾는다. 결과 불명 판단의 근거를 남긴다.

    Args:
        intent_id: 발송 의도 ID.
        request: 동일 출처·세션 CSRF를 확인할 요청.
        response: CSRF 헤더를 받을 응답.

    Returns:
        SuccessResponse[SentCopyCheck]: 현재 상태와 사본 확인 여부.
    """
    mutation_token(request)
    user: Identity = await operational_identity(request, response)
    settings = request.app.state.settings
    await send.require_approver(settings, intent_id, user.id)
    async with client(request, httpx.Timeout(30, connect=10)) as graph_client:
        result = await send.confirm_sent_copy(
            settings,
            intent_id,
            get_token=request.app.state.mailbox_tokens.get_mailbox_access_token,
            client=graph_client,
        )
    return SuccessResponse(
        data=SentCopyCheck(
            intent_id=intent_id,
            status=result.status,  # type: ignore[arg-type]
            sent_copy_confirmed=result.sent_copy_confirmed,
        ),
        meta=ResponseMeta(request_id=request.state.request_id),
    )


@router.post(
    "/{intent_id}/resend",
    response_model=SuccessResponse[ResendResult],
    operation_id="platform_resend_mail",
    openapi_extra=ACTIVATION,
)
async def resend(
    intent_id: UUID, payload: ResendRequest, request: Request, response: Response
) -> SuccessResponse[ResendResult]:
    """결과 불명 발송을 승인자가 사유와 함께 다시 보내도록 허가하고 전송 작업을 등록한다.

    유예(10분) 뒤 보낸편지함 "사본 없음" 확인이 있어야 한다. 등록이 실패해도 의도는
    `QUEUED`이고 정리 작업이 다시 등록한다.

    Args:
        intent_id: `UNKNOWN` 발송 의도.
        payload: 판단 사유.
        request: 동일 출처·세션 CSRF를 확인할 요청.
        response: CSRF 헤더를 받을 응답.

    Returns:
        SuccessResponse[ResendResult]: `QUEUED`와 작업 등록 여부.
    """
    mutation_token(request)
    user: Identity = await operational_identity(request, response)
    settings = request.app.state.settings
    await send.require_approver(settings, intent_id, user.id)
    result = await send.authorize_resend(
        settings, intent_id, actor_user_id=user.id, reason=payload.reason
    )
    row = await send.read_intent_any(settings, intent_id)
    async with client(request, httpx.Timeout(10)) as http:
        registered = await enqueue(
            request, http, "send", send_task(intent_id, row["attempts"]), intent_id
        )
    return SuccessResponse(
        data=ResendResult(intent_id=intent_id, status=result.status, task_registered=registered),  # type: ignore[arg-type]
        meta=ResponseMeta(request_id=request.state.request_id),
    )
