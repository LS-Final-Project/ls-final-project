"""개선요청 AI 작성 초안 조회·생성(KAN-115, 기획서 10·15쪽). 기본 비활성.

- `GET  /api/v1/cs/improvement-requests/{id}/ai-draft`: 저장된 최근 초안과 최근 시도. 요청 조회 범위와 같다.
- `POST /api/v1/cs/improvement-requests/{id}/ai-draft`: cs_agent가 초안을 만든다. 같은 근거 입력·프롬프트 판본의
  성공한 초안이 이미 있으면 모델을 부르지 않고 그것을 돌려준다(200). 새로 만들면 201, 실패하면 시도를 저장하고 502다.
- `CS_IMPROVEMENT_DRAFT_ENABLED`가 꺼져 있으면 두 경로 모두 404다. 켜면 C/S 서비스가 모델을 직접 부른다(비용).

초안은 AI 작성물(`ai_draft`)이며 요청서를 바꾸지 않는다. 담당자가 화면에서 고쳐 요청서 내용 수정(PATCH)으로
반영해야 새 판본이 된다. 입력·출력 검사 규칙은 backend/engine/cs/improvement_drafting.py가 원본이다.
"""

import asyncio
import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.cs.improvements import ai_draft_repository as repository
from backend.api.cs.improvements import repository as requests
from backend.api.cs.improvements.routes import check_open, check_visible, not_found, require_writer
from backend.api.cs.improvements.schemas import (
    AiDraftAttempt,
    AiDraftEvidence,
    AiDraftRecord,
    ImprovementAiDraftView,
)
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.engine.cs.classification_call import ClassificationCallError
from backend.engine.cs.improvement_drafting import (
    IMPROVEMENT_DRAFT_INPUT_VERSION,
    ConditionSummary,
    EvidenceCase,
    EvidenceTicket,
    ImprovementDraft,
    ImprovementDraftContext,
    ImprovementDraftInput,
    build_improvement_draft_input,
    render_prompt,
    validate_output,
)
from backend.engine.cs.prompt_registry import DEFAULT_VERSIONS
from backend.shared.access import Principal, current_principal
from backend.shared.ai import Generation, usage_cost
from backend.shared.database import connection
from backend.shared.departments import TRANSFER_DEPARTMENTS
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind_path_ids
from backend.shared.models import ErrorResponse, SuccessResponse

Provider = Callable[[str], Generation]
Row = dict[str, Any]
PROMPT_VERSION = DEFAULT_VERSIONS["improvement_draft"]
# 근거 문의 하나의 입력 길이. 제목+본문이 길면 앞부분만 쓴다(모델 입력 상한과 비용 관리).
TICKET_TEXT_LIMIT = 2000


class CsImprovementDraftSettings(BaseSettings):
    """AI 작성 초안 설정. 꺼져 있으면 모델 클라이언트를 만들지 않는다."""

    model_config = SettingsConfigDict(
        env_prefix="CS_IMPROVEMENT_DRAFT_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    enabled: bool = False
    model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    # thinking 토큰을 포함한 출력 상한. 요청서 칸과 부서 질문까지 쓰므로 요약보다 크게 둔다.
    max_output_tokens: int = Field(default=8192, ge=256, le=65536)
    max_concurrent_calls: int = Field(default=2, ge=1, le=32)


def configure_improvement_draft(
    app: FastAPI, value: CsImprovementDraftSettings, provider: Provider | None = None
) -> None:
    """초안 공급자를 앱에 둔다. 꺼져 있으면 None이라 경로가 404다.

    Args:
        app: C/S 앱.
        value: 설정.
        provider: 검사용 가짜 공급자나 로컬 중계. 없고 켜져 있으면 Gemini 공급자를 만든다.
    """
    app.state.improvement_draft_model = value.model
    app.state.improvement_draft_provider = None
    app.state.improvement_draft_executor = ThreadPoolExecutor(
        max_workers=value.max_concurrent_calls, thread_name_prefix="cs-improvement-draft"
    )
    # 같은 요청·입력에 동시에 온 생성 요청은 한 번만 모델을 부르도록 프로세스 안에서 줄 세운다.
    app.state.improvement_draft_locks = {}
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
            response_schema=ImprovementDraft,
            generation=GenerationKnobs(max_output_tokens=value.max_output_tokens),
        )
    app.state.improvement_draft_provider = provider


def draft_provider(request: Request) -> Provider:
    provider = getattr(request.app.state, "improvement_draft_provider", None)
    if provider is None:
        raise HTTPException(404)
    return provider


