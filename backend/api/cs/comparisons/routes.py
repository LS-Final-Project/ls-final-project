"""조건 비교 결과 API(KAN-114, permissions.md 1.3·4.1): 조회, 생성, 판정 쓰기·재검토."""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request

from backend.api.cs.comparisons import repository, rules
from backend.api.cs.comparisons.examples import conflict_example, openapi_examples
from backend.api.cs.comparisons.schemas import (
    ConditionComparison,
    EvidenceId,
    EvidenceRemoval,
    JudgementWrite,
)
from backend.api.cs.comparisons.view import build, hide
from backend.api.cs.improvements import repository as requests
from backend.api.cs.improvements.routes import check_visible, require_list, require_writer, scope
from backend.api.cs.improvements.routes import not_found as request_not_found
from backend.api.cs.tickets.access import sees_all_tickets, transfer_departments
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    tags=["condition-comparisons"],
    responses={
        401: {"model": ErrorResponse, "description": "No valid user context"},
        403: {"model": ErrorResponse, "description": "The role may not read comparisons"},
        404: {"model": ErrorResponse, "description": "Not found or outside the user's scope"},
        503: {"model": ErrorResponse},
    },
)
Version = Annotated[int | None, Query(ge=1, description="Omit for the latest version.")]
EXAMPLES: dict[int | str, dict[str, Any]] = {
    200: {"content": {"application/json": {"examples": openapi_examples()}}}
}
WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {
        "model": ErrorResponse,
        "description": "Stale version, existing comparison or a transition the table forbids",
        "content": {"application/json": {"examples": conflict_example()}},
    },
    422: {"model": ErrorResponse, "description": "Missing evidence, reason or value"},
}
PATH = "/api/v1/cs/improvement-requests/{request_id}/condition-comparison"
Row = dict[str, Any]


def not_found() -> ApplicationError:
    """비교 결과나 그 판본이 없을 때의 오류를 만든다.

    Returns:
        ApplicationError: 404 CS_COMPARISON_NOT_FOUND.
    """
    return ApplicationError(404, "CS_COMPARISON_NOT_FOUND", "The comparison was not found.")


async def respond_with(
    request: Request, conn: Any, principal: Principal, comparison: Row, wanted: int | None
) -> SuccessResponse[ConditionComparison]:
    """판본을 읽고 조회 범위를 확인한 뒤 범위 밖 근거를 숨겨 응답을 만든다.

    전체 문의를 보지 않는 사용자는 그 판본의 판정 기준 요청서를 볼 수 있을 때만 받는다. 두 경로가
    같은 규칙을 쓰므로 요청서 경로에서 다른 요청서를 기준으로 한 과거 판본도 막힌다.

    Args:
        request: 요청 ID를 담은 요청.
        conn: C/S DB 연결.
        principal: 조회 사용자.
        comparison: 비교 결과 행.
        wanted: 조회할 판본. None이면 최신.

    Returns:
        SuccessResponse[ConditionComparison]: 그 판본의 응답.
    """
    version = await repository.get_version(
        conn, comparison["id"], wanted or comparison["latest_version"]
    )
    if version is None:
        raise not_found()
    target = version["improvement_request_id"]
    if not sees_all_tickets(principal):
        departments, proposed = scope(principal)
        if (
            target is None
            or not departments
            or not await requests.visible_to(conn, target, departments, proposed)
        ):
            raise not_found()
    rows = await repository.items(conn, comparison["id"], version["version"])
    evidence = await repository.evidence(conn, comparison["id"], version["version"])
    visible = None
    if not sees_all_tickets(principal):
        tickets = [e["ticket_id"] for e in evidence if e["ticket_id"] is not None]
        visible = await repository.visible_tickets(conn, tickets, transfer_departments(principal))
    shown, kept, hidden_count = hide(rows, evidence, visible)
    request_row = await requests.get_request(conn, target) if target else None
    return respond(request, build(comparison, version, request_row, shown, kept, hidden_count))


async def fault_request(conn: Any, principal: Principal, request_id: UUID) -> Row:
    """요청서 경로 공통: 요청서 조회 범위를 확인하고 고장 요청서만 받는다.

    Args:
        conn: C/S DB 연결.
        principal: 사용자.
        request_id: 개선요청 ID.

    Returns:
        Row: 개선요청 행. 범위 밖·없음은 CS_REQUEST_NOT_FOUND, 문서 요청서는 404 비교 없음.
    """
    await check_visible(conn, principal, request_id)
    row = await requests.get_request(conn, request_id)
    if row is None:
        raise request_not_found()
    if row["request_type"] != "FAULT":
        raise not_found()
    return row


