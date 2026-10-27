import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request

from backend.api.cs.tickets import repository
from backend.api.cs.tickets.access import (
    require_ticket_list,
    require_ticket_write,
    sees_all_tickets,
)
from backend.api.cs.tickets.schemas import (
    AuditEvent,
    AuditList,
    ResolutionCase,
    TicketCreate,
    TicketDetail,
    TicketList,
    TicketSummary,
    TicketUpdate,
)
from backend.api.cs.units import repository as units
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind, bind_path_ids
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

router = APIRouter(
    prefix="/api/v1/cs/tickets",
    tags=["tickets"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse, "description": "No valid user context"},
        403: {"model": ErrorResponse, "description": "The role may not take this action"},
        404: {
            "model": ErrorResponse,
            "description": "Not found or outside the user's scope. Production requires an enabled "
            "business relay and a verified platform caller (KAN-178)",
        },
    },
)

PARTIAL = {"x-implementation-status": "partial"}
# Ticket fields a confirmed unit must match.
UNIT_MATCH_FIELDS = {"customer_external_id", "product_code"}


def cursor_error() -> ApplicationError:
    return ApplicationError(400, "CS_INVALID_CURSOR", "The page cursor is invalid.")


def encode_cursor(received_at: datetime, ticket_id: UUID) -> str:
    raw = json.dumps({"r": received_at.isoformat(), "i": str(ticket_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        data = json.loads(base64.urlsafe_b64decode(value.encode()))
    except (binascii.Error, ValueError):
        raise cursor_error() from None
    # A cursor is client input: check its shape before parsing any value.
    if not isinstance(data, dict) or set(data) != {"r", "i"}:
        raise cursor_error()
    received_at, ticket_id = data["r"], data["i"]
    if not isinstance(received_at, str) or not isinstance(ticket_id, str):
        raise cursor_error()
    try:
        parsed_at = datetime.fromisoformat(received_at)
        parsed_id = UUID(ticket_id)
    except ValueError:
        raise cursor_error() from None
    if parsed_at.tzinfo is None:
        raise cursor_error()
    try:
        # Offsets near the datetime limits overflow when normalised (review finding).
        parsed_at = parsed_at.astimezone(UTC)
    except (OverflowError, ValueError):
        raise cursor_error() from None
    return parsed_at, parsed_id


@router.get(
    "",
    response_model=SuccessResponse[TicketList],
    operation_id="cs_list_tickets",
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def list_tickets(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=200),
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[TicketList]:
    require_ticket_list(principal)
    after = decode_cursor(cursor) if cursor else None
    if not sees_all_tickets(principal):
        # Transfer departments arrive with KAN-108; until then no ticket is in this scope.
        return respond(request, TicketList(items=[], next_cursor=None))
    async with connection(request.app.state.settings) as conn:
        rows = await repository.list_tickets(conn, limit, after)
    items = [TicketSummary.model_validate(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(last["received_at"], last["id"])
    return SuccessResponse(
        data=TicketList(items=items, next_cursor=next_cursor),
        meta=ResponseMeta(request_id=request.state.request_id),
    )


@router.get(
    "/{ticket_id}",
    response_model=SuccessResponse[TicketDetail],
    operation_id="cs_get_ticket",
    responses={503: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def get_ticket(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[TicketDetail]:
    require_visible(principal)
    async with connection(request.app.state.settings) as conn:
        row = await repository.get_ticket(conn, ticket_id)
        if row is None:
            raise not_found()
        case = await repository.get_resolution_case(conn, ticket_id)
    return respond(request, detail(row, case))


def not_found() -> ApplicationError:
    return ApplicationError(404, "CS_TICKET_NOT_FOUND", "The ticket was not found.")


def require_visible(principal: Principal) -> None:
    # Out-of-scope tickets look the same as missing ones (permissions.md 1.3 step 2).
    if not sees_all_tickets(principal):
        raise not_found()


def detail(row: dict[str, Any], case: dict[str, Any] | None) -> TicketDetail:
    return TicketDetail.model_validate(
        {**row, "resolution_case": ResolutionCase.model_validate(case) if case else None}
    )


def respond(request: Request, data: Any) -> SuccessResponse[Any]:
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))


@router.post(
    "",
    status_code=201,
    response_model=SuccessResponse[TicketDetail],
    operation_id="cs_create_ticket",
    responses={503: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def create_ticket(
    payload: TicketCreate, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[TicketDetail]:
    """접수 권한을 확인하고 서버가 명시한 출처로 문의와 감사 기록을 함께 저장한다.

    Args:
        payload: 클라이언트가 입력한 문의 필드. 출처·판본·작성자 지정은 허용하지 않는다.
        request: 출처 설정과 DB 연결을 제공하는 요청.
        principal: 호출자·사용자 맥락 검증을 마친 사용자.

    Returns:
        SuccessResponse[TicketDetail]: 서버 판본·출처가 포함된 새 문의.
    """
    require_ticket_write(principal)
    settings = request.app.state.settings
    now = datetime.now(UTC)
    values = {
        "id": uuid4(),
        # 인증용 production 설정과 자료의 실제·시연 여부는 별개다.
        "source": "manual",
        "source_instance": "cs_api",
        "external_id": None,
        "data_origin": (
            settings.cs_manual_data_origin if settings.business_relay_enabled else "demo"
        ),
        "derivation_kind": "SYSTEM",
        "received_at": now,
        "known_at": now,
        **payload.model_dump(),
    }
    bind(ticket_id=values["id"])
    # The original body is immutable and stays in the ticket row, not in the audit trail.
    created = {
        name: {"to": value} for name, value in payload.model_dump().items() if name != "body"
    }
    async with connection(request.app.state.settings) as conn:
        await repository.insert_ticket(conn, values)
        await repository.insert_audit(
            conn, uuid4(), values["id"], "create", 1, principal.user_id, created
        )
    return respond(request, detail({**values, "version": 1}, None))


@router.patch(
    "/{ticket_id}",
    response_model=SuccessResponse[TicketDetail],
    operation_id="cs_update_ticket",
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def update_ticket(
    ticket_id: UUID,
    payload: TicketUpdate,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[TicketDetail]:
    require_visible(principal)
    require_ticket_write(principal)
    async with connection(request.app.state.settings) as conn:
        row = await repository.lock_ticket(conn, ticket_id)
        if row is None:
            raise not_found()
        if row["version"] != payload.version:
            raise ApplicationError(409, "CS_TICKET_VERSION_CONFLICT", "The ticket has changed.")
        changes = {
            name: {"from": row[name], "to": value}
            for name, value in payload.changed_values().items()
            if row[name] != value
        }
        if changes.keys() & UNIT_MATCH_FIELDS and await units.confirmed_link(conn, ticket_id):
            # A confirmed unit was checked against these values (KAN-102). Reject the link first.
            raise ApplicationError(
                409, "CS_TICKET_UNIT_LINKED", "A confirmed unit is linked to this ticket."
            )
        if changes:
            values = {name: change["to"] for name, change in changes.items()}
            await repository.update_ticket(conn, ticket_id, payload.version, values)
            row = {**row, **values, "version": payload.version + 1}
            await repository.insert_audit(
                conn, uuid4(), ticket_id, "update", row["version"], principal.user_id, changes
            )
        case = await repository.get_resolution_case(conn, ticket_id)
    return respond(request, detail(row, case))


@router.get(
    "/{ticket_id}/audit",
    response_model=SuccessResponse[AuditList],
    operation_id="cs_list_ticket_audit",
    responses={503: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def list_ticket_audit(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[AuditList]:
    require_visible(principal)
    async with connection(request.app.state.settings) as conn:
        if await repository.get_ticket(conn, ticket_id) is None:
            raise not_found()
        rows = await repository.list_audit(conn, ticket_id)
    return respond(request, AuditList(items=[AuditEvent.model_validate(r) for r in rows]))
