"""문의 원문의 한국어 번역 조회·생성(문의 처리 화면의 원문/한국어 전환). 기본 비활성.

요약(`backend/api/cs/summary/routes.py`)과 같은 흐름이다.
- `GET  /api/v1/cs/tickets/{id}/translation`: 저장된 최근 번역과 최근 시도. 문의 조회 범위와 같다.
- `POST /api/v1/cs/tickets/{id}/translation`: cs_agent가 번역을 만든다. 같은 원문·프롬프트 판본의 성공한 번역이
  이미 있으면 모델을 부르지 않고 그것을 돌려준다(200). 새로 만들면 201, 실패하면 시도를 저장하고 502다.
  원문이 이미 한국어면 모델을 부르지 않고 422 `CS_TRANSLATION_NOT_NEEDED`다.
- `CS_TRANSLATION_ENABLED`가 꺼져 있으면 두 경로 모두 404다. 켜면 C/S 서비스가 모델을 직접 부른다(비용).

번역은 AI 작성물(`ai_draft`)이며 판정이 아니다. 업무 상태·분류·규칙 결과를 바꾸지 않는다.
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.cs.summary.routes import load, usage_values
from backend.api.cs.tickets.access import require_ticket_write
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.api.cs.translation import repository
from backend.api.cs.translation.schemas import (
    TicketTranslationView,
    TranslationAttempt,
    TranslationRecord,
)
from backend.engine.cs.classification_call import ClassificationCallError
from backend.engine.cs.translation import (
    TRANSLATION_INPUT_VERSION,
    TRANSLATION_PROMPT_VERSION,
    TranslationOutput,
    TranslationOutputError,
    build_translation_input,
    input_sha256,
    needs_translation,
    render_prompt,
    validate_output,
)
from backend.shared.access import Principal, current_principal
from backend.shared.ai import Generation
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind_path_ids
from backend.shared.models import ErrorResponse, SuccessResponse

Provider = Callable[[str], Generation]


class CsTranslationSettings(BaseSettings):
    """번역 기능 설정. 꺼져 있으면 모델 클라이언트를 만들지 않는다."""

    model_config = SettingsConfigDict(
        env_prefix="CS_TRANSLATION_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    enabled: bool = False
    model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    # thinking 토큰을 포함한 출력 상한. 본문 전체를 옮기므로 요약(4096)보다 크게 둔다.
    max_output_tokens: int = Field(default=16384, ge=256, le=65536)
    # 동시에 진행하는 번역 모델 호출 수(프로세스당).
    max_concurrent_calls: int = Field(default=4, ge=1, le=32)


def configure_translation(
    app: FastAPI, value: CsTranslationSettings, provider: Provider | None = None
) -> None:
    """번역 공급자를 앱에 둔다. 꺼져 있으면 None이라 경로가 404다.

    Args:
        app: C/S 앱.
        value: 설정.
        provider: 검사용 가짜 공급자. 없고 켜져 있으면 Gemini 공급자를 만든다.
    """
    app.state.translation_model = value.model
    app.state.translation_provider = None
    # 모델 호출이 기본 스레드 풀을 다 차지하지 않게 번역 전용 풀을 둔다.
    app.state.translation_executor = ThreadPoolExecutor(
        max_workers=value.max_concurrent_calls, thread_name_prefix="cs-translation"
    )
    # 같은 문의·원문에 동시에 온 생성 요청은 한 번만 모델을 부르도록 프로세스 안에서 줄 세운다.
    app.state.translation_locks = {}
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
            response_schema=TranslationOutput,
            generation=GenerationKnobs(max_output_tokens=value.max_output_tokens),
        )
    app.state.translation_provider = provider


def translation_provider(request: Request) -> Provider:
    provider = getattr(request.app.state, "translation_provider", None)
    if provider is None:
        raise HTTPException(404)
    return provider


router = APIRouter(
    prefix="/api/v1/cs/tickets/{ticket_id}/translation",
    tags=["translation"],
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
        payload = build_translation_input(ticket)
    except ValueError:
        raise ApplicationError(
            422, "CS_TRANSLATION_INPUT_INVALID", "The ticket text cannot be translated."
        ) from None
    return payload, input_sha256(payload)


def record(row: dict[str, Any], current_sha: str | None) -> TranslationRecord:
    stale = (
        row["input_sha256"] != current_sha or row["prompt_version"] != TRANSLATION_PROMPT_VERSION
    )
    return TranslationRecord.model_validate({**row, "stale": stale})


def attempt(row: dict[str, Any] | None) -> TranslationAttempt | None:
    return TranslationAttempt.model_validate(row) if row else None


@router.get(
    "",
    response_model=SuccessResponse[TicketTranslationView],
    operation_id="cs_get_ticket_translation",
    openapi_extra={**PARTIAL, "x-activation": "CS_TRANSLATION_ENABLED"},
)
async def get_translation(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[TicketTranslationView]:
    """저장된 한국어 번역. 만들지는 않는다(비용 없음)."""
    translation_provider(request)
    async with connection(request.app.state.settings) as conn:
        ticket = await load(conn, principal, ticket_id)
        try:
            _, sha = prepared(ticket)
        except ApplicationError:
            # 번역할 수 없는 문의여도 저장본 조회는 된다. 지금 원문 기준으로는 오래된 것으로 본다.
            sha = None
        # 지금 원문·판본에 맞는 번역이 있으면 그것을, 없으면 가장 최근 번역을 stale로 보인다(요약과 같다).
        valid = (
            await repository.matching(conn, ticket_id, sha, TRANSLATION_PROMPT_VERSION)
            if sha
            else None
        ) or await repository.latest(conn, ticket_id, valid=True)
        last = await repository.latest(conn, ticket_id, valid=False)
    view = TicketTranslationView(
        ticket_id=ticket_id,
        translation=record(valid, sha) if valid else None,
        latest_attempt=attempt(last),
    )
    return respond(request, view)


@router.post(
    "",
    response_model=SuccessResponse[TicketTranslationView],
    status_code=201,
    operation_id="cs_create_ticket_translation",
    responses={200: {"model": SuccessResponse[TicketTranslationView]}},
    openapi_extra={**PARTIAL, "x-activation": "CS_TRANSLATION_ENABLED"},
)
async def create_translation(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> Any:
    """한국어 번역을 만든다. 같은 원문의 성공한 번역이 있으면 그대로 돌려준다(200, 모델 호출 없음)."""
    provider = translation_provider(request)
    async with connection(request.app.state.settings) as conn:
        # permissions.md 1.3: 범위(404) 다음 역할(403).
        ticket = await load(conn, principal, ticket_id)
        require_ticket_write(principal)
    payload, sha = prepared(ticket)
    if not needs_translation(payload):
        raise ApplicationError(
            422, "CS_TRANSLATION_NOT_NEEDED", "The ticket is already written in Korean."
        )
    locks: dict[tuple[UUID, str], list[Any]] = request.app.state.translation_locks
    key = (ticket_id, sha)
    # [잠금, 쓰는 요청 수]. 마지막 요청이 나갈 때만 지운다(요약과 같은 이유).
    entry = locks.setdefault(key, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            return await make(request, provider, principal, ticket_id, payload, sha)
    finally:
        entry[1] -= 1
        if entry[1] == 0:
            locks.pop(key, None)


async def existing_view(request: Request, ticket_id: UUID, sha: str) -> Any:
    """같은 원문·판본의 성공한 번역이 있으면 200 응답, 없으면 None."""
    async with connection(request.app.state.settings) as conn:
        existing = await repository.matching(conn, ticket_id, sha, TRANSLATION_PROMPT_VERSION)
        last = await repository.latest(conn, ticket_id, valid=False)
    if not existing:
        return None
    view = TicketTranslationView(
        ticket_id=ticket_id, translation=record(existing, sha), latest_attempt=attempt(last)
    )
    return JSONResponse(respond(request, view).model_dump(mode="json"), status_code=200)


async def make(
    request: Request,
    provider: Provider,
    principal: Principal,
    ticket_id: UUID,
    payload: Any,
    sha: str,
) -> Any:
    """모델을 한 번 불러 결과를 저장한다. 잠금 안에서 저장본을 다시 확인한다."""
    reused = await existing_view(request, ticket_id, sha)
    if reused is not None:
        return reused
    model = request.app.state.translation_model
    values: dict[str, Any] = {
        "id": uuid4(),
        "ticket_id": ticket_id,
        "input_version": TRANSLATION_INPUT_VERSION,
        "input_sha256": sha,
        "prompt_version": TRANSLATION_PROMPT_VERSION,
        "requested_model": model,
        "requested_by": principal.user_id,
    }
    # DB 연결을 잡은 채 모델을 기다리지 않는다.
    loop = asyncio.get_running_loop()
    try:
        generation = await loop.run_in_executor(
            request.app.state.translation_executor, provider, render_prompt(payload)
        )
    except ClassificationCallError as error:
        values.update(outcome="PROVIDER_ERROR", error_code=error.code)
        values.update(usage_values(error.usage, model))
    else:
        model = generation.model or model
        values["requested_model"] = model
        values.update(
            model_version=generation.model_version,
            latency_ms=generation.elapsed_ms,
            **usage_values(generation.usage, model),
        )
        try:
            output = validate_output(generation.text, source=payload)
        except TranslationOutputError as error:
            values.update(outcome="INVALID_OUTPUT", error_code=error.code)
        else:
            values.update(outcome="VALID", subject_ko=output.subject_ko, body_ko=output.body_ko)
    async with connection(request.app.state.settings) as conn:
        stored = await repository.insert(conn, values)
    if stored is None:
        # 다른 인스턴스가 같은 원문의 번역을 먼저 저장했다(고유 색인). 그것을 돌려준다.
        reused = await existing_view(request, ticket_id, sha)
        assert reused is not None
        return reused
    if stored["outcome"] != "VALID":
        raise ApplicationError(
            502,
            "CS_TRANSLATION_FAILED",
            "The translation could not be made. Try again later.",
            [{"code": str(stored["error_code"])}],
        )
    view = TicketTranslationView(
        ticket_id=ticket_id, translation=record(stored, sha), latest_attempt=attempt(stored)
    )
    return respond(request, view)