@router.get(
    "/api/v1/cs/condition-comparisons",
    response_model=SuccessResponse[ConditionComparison],
    operation_id="cs_get_condition_comparison",
    responses=EXAMPLES,
    openapi_extra=PARTIAL,
)
async def get_group_comparison(
    request: Request,
    product_code: Annotated[str, Query(min_length=1, max_length=64)],
    fault_id: Annotated[str, Query(pattern=r"^FLT-[0-9]{3}$")],
    version: Version = None,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[ConditionComparison]:
    """묶음의 비교 결과. 전체 문의를 보지 않는 사용자는 볼 수 있는 요청서에 연결된 비교만 본다.

    Args:
        request: 요청.
        product_code: 제품 코드.
        fault_id: 고장 ID(FLT-000).
        version: 판본. 생략하면 최신.
        principal: 조회 사용자.

    Returns:
        SuccessResponse[ConditionComparison]: 비교 결과 한 판본.
    """
    require_list(principal)
    async with connection(request.app.state.settings) as conn:
        comparison = await repository.get_comparison(conn, product_code, fault_id)
        if comparison is None:
            raise not_found()
        return await respond_with(request, conn, principal, comparison, version)


@router.get(
    PATH,
    response_model=SuccessResponse[ConditionComparison],
    operation_id="cs_get_request_condition_comparison",
    responses=EXAMPLES,
    openapi_extra=PARTIAL,
)
async def get_request_comparison(
    request_id: UUID,
    request: Request,
    version: Version = None,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[ConditionComparison]:
    """고장 요청서 묶음(product_code, fault_id)의 비교 결과. 요청서 조회 범위를 따른다.

    Args:
        request_id: 개선요청 ID.
        request: 요청.
        version: 판본. 생략하면 최신.
        principal: 조회 사용자.

    Returns:
        SuccessResponse[ConditionComparison]: 비교 결과 한 판본.
    """
    require_list(principal)
    async with connection(request.app.state.settings) as conn:
        row = await fault_request(conn, principal, request_id)
        comparison = await repository.get_comparison(conn, row["product_code"], row["fault_id"])
        if comparison is None:
            raise not_found()
        return await respond_with(request, conn, principal, comparison, version)


@router.post(
    PATH,
    status_code=201,
    response_model=SuccessResponse[ConditionComparison],
    operation_id="cs_create_condition_comparison",
    responses=WRITE_ERRORS,
    openapi_extra=PARTIAL,
)
async def create_comparison(
    request_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[ConditionComparison]:
    """고장 요청서 묶음의 비교를 만든다. 판본 1은 사용 조건 8개 모두 근거 없는 미확인이다.

    Args:
        request_id: 판정 기준이 될 고장 개선요청 ID.
        request: 요청.
        principal: cs_agent만 만든다.

    Returns:
        SuccessResponse[ConditionComparison]: 만든 비교의 판본 1. 묶음에 이미 있으면 409.
    """
    async with connection(request.app.state.settings) as conn:
        row = await fault_request(conn, principal, request_id)
        require_writer(principal)
        now = datetime.now(UTC)
        comparison = {
            "id": uuid4(),
            "product_code": row["product_code"],
            "fault_id": row["fault_id"],
            "latest_version": 1,
        }
        if not await repository.insert_comparison(conn, comparison):
            raise ApplicationError(409, "CS_COMPARISON_EXISTS", "The group has a comparison.")
        version = {
            "version": 1,
            "improvement_request_id": row["id"],
            "request_version": row["version"],
            "data_origin": row["data_origin"],
            "known_at": now,
            "created_by": principal.user_id,
        }
        await repository.insert_version(
            conn, comparison["id"], version, rules.initial_items(now), []
        )
        await repository.insert_audit(
            conn,
            uuid4(),
            comparison["id"],
            "create",
            1,
            principal.user_id,
            {"improvement_request_id": {"to": str(row["id"])}},
        )
        return await respond_with(request, conn, principal, comparison, None)


@router.post(
    PATH + "/judgements",
    response_model=SuccessResponse[ConditionComparison],
    operation_id="cs_write_condition_judgements",
    responses=WRITE_ERRORS,
    openapi_extra=PARTIAL,
)
async def write_judgements(
    request_id: UUID,
    payload: JudgementWrite,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[ConditionComparison]:
    """판정을 쓰거나 재검토해 새 판본을 만든다. 보낸 항목만 바뀌고 새 근거를 더할 수 있다.

    Args:
        request_id: 판정 기준이 될 고장 개선요청 ID.
        payload: 읽은 판본, 바꿀 항목, 새 근거.
        request: 요청.
        principal: cs_agent만 쓴다. 사람의 판정(human_input)으로 기록한다.

    Returns:
        SuccessResponse[ConditionComparison]: 새 최신 판본. 판본이 다르면 409.
    """
    async with connection(request.app.state.settings) as conn:
        row = await fault_request(conn, principal, request_id)
        require_writer(principal)
        comparison = await repository.lock_comparison(conn, row["product_code"], row["fault_id"])
        if comparison is None:
            raise not_found()
        if payload.version != comparison["latest_version"]:
            raise ApplicationError(
                409, "CS_COMPARISON_VERSION_CONFLICT", "The comparison has changed."
            )
        tickets, cases = await repository.sources(
            conn,
            [e.ticket_id for e in payload.evidence],
            [e.case_id for e in payload.evidence if e.case_id is not None],
        )
        rules.check_sources(payload, tickets, cases, comparison)
        found = {
            e.evidence_id: cases[e.case_id] if e.case_id is not None else tickets[e.ticket_id]
            for e in payload.evidence
        }
        latest = comparison["latest_version"]
        now = datetime.now(UTC)
        items, evidence, changes = rules.next_version(
            await repository.items(conn, comparison["id"], latest),
            await repository.evidence(conn, comparison["id"], latest),
            payload,
            found,
            "human_input",
            principal.user_id,
            now,
            await repository.removals(conn, comparison["id"]),
        )
        version = {
            "version": latest + 1,
            "improvement_request_id": row["id"],
            "request_version": row["version"],
            "data_origin": rules.data_origin(evidence, row["data_origin"]),
            "known_at": now,
            "created_by": principal.user_id,
        }
        await repository.insert_version(conn, comparison["id"], version, items, evidence)
        await repository.set_latest(conn, comparison["id"], latest + 1)
        await repository.insert_audit(
            conn, uuid4(), comparison["id"], "update", latest + 1, principal.user_id, changes
        )
        fresh = comparison | {"latest_version": latest + 1}
        return await respond_with(request, conn, principal, fresh, None)


@router.post(
    PATH + "/evidence/{evidence_id}/remove",
    response_model=SuccessResponse[ConditionComparison],
    operation_id="cs_remove_condition_comparison_evidence",
    responses=WRITE_ERRORS,
    openapi_extra=PARTIAL,
)
async def remove_comparison_evidence(
    request_id: UUID,
    evidence_id: EvidenceId,
    payload: EvidenceRemoval,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[ConditionComparison]:
    """근거 하나를 사유와 함께 빼고 새 판본을 만든다. 이전 판본과 제외 이력은 남는다.

    Args:
        request_id: 판정 기준이 될 고장 개선요청 ID.
        evidence_id: 뺄 비교 근거 ID.
        payload: 읽은 판본과 제외 사유.
        request: 요청.
        principal: cs_agent만 뺀다.

    Returns:
        SuccessResponse[ConditionComparison]: 새 최신 판본. 근거가 없으면 404, 판정 조건이
        깨지는 제외(확정, 근거가 남은 의심)는 409.
    """
    async with connection(request.app.state.settings) as conn:
        row = await fault_request(conn, principal, request_id)
        require_writer(principal)
        comparison = await repository.lock_comparison(conn, row["product_code"], row["fault_id"])
        if comparison is None:
            raise not_found()
        if payload.version != comparison["latest_version"]:
            raise ApplicationError(
                409, "CS_COMPARISON_VERSION_CONFLICT", "The comparison has changed."
            )
        latest = comparison["latest_version"]
        now = datetime.now(UTC)
        items, evidence, changes, affected = rules.remove_evidence(
            await repository.items(conn, comparison["id"], latest),
            await repository.evidence(conn, comparison["id"], latest),
            evidence_id,
            payload.reason,
            await repository.removals(conn, comparison["id"]),
            now,
        )
        version = {
            "version": latest + 1,
            "improvement_request_id": row["id"],
            "request_version": row["version"],
            "data_origin": rules.data_origin(evidence, row["data_origin"]),
            "known_at": now,
            "created_by": principal.user_id,
        }
        await repository.insert_version(conn, comparison["id"], version, items, evidence)
        await repository.set_latest(conn, comparison["id"], latest + 1)
        await repository.insert_removal(
            conn,
            {
                "comparison_id": comparison["id"],
                "evidence_id": evidence_id,
                "version": latest + 1,
                "item_keys": affected,
                "reason": payload.reason,
                "removed_by": principal.user_id,
            },
        )
        await repository.insert_audit(
            conn, uuid4(), comparison["id"], "update", latest + 1, principal.user_id, changes
        )
        fresh = comparison | {"latest_version": latest + 1}
        return await respond_with(request, conn, principal, fresh, None)
