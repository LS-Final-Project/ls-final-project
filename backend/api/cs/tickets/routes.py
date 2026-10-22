import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request

from backend.api.cs.tickets import repository
from backend.api.cs.tickets.schemas import (
    ResolutionCase,
    TicketDetail,
    TicketList,
    TicketSummary,
)
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

router = APIRouter(prefix="/api/v1/cs/tickets", tags=["tickets"])

# Access checks by role, department and project are added by KAN-78.
PARTIAL = {"x-implementation-status": "partial"}


def encode_cursor(received_at: datetime, ticket_id: UUID) -> str:
    raw = json.dumps({"r": received_at.isoformat(), "i": str(ticket_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        data = json.loads(base64.urlsafe_b64decode(value.encode()))
        received_at = datetime.fromisoformat(data["r"])
        if received_at.tzinfo is None:
            raise ValueError
        return received_at, UUID(data["i"])
    except (binascii.Error, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise ApplicationError(400, "CS_INVALID_CURSOR", "The page cursor is invalid.") from None


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
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def get_ticket(ticket_id: UUID, request: Request) -> SuccessResponse[TicketDetail]:
    async with connection(request.app.state.settings) as conn:
        row = await repository.get_ticket(conn, ticket_id)
        if row is None:
            raise ApplicationError(404, "CS_TICKET_NOT_FOUND", "The ticket was not found.")
        case = await repository.get_resolution_case(conn, ticket_id)
    detail = TicketDetail.model_validate(
        {**row, "resolution_case": ResolutionCase.model_validate(case) if case else None}
    )
    return SuccessResponse(data=detail, meta=ResponseMeta(request_id=request.state.request_id))
