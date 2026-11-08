"""해결 사례 등록 때 같은 고장 AI 추천(KAN-245). 기본 비활성.

- `POST /api/v1/cs/tickets/{id}/fault-suggestion`: cs_agent가 해결 사례의 고장 ID를 고르기 전에 부른다. 이 문의
  원문을 같은 제품·같은 출처의 이미 알려진 해결 기록 묶음(고장 ID별, 묶음마다 이전 문의 원문 예시 2건)과 비교해
  기존 고장 하나 / 새 고장 / 판단 보류를 고른다. 판단 규칙·프롬프트는 KAN-204에서 평가한
  `backend/engine/cs/fault_matching.py`(fault-match-v2)를 그대로 쓴다.
- `CS_FAULT_SUGGESTION_ENABLED`가 꺼져 있으면 404다. 켜면 C/S 서비스가 모델을 직접 부른다(비용, 호출마다 1~2회).

추천은 AI 작성물(`ai_draft`)이며 저장하지 않는다. 고장 ID는 사람이 해결 사례 등록(`POST …/resolution-case`)으로
확정하고, 반복 판정(90일·3곳)은 그 확정값으로 규칙이 한다. 모델에는 고장 ID·해결 기록 문장을 넣지 않는다.
"""

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.cs.summary.routes import load
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.engine.cs.classification_call import ClassificationCallError
from backend.engine.cs.fault_matching import (
    MAX_EXAMPLES_PER_GROUP,
    PROMPT_VERSION,
    Decision,
    GroupView,
    MatchInput,
    MatchOutput,
    build_prompt,
    parse_output,
)
from backend.engine.cs.rules import fault_catalog, product_names
from backend.engine.cs.translation import build_translation_input
from backend.shared.access import Principal, current_principal
from backend.shared.ai import Generation
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind_path_ids
from backend.shared.models import ErrorResponse, SuccessResponse

Provider = Callable[[str], Generation]
logger = logging.getLogger(__name__)
# 호출 실패나 규칙 밖 응답이면 한 번 더 부른다(KAN-204 평가 실행과 같은 재호출).
RETRIES = 1


class CsFaultSuggestionSettings(BaseSettings):
    """같은 고장 추천 설정. 꺼져 있으면 모델 클라이언트를 만들지 않는다."""

    model_config = SettingsConfigDict(
        env_prefix="CS_FAULT_SUGGESTION_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    enabled: bool = False
    model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    # thinking 토큰을 포함한 출력 상한. KAN-204 평가(1024)와 같다.
    max_output_tokens: int = Field(default=1024, ge=256, le=65536)
    max_concurrent_calls: int = Field(default=2, ge=1, le=32)


def configure_fault_suggestion(
    app: FastAPI, value: CsFaultSuggestionSettings, provider: Provider | None = None
) -> None:
    """추천 공급자를 앱에 둔다. 꺼져 있으면 None이라 경로가 404다.

    Args:
        app: C/S 앱.
        value: 설정.
        provider: 검사용 가짜 공급자나 로컬 중계. 없고 켜져 있으면 Gemini 공급자를 만든다.
    """
    app.state.fault_suggestion_model = value.model
    app.state.fault_suggestion_provider = None
    app.state.fault_suggestion_executor = ThreadPoolExecutor(
        max_workers=value.max_concurrent_calls, thread_name_prefix="cs-fault-suggestion"
    )
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
            response_schema=MatchOutput,
            generation=GenerationKnobs(max_output_tokens=value.max_output_tokens),
        )
    app.state.fault_suggestion_provider = provider


class FaultSuggestion(BaseModel):
    ticket_id: UUID
    decision: Decision = Field(
        description="EXISTING: 기존 고장과 같음, NEW: 증상이 있고 어느 기존 고장과도 다름, "
        "UNSURE: 원문에 증상이 없거나 구별할 수 없어 사람이 판단."
    )
    fault_id: str | None = Field(description="EXISTING일 때 추천 고장 ID. 사람이 확정한다.")
    fault_symptom: str | None = Field(description="추천 고장의 카탈로그 증상(참고).")
    matched_cases: int = Field(description="추천 고장 묶음의 이미 알려진 해결 기록 수.")
    evidence_quote: str | None = Field(description="이 문의 원문에서 판단에 쓴 짧은 구절.")
    reason: str
    compared_faults: int = Field(description="비교한 같은 제품의 기존 고장 묶음 수.")
    compared_cases: int = Field(description="비교에 쓴 같은 제품의 해결 기록 수.")
    prompt_version: str
    model: str | None = Field(description="모델을 부르지 않았으면(비교할 묶음 없음) null.")
    provenance: Literal["ai_draft"] = "ai_draft"


