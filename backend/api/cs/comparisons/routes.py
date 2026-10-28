"""조건 비교 결과 조회 API(KAN-114, permissions.md 1.3·4.1). 판정 쓰기는 다음 단위다."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from backend.api.cs.comparisons import repository
from backend.api.cs.comparisons.examples import openapi_examples
from backend.api.cs.comparisons.schemas import ConditionComparison
from backend.api.cs.comparisons.view import build, hide
from backend.api.cs.improvements import repository as requests
from backend.api.cs.improvements.routes import check_visible, require_list, scope
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
    """판본을 읽고 조회 범위 밖 근거를 숨겨 응답을 만든다.

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
    rows = await repository.items(conn, comparison["id"], version["version"])
    evidence = await repository.evidence(conn, comparison["id"], version["version"])
    visible = None
    if not sees_all_tickets(principal):
        tickets = [e["ticket_id"] for e in evidence if e["ticket_id"] is not None]
        visible = await repository.visible_tickets(conn, tickets, transfer_departments(principal))
    shown, kept, hidden_count = hide(rows, evidence, visible)
    target = version["improvement_request_id"]
    request_row = await requests.get_request(conn, target) if target else None
    return respond(request, build(comparison, version, request_row, shown, kept, hidden_count))


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
        if not sees_all_tickets(principal):
            row = await repository.get_version(
                conn, comparison["id"], version or comparison["latest_version"]
            )
            departments, proposed = scope(principal)
            target = row["improvement_request_id"] if row else None
            if (
                target is None
                or not departments
                or not await requests.visible_to(conn, target, departments, proposed)
            ):
                raise not_found()
        return await respond_with(request, conn, principal, comparison, version)


@router.get(
    "/api/v1/cs/improvement-requests/{request_id}/condition-comparison",
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
        await check_visible(conn, principal, request_id)
        row = await requests.get_request(conn, request_id)
        if row is None:
            raise request_not_found()
        if row["request_type"] != "FAULT":
            raise not_found()
        comparison = await repository.get_comparison(conn, row["product_code"], row["fault_id"])
        if comparison is None:
            raise not_found()
        return await respond_with(request, conn, principal, comparison, version)
