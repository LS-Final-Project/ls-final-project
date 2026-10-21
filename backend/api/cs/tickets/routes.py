from fastapi import APIRouter, HTTPException

from backend.api.cs.tickets.schemas import TicketList
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(prefix="/api/v1/cs/tickets", tags=["tickets"])


@router.get(
    "",
    response_model=SuccessResponse[TicketList],
    operation_id="cs_list_tickets",
    responses={501: {"model": ErrorResponse}},
    openapi_extra={"x-implementation-status": "stub"},
)
async def list_tickets() -> SuccessResponse[TicketList]:
    """Contract stub. Browser mock examples are synthetic; real implementation is pending."""
    raise HTTPException(501)
