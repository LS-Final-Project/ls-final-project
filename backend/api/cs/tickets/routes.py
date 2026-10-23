import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.cs.tickets import repository
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
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse


def guard_until_access_checks(request: Request) -> None:
    # No role, department or project checks exist yet (KAN-78). Fail closed outside
    # local and test so ticket bodies and customer IDs are never exposed in production.
    if request.app.state.settings.environment not in {"local", "test"}:
        raise HTTPException(404)


router = APIRouter(
    prefix="/api/v1/cs/tickets",
    tags=["tickets"],
    dependencies=[Depends(guard_until_access_checks)],
    responses={
        404: {"model": ErrorResponse, "description": "Not found, or closed outside local/test"}
    },
)

PARTIAL = {"x-implementation-status": "partial"}


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
) -> SuccessResponse[TicketList]:
    after = decode_cursor(cursor) if cursor else None
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
async def get_ticket(ticket_id: UUID, request: Request) -> SuccessResponse[TicketDetail]:
    async with connection(request.app.state.settings) as conn:
        row = await repository.get_ticket(conn, ticket_id)
        if row is None:
            raise not_found()
        case = await repository.get_resolution_case(conn, ticket_id)
    return respond(request, detail(row, case))


def not_found() -> ApplicationError:
    return ApplicationError(404, "CS_TICKET_NOT_FOUND", "The ticket was not found.")


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
async def create_ticket(payload: TicketCreate, request: Request) -> SuccessResponse[TicketDetail]:
    now = datetime.now(UTC)
    values = {
        "id": uuid4(),
        # Manual input has no source ID. Routes only answer in local/test, so records are demo.
        "source": "manual",
        "source_instance": "cs_api",
        "external_id": None,
        "data_origin": "demo",
        "derivation_kind": "SYSTEM",
        "received_at": now,
        "known_at": now,
        **payload.model_dump(),
    }
    # The original body is immutable and stays in the ticket row, not in the audit trail.
    created = {
        name: {"to": value} for name, value in payload.model_dump().items() if name != "body"
    }
    async with connection(request.app.state.settings) as conn:
        await repository.insert_ticket(conn, values)
        await repository.insert_audit(conn, uuid4(), values["id"], "create", 1, created)
    return respond(request, detail({**values, "version": 1}, None))


@router.patch(
    "/{ticket_id}",
    response_model=SuccessResponse[TicketDetail],
    operation_id="cs_update_ticket",
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def update_ticket(
    ticket_id: UUID, payload: TicketUpdate, request: Request
) -> SuccessResponse[TicketDetail]:
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
        if changes:
            values = {name: change["to"] for name, change in changes.items()}
            await repository.update_ticket(conn, ticket_id, payload.version, values)
            row = {**row, **values, "version": payload.version + 1}
            await repository.insert_audit(
                conn, uuid4(), ticket_id, "update", row["version"], changes
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
async def list_ticket_audit(ticket_id: UUID, request: Request) -> SuccessResponse[AuditList]:
    async with connection(request.app.state.settings) as conn:
        if await repository.get_ticket(conn, ticket_id) is None:
            raise not_found()
        rows = await repository.list_audit(conn, ticket_id)
    return respond(request, AuditList(items=[AuditEvent.model_validate(r) for r in rows]))