router = APIRouter(
    prefix="/api/v1/cs/tickets/{ticket_id}/fault-suggestion",
    tags=["faults"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)


def text_of(row: dict[str, Any]) -> str:
    """분류·번역과 같은 허용 필드·마스킹을 거친 제목·본문(KAN-204 평가 입력과 같은 모양)."""
    payload = build_translation_input(row)
    return "\n".join(part for part in (payload.subject, payload.body) if part)


async def history(conn: Any, ticket: dict[str, Any]) -> list[dict[str, Any]]:
    """같은 제품·같은 출처에서 이미 알려진 다른 문의의 해결 기록과 그 문의 원문, 적재 시각 순."""
    cursor = await conn.execute(
        "SELECT rc.fault_id, t.channel, t.body_language, t.subject, t.body "
        "FROM cs.resolution_cases rc JOIN cs.tickets t ON t.id = rc.ticket_id "
        "WHERE rc.product_code = %s AND rc.data_origin = %s AND rc.ticket_id <> %s "
        "AND rc.known_at <= now() AND rc.fault_id ~ '^FLT-[0-9]{3}$' "
        "ORDER BY rc.known_at, rc.id",
        (ticket["product_code"], ticket["data_origin"], ticket["id"]),
    )
    return await cursor.fetchall()


def match_input(ticket: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[MatchInput, dict]:
    """모델 입력과 가린 참조 → (고장 ID, 묶음 기록 수). 묶음 순서는 처음 알려진 순."""
    members: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        members.setdefault(row["fault_id"], []).append(row)
    refs = {f"G{i}": fault for i, fault in enumerate(members, 1)}
    groups = [
        GroupView(ref=ref, examples=[text_of(r) for r in members[fault][:MAX_EXAMPLES_PER_GROUP]])
        for ref, fault in refs.items()
    ]
    name = product_names().get(ticket["product_code"], ticket["product_code"])
    payload = MatchInput(product_name=name, query=text_of(ticket), groups=groups)
    return payload, {ref: (fault, len(members[fault])) for ref, fault in refs.items()}


@router.post(
    "",
    response_model=SuccessResponse[FaultSuggestion],
    operation_id="cs_suggest_ticket_fault",
    openapi_extra={**PARTIAL, "x-activation": "CS_FAULT_SUGGESTION_ENABLED"},
)
async def suggest_fault(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[FaultSuggestion]:
    """이 문의가 같은 제품의 어느 기존 고장과 같은지 AI가 추천한다. 저장하지 않는다."""
    provider = getattr(request.app.state, "fault_suggestion_provider", None)
    if provider is None:
        raise HTTPException(404)
    async with connection(request.app.state.settings) as conn:
        # permissions.md 1.3: 범위(404) 다음 역할(403). 다른 고객의 문의 원문을 예시로 쓰므로 cs_agent만.
        ticket = await load(conn, principal, ticket_id)
        if not principal.has("cs_agent"):
            raise HTTPException(403)
        rows = await history(conn, ticket)
    try:
        payload, refs = match_input(ticket, rows)
    except ValueError:
        raise ApplicationError(
            422, "CS_FAULT_SUGGESTION_INPUT_INVALID", "The ticket text cannot be compared."
        ) from None
    base = {
        "ticket_id": ticket_id,
        "compared_faults": len(refs),
        "compared_cases": len(rows),
        "prompt_version": PROMPT_VERSION,
    }
    if not refs:
        # 같은 제품의 알려진 고장이 없으면 고를 묶음이 없다. 모델을 부르지 않는다.
        return respond(
            request,
            FaultSuggestion(
                **base,
                decision="NEW",
                fault_id=None,
                fault_symptom=None,
                matched_cases=0,
                evidence_quote=None,
                reason="같은 제품에 알려진 해결 기록이 없어 비교할 고장이 없습니다.",
                model=None,
            ),
        )
    out, model = await ask(request.app.state, provider, payload, set(refs))
    fault, count = refs[out.group_ref] if out.group_ref else (None, 0)
    symptom = next((f.symptom for f in fault_catalog() if f.fault_id == fault), None)
    return respond(
        request,
        FaultSuggestion(
            **base,
            decision=out.decision,
            fault_id=fault,
            fault_symptom=symptom,
            matched_cases=count,
            evidence_quote=out.evidence_quote,
            reason=out.reason,
            model=model,
        ),
    )


async def ask(
    state: Any, provider: Provider, payload: MatchInput, refs: set[str]
) -> tuple[MatchOutput, str]:
    """모델을 불러 검사한 출력. 호출 실패·규칙 밖 응답은 RETRIES번 다시 부르고, 그래도 안 되면 502."""
    prompt = build_prompt(payload)
    loop = asyncio.get_running_loop()
    code = "CS_FAULT_SUGGESTION_FAILED"
    for _ in range(RETRIES + 1):
        try:
            generation = await loop.run_in_executor(
                state.fault_suggestion_executor, provider, prompt
            )
        except ClassificationCallError as error:
            code = error.code
            continue
        try:
            return parse_output(
                generation.text, refs
            ), generation.model or state.fault_suggestion_model
        except ValueError as error:
            code = str(error)
    logger.warning("cs fault suggestion failed", extra={"code": code})
    raise ApplicationError(
        502,
        "CS_FAULT_SUGGESTION_FAILED",
        "The fault suggestion could not be made. Choose the fault yourself or try again.",
        [{"code": code}],
    )
