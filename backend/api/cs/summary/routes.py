"""문의 원문의 한국어 요약 조회·생성(KAN-131 화면 요청). 기본 비활성.

- `GET  /api/v1/cs/tickets/{id}/summary`: 저장된 최근 요약과 최근 시도. 문의 조회 범위와 같다.
- `POST /api/v1/cs/tickets/{id}/summary`: cs_agent가 요약을 만든다. 같은 원문·프롬프트 판본의 성공한 요약이
  이미 있으면 모델을 부르지 않고 그것을 돌려준다(200). 새로 만들면 201, 실패하면 시도를 저장하고 502다.
- `CS_SUMMARY_ENABLED`가 꺼져 있으면 두 경로 모두 404다. 켜면 C/S 서비스가 모델을 직접 부른다(비용).

요약은 AI 작성물(`ai_draft`)이며 판정이 아니다. 업무 상태·분류·규칙 결과를 바꾸지 않는다.
"""

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.cs.summary import repository
from backend.api.cs.summary.schemas import SummaryAttempt, SummaryRecord, TicketSummaryView
from backend.api.cs.tickets import repository as tickets
from backend.api.cs.tickets.access import require_ticket_write
from backend.api.cs.tickets.routes import PARTIAL, check_visible, not_found, respond
from backend.engine.cs.classification_call import ClassificationCallError
from backend.engine.cs.summary import (
    SUMMARY_INPUT_VERSION,
    SUMMARY_PROMPT_VERSION,
    SummaryOutput,
    SummaryOutputError,
    build_summary_input,
    input_sha256,
    render_prompt,
    validate_output,
)
from backend.shared.access import Principal, current_principal
from backend.shared.ai import Generation, usage_cost
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind_path_ids
from backend.shared.models import ErrorResponse, SuccessResponse

Provider = Callable[[str], Generation]