router = APIRouter(
    prefix="/api/v1/cs/improvement-requests/{request_id}/ai-draft",
    tags=["improvements"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)


def ticket_ref(row: Row) -> str:
    return row["ticket_external_id"] or str(row["ticket_id"])


def case_ref(row: Row) -> str:
    return row["case_external_id"] or str(row["case_id"])


def context_for(request: Row, rows: list[Row], as_of: datetime) -> ImprovementDraftContext:
    """요청과 유효 근거로 엔진 맥락을 만든다. 근거가 없거나 규칙에 맞지 않으면 422."""
    fault = request["request_type"] == "FAULT"
    tickets: dict[UUID, EvidenceTicket] = {}
    cases: list[EvidenceCase] = []
    for row in rows:
        text = "\n".join(part for part in (row["subject"], row["body"]) if part and part.strip())
        tickets.setdefault(
            row["ticket_id"],
            EvidenceTicket(
                ticket_ref=ticket_ref(row),
                received_at=row["received_at"],
                product_code=row["product_code"],
                fw_version_reported=row["fw_version_reported"],
                error_code_reported=row["error_code_reported"],
                summary=text[:TICKET_TEXT_LIMIT] or "(본문 없음)",
            ),
        )
        if fault and row["case_id"] is not None:
            cases.append(
                EvidenceCase(
                    case_ref=case_ref(row),
                    known_at=row["known_at"],
                    fault_id=row["fault_id"],
                    fw_version_at_issue=row["fw_version_at_issue"],
                    root_cause=row["root_cause"],
                    root_cause_type=row["root_cause_type"],
                    action_taken=row["action_taken"],
                    fw_fixed_version=row["fw_fixed_version"],
                    resolved_by_dept=row["resolved_by_dept"],
                )
            )
    # 후보 부서: 지금 제안 부서와 근거를 해결한 부서. 부서 목록 밖(C/S 1차 등)은 뺀다. 없으면 전체 목록.
    known = {*request["proposed_depts"], *(row["resolved_by_dept"] for row in rows if fault)}
    candidates = sorted(dept for dept in known if dept in TRANSFER_DEPARTMENTS) or sorted(
        TRANSFER_DEPARTMENTS
    )
    try:
        return ImprovementDraftContext(
            request_type=request["request_type"],
            product_code=request["product_code"],
            fault_id=request["fault_id"] if fault else None,
            doc_signal=None if fault else request["doc_signal"],
            tickets=list(tickets.values()),
            cases=cases,
            condition=ConditionSummary(
                condition_status=request["condition_status"], summary=request["condition_summary"]
            ),
            candidate_depts=candidates,
            as_of=as_of,
        )
    except ValueError:
        raise invalid_input() from None


def invalid_input() -> ApplicationError:
    return ApplicationError(
        422,
        "CS_IMPROVEMENT_DRAFT_INPUT_INVALID",
        "The request has no usable evidence for a draft.",
    )


def prepared(context: ImprovementDraftContext) -> ImprovementDraftInput:
    try:
        return build_improvement_draft_input(context, sensitive_values={}).payload
    except ValueError:
        raise invalid_input() from None


def input_sha256(payload: ImprovementDraftInput) -> str:
    """초안 입력 판본. 작성 시각(as_of)은 빼서 같은 근거면 같은 값이다. 근거를 바꾸면 달라진다."""
    data = payload.model_dump(mode="json")
    data.pop("as_of")
    encoded = json.dumps(
        {"v": IMPROVEMENT_DRAFT_INPUT_VERSION, **data},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def references(rows: list[Row]) -> dict[str, dict[str, str | None]]:
    """초안의 근거 참조 → 문의·사례 ID. 화면이 참조를 원문 링크로 바꾼다."""
    refs: dict[str, dict[str, str | None]] = {}
    for row in rows:
        refs.setdefault(ticket_ref(row), {"ticket_id": str(row["ticket_id"]), "case_id": None})
        if row["case_id"] is not None:
            refs.setdefault(
                case_ref(row), {"ticket_id": str(row["ticket_id"]), "case_id": str(row["case_id"])}
            )
    return refs


def record(row: Row, current_sha: str | None) -> AiDraftRecord:
    stored = row["draft"]
    stale = row["input_sha256"] != current_sha or row["prompt_version"] != PROMPT_VERSION
    return AiDraftRecord.model_validate(
        {
            **row,
            **stored["draft"],
            "evidence": [AiDraftEvidence(ref=ref, **ids) for ref, ids in stored["refs"].items()],
            "stale": stale,
        }
    )


def attempt(row: Row | None) -> AiDraftAttempt | None:
    return AiDraftAttempt.model_validate(row) if row else None


async def load(conn: Any, principal: Principal, request_id: UUID) -> tuple[Row, list[Row]]:
    await check_visible(conn, principal, request_id)
    row = await requests.get_request(conn, request_id)
    if row is None:
        raise not_found()
    return row, await repository.evidence_rows(conn, request_id)


@router.get(
    "",
    response_model=SuccessResponse[ImprovementAiDraftView],
    operation_id="cs_get_improvement_ai_draft",
    openapi_extra={**PARTIAL, "x-activation": "CS_IMPROVEMENT_DRAFT_ENABLED"},
)
async def get_ai_draft(
    request_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[ImprovementAiDraftView]:
    """저장된 AI 작성 초안. 만들지는 않는다(비용 없음)."""
    draft_provider(request)
    async with connection(request.app.state.settings) as conn:
        row, rows = await load(conn, principal, request_id)
        try:
            sha: str | None = input_sha256(prepared(context_for(row, rows, datetime.now(UTC))))
        except ApplicationError:
            # 근거를 모두 뺀 요청이어도 저장본 조회는 된다. 지금 근거 기준으로는 오래된 것으로 본다.
            sha = None
        valid = (
            await repository.matching(conn, request_id, sha, PROMPT_VERSION) if sha else None
        ) or await repository.latest(conn, request_id, valid=True)
        last = await repository.latest(conn, request_id, valid=False)
    view = ImprovementAiDraftView(
        request_id=request_id,
        draft=record(valid, sha) if valid else None,
        latest_attempt=attempt(last),
    )
    return respond(request, view)


@router.post(
    "",
    response_model=SuccessResponse[ImprovementAiDraftView],
    status_code=201,
    operation_id="cs_create_improvement_ai_draft",
    responses={200: {"model": SuccessResponse[ImprovementAiDraftView]}},
    openapi_extra={**PARTIAL, "x-activation": "CS_IMPROVEMENT_DRAFT_ENABLED"},
)
async def create_ai_draft(
    request_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> Any:
    """AI 작성 초안을 만든다. 같은 근거 입력의 성공한 초안이 있으면 그대로 돌려준다(200, 모델 호출 없음)."""
    provider = draft_provider(request)
    async with connection(request.app.state.settings) as conn:
        # permissions.md 1.3: 범위(404) 다음 역할(403), 그다음 상태(409).
        row, rows = await load(conn, principal, request_id)
        require_writer(principal)
        check_open(row)
    context = context_for(row, rows, datetime.now(UTC))
    payload = prepared(context)
    sha = input_sha256(payload)
    locks: dict[tuple[UUID, str], list[Any]] = request.app.state.improvement_draft_locks
    key = (request_id, sha)
    # [잠금, 쓰는 요청 수]. 마지막 요청이 나갈 때만 지운다(요약과 같은 이유).
    entry = locks.setdefault(key, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            return await make(request, provider, principal, row, rows, context, payload, sha)
    finally:
        entry[1] -= 1
        if entry[1] == 0:
            locks.pop(key, None)


async def existing_view(request: Request, request_id: UUID, sha: str) -> Any:
    """같은 입력·판본의 성공한 초안이 있으면 200 응답, 없으면 None."""
    async with connection(request.app.state.settings) as conn:
        existing = await repository.matching(conn, request_id, sha, PROMPT_VERSION)
        last = await repository.latest(conn, request_id, valid=False)
    if not existing:
        return None
    view = ImprovementAiDraftView(
        request_id=request_id, draft=record(existing, sha), latest_attempt=attempt(last)
    )
    return JSONResponse(respond(request, view).model_dump(mode="json"), status_code=200)


def usage_values(usage: Any, model: str) -> dict[str, Any]:
    cost, pricing = usage_cost(usage, model)
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "thought_tokens": usage.thought_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "total_tokens": usage.total_tokens,
        "cost_usd": cost,
        "pricing_version": pricing,
    }


async def make(
    request: Request,
    provider: Provider,
    principal: Principal,
    row: Row,
    rows: list[Row],
    context: ImprovementDraftContext,
    payload: ImprovementDraftInput,
    sha: str,
) -> Any:
    """모델을 한 번 불러 결과를 저장한다. 잠금 안에서 저장본을 다시 확인한다."""
    request_id = row["id"]
    reused = await existing_view(request, request_id, sha)
    if reused is not None:
        return reused
    model = request.app.state.improvement_draft_model
    values: Row = {
        "id": uuid4(),
        "request_id": request_id,
        "request_version": row["version"],
        "input_version": IMPROVEMENT_DRAFT_INPUT_VERSION,
        "input_sha256": sha,
        "prompt_version": PROMPT_VERSION,
        "requested_model": model,
        "requested_by": principal.user_id,
    }
    # DB 연결을 잡은 채 모델을 기다리지 않는다.
    loop = asyncio.get_running_loop()
    try:
        generation = await loop.run_in_executor(
            request.app.state.improvement_draft_executor, provider, render_prompt(payload)
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
            output = validate_output(generation.text, context=context)
        except ValueError as error:
            code = str(error) if str(error).startswith("CS_") else "CS_INVALID_OUTPUT"
            values.update(outcome="INVALID_OUTPUT", error_code=code)
        else:
            values.update(
                outcome="VALID",
                draft={"draft": output.model_dump(mode="json"), "refs": references(rows)},
            )
    async with connection(request.app.state.settings) as conn:
        stored = await repository.insert(conn, values)
    if stored is None:
        # 다른 인스턴스가 같은 입력의 초안을 먼저 저장했다(고유 색인). 그것을 돌려준다.
        reused = await existing_view(request, request_id, sha)
        assert reused is not None
        return reused
    if stored["outcome"] != "VALID":
        raise ApplicationError(
            502,
            "CS_IMPROVEMENT_DRAFT_FAILED",
            "The draft could not be made. Try again later.",
            [{"code": str(stored["error_code"])}],
        )
    view = ImprovementAiDraftView(
        request_id=request_id, draft=record(stored, sha), latest_attempt=attempt(stored)
    )
    return respond(request, view)
