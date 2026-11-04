"""연결된 고객 회신의 AI 해석(회신 반영 분석, KAN-230 2단계). 기본 비활성.

번역(`backend/api/cs/translation/routes.py`)과 같은 흐름이다. 해석 엔진은 `backend/engine/cs/reply.py`(KAN-109).
- `GET  /api/v1/cs/tickets/{id}/reply-analysis[?message_id=]`: 저장된 해석과 최근 시도. 문의 조회 범위와 같다.
- `POST /api/v1/cs/tickets/{id}/reply-analysis`: cs_agent만 해석을 만든다(담당자가 버튼을 누를 때만, 자동 실행 없음).
  같은 회신·입력·프롬프트 판본의 성공한 해석이 있으면 모델을 부르지 않고 그것을 돌려준다(200). 새로 만들면 201,
  실패하면 시도를 저장하고 502다. 본문이 있는 회신이 없으면 모델을 부르지 않고 422 `CS_REPLY_TEXT_MISSING`이다.
- `CS_REPLY_ANALYSIS_ENABLED`가 꺼져 있으면 두 경로 모두 404다. 켜면 C/S 서비스가 모델을 직접 부른다(비용).

해석은 AI 작성물(`ai_draft`)이며 판정이 아니다. 업무 상태·분석 칸·정정을 바꾸지 않는다. 답한 정보를
제공 정보에 반영하는 것은 사람이 기존 정정 API(`/analysis/corrections`)로 한다.
"""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.cs.analysis import repository as analysis_repository
from backend.api.cs.analysis.service import latest_valid
from backend.api.cs.reply_analysis import repository
from backend.api.cs.reply_analysis.schemas import (
    ReplyAnalysisAttempt,
    ReplyAnalysisRecord,
    ReplyAnalysisRequest,
    TicketReplyAnalysisView,
)
from backend.api.cs.summary.routes import load, usage_values
from backend.api.cs.tickets.access import require_ticket_write, sees_all_tickets
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.engine.cs.analysis import effective_fields
from backend.engine.cs.classification_call import ClassificationCallError
from backend.engine.cs.drafting import DraftOutput
from backend.engine.cs.prompt_registry import DEFAULT_VERSIONS
from backend.engine.cs.reply import (
    REPLY_INPUT_VERSION,
    CustomerReply,
    ReplyContext,
    ReplyInterpretation,
    ReplyInterpretationInput,
    apply_reply,
    build_reply_input,
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
PROMPT_VERSION = DEFAULT_VERSIONS["reply"]


class CsReplyAnalysisSettings(BaseSettings):
    """회신 해석 설정. 꺼져 있으면 모델 클라이언트를 만들지 않는다."""

    model_config = SettingsConfigDict(
        env_prefix="CS_REPLY_ANALYSIS_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    enabled: bool = False
    model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    # thinking 토큰을 포함한 출력 상한. 판정 이유·새 사실·남은 질문을 쓰므로 요약(4096)보다 크게 둔다.
    max_output_tokens: int = Field(default=8192, ge=256, le=65536)
    # 동시에 진행하는 회신 해석 모델 호출 수(프로세스당).
    max_concurrent_calls: int = Field(default=4, ge=1, le=32)


def configure_reply_analysis(
    app: FastAPI, value: CsReplyAnalysisSettings, provider: Provider | None = None
) -> None:
    """회신 해석 공급자를 앱에 둔다. 꺼져 있으면 None이라 경로가 404다.

    Args:
        app: C/S 앱.
        value: 설정.
        provider: 검사용 가짜 공급자. 없고 켜져 있으면 Gemini 공급자를 만든다.
    """
    app.state.reply_analysis_model = value.model
    app.state.reply_analysis_provider = None
    # 모델 호출이 기본 스레드 풀을 다 차지하지 않게 전용 풀을 둔다.
    app.state.reply_analysis_executor = ThreadPoolExecutor(
        max_workers=value.max_concurrent_calls, thread_name_prefix="cs-reply-analysis"
    )
    # 같은 회신·입력에 동시에 온 생성 요청은 한 번만 모델을 부르도록 프로세스 안에서 줄 세운다.
    app.state.reply_analysis_locks = {}
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
            response_schema=ReplyInterpretation,
            generation=GenerationKnobs(max_output_tokens=value.max_output_tokens),
        )
    app.state.reply_analysis_provider = provider


def reply_analysis_provider(request: Request) -> Provider:
    provider = getattr(request.app.state, "reply_analysis_provider", None)
    if provider is None:
        raise HTTPException(404)
    return provider


router = APIRouter(
    prefix="/api/v1/cs/tickets/{ticket_id}/reply-analysis",
    tags=["reply-analysis"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)


@dataclass(frozen=True)
class Prepared:
    """한 회신의 해석 맥락·마스킹한 모델 입력과 그 입력의 SHA-256."""

    message_id: UUID
    context: ReplyContext
    payload: ReplyInterpretationInput
    sha: str


def reply_not_found() -> ApplicationError:
    return ApplicationError(404, "CS_REPLY_NOT_FOUND", "The reply was not found on this ticket.")


def text_missing() -> ApplicationError:
    return ApplicationError(
        422, "CS_REPLY_TEXT_MISSING", "No linked reply with text. Read it in the mail account."
    )


def input_sha256(payload: ReplyInterpretationInput) -> str:
    """회신 해석 입력 판본. 요청 코드·제공 정보·확정 장비 대조·이전 초안이 바뀌면 바뀐다."""
    encoded = json.dumps(
        {"v": REPLY_INPUT_VERSION, **payload.model_dump(mode="json")},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


async def prepare(conn: Any, ticket: dict[str, Any], reply: dict[str, Any]) -> Prepared:
    """회신 하나의 해석 입력을 만든다. 본문이 없으면 422 `CS_REPLY_TEXT_MISSING`.

    - requested_info_codes: 고객에게 실제로 나간 메시지에서 물은 코드(분석 화면 already_asked와 같은 규칙).
    - already_provided_codes: 사람 정정을 반영한 유효 제공 정보. 유효 분석·정정이 없으면 빈 목록.
    - prior_options: 가장 최근의 성공한 답변 초안. 없거나 지금 스키마로 읽을 수 없으면 None.
    - 확정 장비 시리얼은 대조에만 쓴다. 어댑터가 식별한 민감값은 아직 없어 빈 집합이다(라벨 붙은 시리얼과
      이메일은 build_input 규칙으로 가려진다).
    """
    if reply["body"] is None:
        raise text_missing()
    ticket_id = reply["ticket_id"]
    runs = await analysis_repository.list_runs(conn, ticket_id)
    corrections = await analysis_repository.list_corrections(conn, ticket_id)
    valid = latest_valid(runs)
    fields = effective_fields(
        valid["classification"] if valid else None, [c["fields"] for c in corrections]
    )
    provided = fields["provided_info"].effective_value or []
    draft = latest_valid(await analysis_repository.list_drafts(conn, ticket_id))
    prior = None
    if draft is not None:
        try:
            prior = DraftOutput.model_validate(draft["draft"])
        except ValueError:
            prior = None
    asked = await analysis_repository.asked_info_codes(conn, ticket_id)
    serial = await repository.confirmed_serial(conn, ticket_id)
    try:
        context = ReplyContext(
            ticket_product_code=ticket["product_code"],
            fw_version_reported=ticket["fw_version_reported"],
            requested_info_codes=sorted(asked),
            already_provided_codes=sorted(set(provided)),
            prior_options=prior,
        )
        prepared = build_reply_input(
            context,
            CustomerReply(
                # 연결된 회신은 메일로만 들어온다(메일 외 채널은 회신 연결이 없다).
                channel="EMAIL",
                body=reply["body"],
                body_language=ticket["body_language"],
                received_at=reply["received_at"],
            ),
            confirmed_unit_serial=serial,
            sensitive_values={},
        )
    except ValueError:
        raise ApplicationError(
            422, "CS_REPLY_INPUT_INVALID", "The reply cannot be analysed for this ticket."
        ) from None
    return Prepared(reply["message_id"], context, prepared.payload, input_sha256(prepared.payload))


def record(row: dict[str, Any], current_sha: str | None) -> ReplyAnalysisRecord:
    stale = row["input_sha256"] != current_sha or row["prompt_version"] != PROMPT_VERSION
    return ReplyAnalysisRecord.model_validate(
        {**row, "update": row["reply_update"], "stale": stale}
    )


def attempt(row: dict[str, Any] | None) -> ReplyAnalysisAttempt | None:
    return ReplyAnalysisAttempt.model_validate(row) if row else None


@router.get(
    "",
    response_model=SuccessResponse[TicketReplyAnalysisView],
    operation_id="cs_get_ticket_reply_analysis",
    openapi_extra={**PARTIAL, "x-activation": "CS_REPLY_ANALYSIS_ENABLED"},
)
async def get_reply_analysis(
    ticket_id: UUID,
    request: Request,
    message_id: UUID | None = None,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[TicketReplyAnalysisView]:
    """저장된 회신 해석. 만들지는 않는다(비용 없음).

    고객 회신은 C/S 업무라 부서 사용자에게 보이지 않는다(tickets.md 조회 범위). 회신에서 만든
    해석도 같다: cs_agent와 C/S 부서 관리자만 읽는다. 범위 밖이면 먼저 404다.
    """
    reply_analysis_provider(request)
    async with connection(request.app.state.settings) as conn:
        ticket = await load(conn, principal, ticket_id)
        if not sees_all_tickets(principal):
            raise HTTPException(403)
        reply = await repository.reply(conn, ticket_id, message_id)
        if reply is None:
            if message_id is not None:
                raise reply_not_found()
            view = TicketReplyAnalysisView(
                ticket_id=ticket_id, message_id=None, analysis=None, latest_attempt=None
            )
            return respond(request, view)
        try:
            sha: str | None = (await prepare(conn, ticket, reply)).sha
        except ApplicationError:
            # 지금은 입력을 만들 수 없어도 저장본 조회는 된다. 지금 기준으로는 오래된 것으로 본다.
            sha = None
        target = reply["message_id"]
        # 지금 입력·판본에 맞는 해석이 있으면 그것을, 없으면 가장 최근 해석을 stale로 보인다.
        valid = (
            await repository.matching(conn, target, sha, PROMPT_VERSION) if sha else None
        ) or await repository.latest(conn, target, valid=True)
        last = await repository.latest(conn, target, valid=False)
    view = TicketReplyAnalysisView(
        ticket_id=ticket_id,
        message_id=target,
        analysis=record(valid, sha) if valid else None,
        latest_attempt=attempt(last),
    )
    return respond(request, view)


@router.post(
    "",
    response_model=SuccessResponse[TicketReplyAnalysisView],
    status_code=201,
    operation_id="cs_create_ticket_reply_analysis",
    responses={200: {"model": SuccessResponse[TicketReplyAnalysisView]}},
    openapi_extra={**PARTIAL, "x-activation": "CS_REPLY_ANALYSIS_ENABLED"},
)
async def create_reply_analysis(
    ticket_id: UUID,
    request: Request,
    body: ReplyAnalysisRequest | None = None,
    principal: Principal = Depends(current_principal),
) -> Any:
    """회신을 해석한다. 같은 입력의 성공한 해석이 있으면 그대로 돌려준다(200, 모델 호출 없음)."""
    provider = reply_analysis_provider(request)
    message_id = body.message_id if body else None
    async with connection(request.app.state.settings) as conn:
        # permissions.md 1.3: 범위(404) 다음 역할(403).
        ticket = await load(conn, principal, ticket_id)
        require_ticket_write(principal)
        reply = await repository.reply(conn, ticket_id, message_id)
        if reply is None:
            raise reply_not_found() if message_id is not None else text_missing()
        prepared = await prepare(conn, ticket, reply)
    async with reply_lock(request.app.state, prepared.message_id, prepared.sha):
        return await make(request, provider, principal, ticket_id, prepared)


@asynccontextmanager
async def reply_lock(state: Any, message_id: UUID, sha: str) -> AsyncIterator[None]:
    """같은 회신·입력의 해석 생성을 프로세스 안에서 한 번에 하나만 돌린다."""
    locks: dict[tuple[UUID, str], list[Any]] = state.reply_analysis_locks
    key = (message_id, sha)
    # [잠금, 쓰는 요청 수]. 마지막 요청이 나갈 때만 지운다(번역과 같은 이유).
    entry = locks.setdefault(key, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] == 0:
            locks.pop(key, None)


async def existing_view(request: Request, ticket_id: UUID, prepared: Prepared) -> Any:
    """같은 회신·입력·판본의 성공한 해석이 있으면 200 응답, 없으면 None."""
    async with connection(request.app.state.settings) as conn:
        existing = await repository.matching(
            conn, prepared.message_id, prepared.sha, PROMPT_VERSION
        )
        last = await repository.latest(conn, prepared.message_id, valid=False)
    if not existing:
        return None
    view = TicketReplyAnalysisView(
        ticket_id=ticket_id,
        message_id=prepared.message_id,
        analysis=record(existing, prepared.sha),
        latest_attempt=attempt(last),
    )
    return JSONResponse(respond(request, view).model_dump(mode="json"), status_code=200)


async def make(
    request: Request, provider: Provider, principal: Principal, ticket_id: UUID, prepared: Prepared
) -> Any:
    """모델을 한 번 불러 결과를 저장한다. 잠금 안에서 저장본을 다시 확인한다."""
    reused = await existing_view(request, ticket_id, prepared)
    if reused is not None:
        return reused
    stored = await generate(request.app.state, provider, ticket_id, prepared, principal.user_id)
    if stored is None:
        # 다른 인스턴스가 같은 입력의 해석을 먼저 저장했다(고유 색인). 그것을 돌려준다.
        reused = await existing_view(request, ticket_id, prepared)
        if reused is None:
            raise ApplicationError(
                409,
                "CS_REPLY_ANALYSIS_CONFLICT",
                "Another request stored this analysis. Read it again.",
            )
        return reused
    if stored["outcome"] != "VALID":
        raise ApplicationError(
            502,
            "CS_REPLY_ANALYSIS_FAILED",
            "The reply could not be analysed. Try again later.",
            [{"code": str(stored["error_code"])}],
        )
    view = TicketReplyAnalysisView(
        ticket_id=ticket_id,
        message_id=prepared.message_id,
        analysis=record(stored, prepared.sha),
        latest_attempt=attempt(stored),
    )
    return respond(request, view)


async def generate(
    state: Any, provider: Provider, ticket_id: UUID, prepared: Prepared, requested_by: UUID
) -> dict[str, Any] | None:
    """모델을 한 번 불러 시도를 저장한다. 같은 입력의 성공한 해석이 먼저 저장됐으면 None."""
    model = state.reply_analysis_model
    payload = prepared.payload
    values: dict[str, Any] = {
        "id": uuid4(),
        "ticket_id": ticket_id,
        "message_id": prepared.message_id,
        "input_version": REPLY_INPUT_VERSION,
        "input_sha256": prepared.sha,
        "prompt_version": PROMPT_VERSION,
        "requested_model": model,
        "requested_by": requested_by,
    }
    # DB 연결을 잡은 채 모델을 기다리지 않는다.
    loop = asyncio.get_running_loop()
    try:
        generation = await loop.run_in_executor(
            state.reply_analysis_executor,
            provider,
            render_prompt(payload, prompt_version=PROMPT_VERSION),
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
            interpretation = validate_output(
                generation.text,
                requested_info_codes=frozenset(payload.requested_info_codes),
                serial_match=payload.reply_serial_match,
            )
            update = apply_reply(prepared.context, interpretation)
        except ValueError as error:
            code = str(error)
            values.update(
                outcome="INVALID_OUTPUT",
                error_code=code if code.startswith("CS_") else "CS_INVALID_OUTPUT",
            )
        else:
            values.update(
                outcome="VALID",
                interpretation=interpretation.model_dump(mode="json"),
                reply_update=update.model_dump(mode="json"),
            )
    async with connection(state.settings) as conn:
        return await repository.insert(conn, values)