class CsSummarySettings(BaseSettings):
    """요약 기능 설정. 꺼져 있으면 모델 클라이언트를 만들지 않는다."""

    model_config = SettingsConfigDict(
        env_prefix="CS_SUMMARY_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    enabled: bool = False
    model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    # thinking 토큰을 포함한 출력 상한. 분류에서 1024는 thinking으로 잘렸다(cs_runner.py).
    max_output_tokens: int = Field(default=4096, ge=256, le=65536)


def configure_summary(
    app: FastAPI, value: CsSummarySettings, provider: Provider | None = None
) -> None:
    """요약 공급자를 앱에 둔다. 꺼져 있으면 None이라 경로가 404다.

    Args:
        app: C/S 앱.
        value: 설정.
        provider: 검사용 가짜 공급자. 없고 켜져 있으면 Gemini 공급자를 만든다.
    """
    app.state.summary_model = value.model
    app.state.summary_provider = None
    if not value.enabled:
        return
    if provider is None:
        from backend.engine.cs.providers import GenerationKnobs, gemini_text_provider
        from backend.shared.ai import AISettings, create_client

        ai = AISettings()
        provider = gemini_text_provider(
            create_client(ai),
            ai,
            model=value.model,
            response_schema=SummaryOutput,
            generation=GenerationKnobs(max_output_tokens=value.max_output_tokens),
        )
    app.state.summary_provider = provider


def summary_provider(request: Request) -> Provider:
    provider = getattr(request.app.state, "summary_provider", None)
    if provider is None:
        raise HTTPException(404)
    return provider


router = APIRouter(
    prefix="/api/v1/cs/tickets/{ticket_id}/summary",
    tags=["summary"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)


def prepared(ticket: dict[str, Any]) -> tuple[Any, str]:
    """마스킹한 모델 입력과 그 판본. 입력으로 쓸 수 없는 문의(빈 본문 등)는 422."""
    try:
        payload = build_summary_input(ticket)
    except ValueError:
        raise ApplicationError(
            422, "CS_SUMMARY_INPUT_INVALID", "The ticket text cannot be summarized."
        ) from None
    return payload, input_sha256(payload)


def record(row: dict[str, Any], current_sha: str) -> SummaryRecord:
    stale = row["input_sha256"] != current_sha or row["prompt_version"] != SUMMARY_PROMPT_VERSION
    return SummaryRecord.model_validate({**row, "stale": stale})


def attempt(row: dict[str, Any] | None) -> SummaryAttempt | None:
    return SummaryAttempt.model_validate(row) if row else None


async def load(conn: Any, principal: Principal, ticket_id: UUID) -> dict[str, Any]:
    await check_visible(conn, principal, ticket_id)
    ticket = await tickets.get_ticket(conn, ticket_id)
    if ticket is None:
        raise not_found()
    return ticket


@router.get(
    "",
    response_model=SuccessResponse[TicketSummaryView],
    operation_id="cs_get_ticket_summary",
    openapi_extra={**PARTIAL, "x-activation": "CS_SUMMARY_ENABLED"},
)
async def get_summary(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[TicketSummaryView]:
    """저장된 한국어 요약. 만들지는 않는다(비용 없음)."""
    summary_provider(request)
    async with connection(request.app.state.settings) as conn:
        ticket = await load(conn, principal, ticket_id)
        _, sha = prepared(ticket)
        valid = await repository.latest(conn, ticket_id, valid=True)
        last = await repository.latest(conn, ticket_id, valid=False)
    view = TicketSummaryView(
        ticket_id=ticket_id,
        summary=record(valid, sha) if valid else None,
        latest_attempt=attempt(last),
    )
    return respond(request, view)


@router.post(
    "",
    response_model=SuccessResponse[TicketSummaryView],
    status_code=201,
    operation_id="cs_create_ticket_summary",
    responses={200: {"model": SuccessResponse[TicketSummaryView]}},
    openapi_extra={**PARTIAL, "x-activation": "CS_SUMMARY_ENABLED"},
)
async def create_summary(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> Any:
    """한국어 요약을 만든다. 같은 원문의 성공한 요약이 있으면 그대로 돌려준다(200, 모델 호출 없음)."""
    provider = summary_provider(request)
    async with connection(request.app.state.settings) as conn:
        # permissions.md 1.3: 범위(404) 다음 역할(403).
        ticket = await load(conn, principal, ticket_id)
        require_ticket_write(principal)
        payload, sha = prepared(ticket)
        existing = await repository.matching(conn, ticket_id, sha, SUMMARY_PROMPT_VERSION)
        last = await repository.latest(conn, ticket_id, valid=False)
    if existing:
        view = TicketSummaryView(
            ticket_id=ticket_id, summary=record(existing, sha), latest_attempt=attempt(last)
        )
        return JSONResponse(respond(request, view).model_dump(mode="json"), status_code=200)
    model = request.app.state.summary_model
    values: dict[str, Any] = {
        "id": uuid4(),
        "ticket_id": ticket_id,
        "input_version": SUMMARY_INPUT_VERSION,
        "input_sha256": sha,
        "prompt_version": SUMMARY_PROMPT_VERSION,
        "requested_model": model,
        "requested_by": principal.user_id,
    }
    # DB 연결을 잡은 채 모델을 기다리지 않는다.
    try:
        generation = await asyncio.to_thread(provider, render_prompt(payload))
    except ClassificationCallError as error:
        values.update(outcome="PROVIDER_ERROR", error_code=error.code)
    else:
        cost, pricing = usage_cost(generation.usage, model)
        usage = generation.usage
        values.update(
            model_version=generation.model_version,
            latency_ms=generation.elapsed_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            thought_tokens=usage.thought_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            total_tokens=usage.total_tokens,
            cost_usd=cost,
            pricing_version=pricing,
        )
        try:
            output = validate_output(generation.text, source=payload)
        except SummaryOutputError as error:
            values.update(outcome="INVALID_OUTPUT", error_code=error.code)
        else:
            values.update(outcome="VALID", summary_ko=output.summary_ko)
    async with connection(request.app.state.settings) as conn:
        stored = await repository.insert(conn, values)
    if stored["outcome"] != "VALID":
        raise ApplicationError(
            502,
            "CS_SUMMARY_FAILED",
            "The summary could not be made. Try again later.",
            [{"code": str(stored["error_code"])}],
        )
    view = TicketSummaryView(
        ticket_id=ticket_id, summary=record(stored, sha), latest_attempt=attempt(stored)
    )
    return respond(request, view)
