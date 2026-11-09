"""고객 회신 초안의 AI 보조(KAN-251). 기본 비활성. 판단·규칙은 `backend/engine/cs/reply_assist.py`.

- `POST …/reply-draft/dept-answer` (`CS_DEPT_REPLY_DRAFT_ENABLED`): 확인 완료(ACCEPTED)한 부서 답변으로 고객 언어
  회신 초안 하나를 만든다.
- `POST …/reply-draft/korean` (`CS_REPLY_TRANSLATION_ENABLED`): 회신 상자의 초안을 한국어로 옮긴다. 고객 언어가
  한국어이거나 글이 이미 한국어면 모델을 부르지 않고 422 `CS_REPLY_TRANSLATION_NOT_NEEDED`다.
- `POST …/reply-draft/korean-edit` (같은 설정): 담당자가 고친 한국어를 원래 초안에 반영한 고객 언어 글을 만든다.
  원래 초안의 한국어 번역(위 경로의 저장본)이 있어야 한다.

세 경로 모두 cs_agent만 쓴다(범위 404 다음 역할 403, permissions.md 1.3). 결과는 AI 작성물(`ai_draft`)이고 시도마다
`cs.reply_draft_assists`(C34)에 남긴다. 같은 입력·프롬프트 판본의 성공한 결과가 있으면 모델을 다시 부르지 않고
그것을 돌려준다(`reused`). 아무것도 보내거나 업무 상태를 바꾸지 않는다. 담당자가 회신 상자에 넣고 고쳐 보내기 확인에서
승인해야 나간다. 설정이 꺼져 있으면 그 경로는 404다. 켜면 C/S 서비스가 모델을 직접 부른다(비용).
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.cs.summary.repository import USAGE
from backend.api.cs.summary.routes import load, usage_values
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.api.cs.workflow import repository as workflow
from backend.engine.cs.classification_call import ClassificationCallError
from backend.engine.cs.drafting import find_forbidden_commitment
from backend.engine.cs.reply_assist import (
    INPUT_VERSION,
    MAX_TEXT_CHARS,
    PROMPT_VERSIONS,
    Kind,
    KoreanEditInput,
    KoreanInput,
    Payload,
    ReplyAssistError,
    TextOutput,
    build_dept_draft_input,
    input_sha256,
    korean_language,
    mask,
    mostly_korean,
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
Feature = Literal["dept", "translation"]
COLUMNS = (
    "id, ticket_id, kind, dept_result_id, outcome, error_code, output_text, prompt_version, "
    "requested_model, model_version, created_at"
)


class _AssistSettings(BaseSettings):
    enabled: bool = False
    model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    # thinking 토큰을 포함한 출력 상한. 회신 하나라 문의 번역(16384)보다 작게 둔다.
    max_output_tokens: int = Field(default=8192, ge=256, le=65536)
    max_concurrent_calls: int = Field(default=2, ge=1, le=32)


class CsDeptReplyDraftSettings(_AssistSettings):
    """부서 답변 기반 회신 초안 설정. 꺼져 있으면 모델 클라이언트를 만들지 않는다."""

    model_config = SettingsConfigDict(
        env_prefix="CS_DEPT_REPLY_DRAFT_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )


class CsReplyTranslationSettings(_AssistSettings):
    """회신 초안 한국어 번역·한국어 수정 반영 설정. 꺼져 있으면 모델 클라이언트를 만들지 않는다."""

    model_config = SettingsConfigDict(
        env_prefix="CS_REPLY_TRANSLATION_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )


@dataclass(frozen=True)
class Assist:
    provider: Provider
    model: str
    executor: ThreadPoolExecutor


def configure_reply_assist(
    app: FastAPI,
    dept: CsDeptReplyDraftSettings,
    translation: CsReplyTranslationSettings,
    *,
    dept_provider: Provider | None = None,
    translation_provider: Provider | None = None,
) -> None:
    """켜진 기능의 공급자를 앱에 둔다. 꺼진 기능은 없어서 그 경로가 404다.

    Args:
        app: C/S 앱.
        dept: 부서 답변 기반 초안 설정.
        translation: 한국어 번역·수정 반영 설정.
        dept_provider: 검사용 가짜 공급자. 없고 켜져 있으면 Gemini 공급자를 만든다.
        translation_provider: 위와 같다.
    """
    features: dict[Feature, Assist] = {}
    choices: tuple[tuple[Feature, _AssistSettings, Provider | None], ...] = (
        ("dept", dept, dept_provider),
        ("translation", translation, translation_provider),
    )
    for name, value, provider in choices:
        if not value.enabled:
            continue
        if provider is None:
            from backend.engine.cs.providers import GenerationKnobs, gemini_text_provider
            from backend.shared.ai import AISettings, create_client

            ai = AISettings()
            provider = gemini_text_provider(
                create_client(ai),
                ai,
                model=value.model,
                response_schema=TextOutput,
                generation=GenerationKnobs(max_output_tokens=value.max_output_tokens),
            )
        # 모델 호출이 기본 스레드 풀을 다 차지하지 않게 기능마다 전용 풀을 둔다.
        executor = ThreadPoolExecutor(
            max_workers=value.max_concurrent_calls, thread_name_prefix=f"cs-reply-{name}"
        )
        features[name] = Assist(provider, value.model, executor)
    app.state.reply_assist = features


def feature(request: Request, name: Feature) -> Assist:
    found = getattr(request.app.state, "reply_assist", {}).get(name)
    if found is None:
        raise HTTPException(404)
    return found


Text = Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT_CHARS, pattern=r"\S")]


class DeptDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dept_result_id: UUID = Field(description="확인 완료(ACCEPTED)한 이 문의의 부서 답변.")


class KoreanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Text = Field(
        description="회신 상자의 고객 언어 초안(AI 선택지·부서 답변 기반 초안·직접 쓴 글)."
    )


class KoreanEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Text = Field(
        description="한국어로 옮긴 원래 고객 언어 초안(`…/korean`에 보낸 글과 같아야 한다)."
    )
    edited_ko: Text = Field(description="담당자가 고친 한국어.")


class ReplyAssist(BaseModel):
    ticket_id: UUID
    kind: Kind = Field(
        description="DEPT_DRAFT: 부서 답변 기반 고객 언어 초안, TO_KO: 초안의 한국어 번역, "
        "FROM_KO: 한국어 수정을 반영한 고객 언어 초안."
    )
    text: str = Field(description="AI가 쓴 글. 담당자가 확인·수정한 뒤 보낸다. 보내지 않았다.")
    dept_result_id: UUID | None = Field(description="DEPT_DRAFT의 근거 부서 답변. 나머지는 null.")
    commitment_warning: bool = Field(
        description="FROM_KO에서 담당자가 한국어에 쓴 승인·확정 약속이 고객 언어 글에도 있다. 확정한 내용인지 "
        "보내기 전에 확인한다."
    )
    reused: bool = Field(description="True면 같은 입력의 저장본이다(모델 호출 없음).")
    created_at: datetime
    requested_model: str
    model_version: str | None
    prompt_version: str
    provenance: Literal["ai_draft"] = "ai_draft"


router = APIRouter(
    prefix="/api/v1/cs/tickets/{ticket_id}/reply-draft",
    tags=["reply-draft"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)


async def ticket_for_agent(
    request: Request, principal: Principal, ticket_id: UUID, *, results: bool = False
) -> tuple[Any, list[Any]]:
    """범위(404) 다음 역할(403). 고객 회신은 C/S 담당자의 일이다(보내기와 같다). results면 부서 답변도 읽는다."""
    async with connection(request.app.state.settings) as conn:
        ticket = await load(conn, principal, ticket_id)
        if not principal.has("cs_agent"):
            raise HTTPException(403)
        return ticket, await workflow.list_results(conn, ticket_id) if results else []


def invalid_input() -> ApplicationError:
    return ApplicationError(422, "CS_REPLY_ASSIST_INPUT_INVALID", "The text cannot be used.")


def not_needed() -> ApplicationError:
    return ApplicationError(
        422, "CS_REPLY_TRANSLATION_NOT_NEEDED", "The draft is already written in Korean."
    )


@router.post(
    "/dept-answer",
    response_model=SuccessResponse[ReplyAssist],
    operation_id="cs_create_dept_reply_draft",
    openapi_extra={**PARTIAL, "x-activation": "CS_DEPT_REPLY_DRAFT_ENABLED"},
)
async def dept_reply_draft(
    ticket_id: UUID,
    payload: DeptDraftRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[ReplyAssist]:
    """확인 완료한 부서 답변으로 고객 언어 회신 초안을 만든다. 보내지 않는다."""
    assist = feature(request, "dept")
    ticket, results = await ticket_for_agent(request, principal, ticket_id, results=True)
    result = next((r for r in results if r["id"] == payload.dept_result_id), None)
    if result is None or result["review_status"] != "ACCEPTED":
        raise ApplicationError(
            422, "CS_DEPT_RESULT_NOT_ACCEPTED", "Choose a department answer you accepted."
        )
    try:
        model_input = build_dept_draft_input(ticket, result)
    except ValueError:
        raise invalid_input() from None
    return await run(request, assist, principal, ticket_id, "DEPT_DRAFT", model_input, result["id"])


@router.post(
    "/korean",
    response_model=SuccessResponse[ReplyAssist],
    operation_id="cs_translate_reply_draft",
    openapi_extra={**PARTIAL, "x-activation": "CS_REPLY_TRANSLATION_ENABLED"},
)
async def translate_draft(
    ticket_id: UUID,
    payload: KoreanRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[ReplyAssist]:
    """회신 초안을 한국어로 옮긴다. 이미 한국어면 모델을 부르지 않고 422."""
    assist = feature(request, "translation")
    ticket, _ = await ticket_for_agent(request, principal, ticket_id)
    model_input = korean_input(ticket, payload.text)
    return await run(request, assist, principal, ticket_id, "TO_KO", model_input)


def korean_input(ticket: Any, text: str) -> KoreanInput:
    language = ticket["body_language"]
    if korean_language(language) or mostly_korean(text):
        raise not_needed()
    try:
        return KoreanInput(language=language, text=mask(text))
    except ValueError:
        raise invalid_input() from None


@router.post(
    "/korean-edit",
    response_model=SuccessResponse[ReplyAssist],
    operation_id="cs_apply_korean_reply_edit",
    openapi_extra={**PARTIAL, "x-activation": "CS_REPLY_TRANSLATION_ENABLED"},
)
async def apply_korean_edit(
    ticket_id: UUID,
    payload: KoreanEditRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[ReplyAssist]:
    """고친 한국어를 원래 초안에 반영한 고객 언어 글을 만든다. 회신 상자에 넣는 것은 담당자다."""
    assist = feature(request, "translation")
    ticket, _ = await ticket_for_agent(request, principal, ticket_id)
    base_input = korean_input(ticket, payload.text)
    async with connection(request.app.state.settings) as conn:
        base = await matching(conn, ticket_id, "TO_KO", input_sha256("TO_KO", base_input))
    if base is None:
        raise ApplicationError(
            422, "CS_REPLY_TRANSLATION_BASE_MISSING", "Translate the draft to Korean first."
        )
    edited = mask(payload.edited_ko)
    if edited == base["output_text"]:
        raise ApplicationError(
            422, "CS_REPLY_TRANSLATION_UNCHANGED", "The Korean text was not changed."
        )
    model_input = KoreanEditInput(
        language=base_input.language,
        original=base_input.text,
        original_ko=base["output_text"],
        edited_ko=edited,
    )
    return await run(request, assist, principal, ticket_id, "FROM_KO", model_input)


async def matching(conn: Any, ticket_id: UUID, kind: Kind, sha: str) -> dict[str, Any] | None:
    """같은 입력·지금 프롬프트 판본의 성공한 결과."""
    cursor = await conn.execute(
        f"SELECT {COLUMNS} FROM cs.reply_draft_assists WHERE ticket_id = %s AND kind = %s "
        "AND outcome = 'VALID' AND input_sha256 = %s AND prompt_version = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (ticket_id, kind, sha, PROMPT_VERSIONS[kind]),
    )
    return await cursor.fetchone()


async def insert(conn: Any, values: dict[str, Any]) -> dict[str, Any] | None:
    """시도 한 행을 더한다. 같은 입력의 성공한 결과가 먼저 저장됐으면(동시 생성) None."""
    names = (
        "id",
        "ticket_id",
        "kind",
        "dept_result_id",
        "outcome",
        "error_code",
        "output_text",
        "input_version",
        "input_sha256",
        "prompt_version",
        "requested_model",
        "model_version",
        "requested_by",
        *USAGE,
    )
    cursor = await conn.execute(
        f"INSERT INTO cs.reply_draft_assists ({', '.join(names)}) "
        f"VALUES ({', '.join(['%s'] * len(names))}) "
        "ON CONFLICT (ticket_id, kind, input_sha256, prompt_version) WHERE outcome = 'VALID' "
        f"DO NOTHING RETURNING {COLUMNS}",
        tuple(values.get(name) for name in names),
    )
    return await cursor.fetchone()


def view(row: dict[str, Any], *, reused: bool) -> ReplyAssist:
    warning = row["kind"] == "FROM_KO" and find_forbidden_commitment(row["output_text"])
    return ReplyAssist.model_validate(
        {**row, "text": row["output_text"], "commitment_warning": warning, "reused": reused}
    )


async def run(
    request: Request,
    assist: Assist,
    principal: Principal,
    ticket_id: UUID,
    kind: Kind,
    model_input: Payload,
    dept_result_id: UUID | None = None,
) -> SuccessResponse[ReplyAssist]:
    """저장본이 있으면 돌려주고, 없으면 모델을 한 번 불러 검사·저장한다. 실패도 저장하고 502다."""
    sha = input_sha256(kind, model_input)
    settings = request.app.state.settings
    async with connection(settings) as conn:
        found = await matching(conn, ticket_id, kind, sha)
    if found:
        return respond(request, view(found, reused=True))
    values: dict[str, Any] = {
        "id": uuid4(),
        "ticket_id": ticket_id,
        "kind": kind,
        "dept_result_id": dept_result_id,
        "input_version": INPUT_VERSION,
        "input_sha256": sha,
        "prompt_version": PROMPT_VERSIONS[kind],
        "requested_model": assist.model,
        "requested_by": principal.user_id,
    }
    # DB 연결을 잡은 채 모델을 기다리지 않는다.
    loop = asyncio.get_running_loop()
    try:
        generation = await loop.run_in_executor(
            assist.executor, assist.provider, render_prompt(kind, model_input)
        )
    except ClassificationCallError as error:
        values.update(outcome="PROVIDER_ERROR", error_code=error.code)
        values.update(usage_values(error.usage, assist.model))
    else:
        model = generation.model or assist.model
        values.update(
            requested_model=model,
            model_version=generation.model_version,
            latency_ms=generation.elapsed_ms,
            **usage_values(generation.usage, model),
        )
        try:
            text = validate_output(generation.text, kind=kind, payload=model_input)
        except ReplyAssistError as error:
            values.update(outcome="INVALID_OUTPUT", error_code=error.code)
        else:
            values.update(outcome="VALID", output_text=text)
    async with connection(settings) as conn:
        stored = await insert(conn, values)
        reused = stored is None
        if stored is None:
            # 다른 요청이 같은 입력의 결과를 먼저 저장했다(고유 색인). 그것을 돌려준다.
            stored = await matching(conn, ticket_id, kind, sha)
    assert stored is not None
    if stored["outcome"] != "VALID":
        raise ApplicationError(
            502,
            "CS_REPLY_ASSIST_FAILED",
            "The AI text could not be made. Try again or write it yourself.",
            [{"code": str(stored["error_code"])}],
        )
    return respond(request, view(stored, reused=reused))
