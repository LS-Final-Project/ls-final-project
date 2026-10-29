"""Routes the platform calls on the C/S service for customer mail (KAN-110).

| Path (`/internal/v1/cs/` below) | Caller | Does |
| --- | --- | --- |
| `POST tickets/from-mail` | platform, on behalf of a cs_agent | registers a received mail as a ticket |
| `POST tickets/{ticket_id}/customer-reply` | platform (no user) | records a linked reply, reopens |

Not in OpenAPI and not reachable through the business relay. The platform caller token is
verified the same way as for relayed requests; the reply route carries no user.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.cs.tickets import repository as tickets
from backend.api.cs.tickets.access import require_ticket_write
from backend.api.cs.tickets.routes import detail, not_found, respond
from backend.api.cs.tickets.schemas import Code, CustomerId, Subject, TicketDetail
from backend.api.cs.workflow import repository
from backend.api.cs.workflow.schemas import CustomerReplyIn, CustomerReplyResult
from backend.api.cs.workflow.service import record_customer_reply
from backend.shared.access import Principal, current_principal, verify_platform_caller
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind, bind_path_ids
from backend.shared.models import SuccessResponse

router = APIRouter(
    prefix="/internal/v1/cs/tickets",
    tags=["internal-customer-mail"],
    include_in_schema=False,
    dependencies=[Depends(bind_path_ids)],
)
Row = dict[str, Any]
MAIL_SOURCE = "microsoft_graph"


class MailTicketCreate(BaseModel):
    """A received mail plus what the agent confirmed before registering it."""

    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    mailbox_id: UUID
    external_id: Annotated[str, Field(min_length=1, max_length=512)] = Field(
        description="Graph message ID of the mail."
    )
    sender_address: Annotated[str, Field(min_length=3, max_length=320)]
    received_at: datetime
    subject: Subject | None = None
    body: Annotated[str, Field(min_length=1, max_length=50_000)]
    body_language: Annotated[str, Field(min_length=2, max_length=16)]
    customer_external_id: CustomerId
    product_code: Code
    fw_version_reported: Code | None = None
    error_code_reported: Code | None = None


@router.post("/from-mail", status_code=201, response_model=SuccessResponse[TicketDetail])
async def register_from_mail(
    payload: MailTicketCreate,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[TicketDetail]:
    """Registers a mail as an EMAIL ticket and keeps the mail reference and sender.

    Source is `microsoft_graph`, `external_id` the Graph ID, `received_at` and `known_at` the
    mail's receipt. The same mail registered twice is 409 with the existing ticket ID.
    """
    require_ticket_write(principal)
    settings = request.app.state.settings
    ticket_id = uuid4()
    bind(ticket_id=ticket_id)
    values: Row = {
        "id": ticket_id,
        "source": MAIL_SOURCE,
        "source_instance": str(payload.mailbox_id),
        "external_id": payload.external_id,
        "data_origin": (
            settings.cs_manual_data_origin if settings.business_relay_enabled else "demo"
        ),
        "derivation_kind": "SYSTEM",
        "received_at": payload.received_at,
        "known_at": payload.received_at,
        "channel": "EMAIL",
        "body_language": payload.body_language,
        "customer_external_id": payload.customer_external_id,
        "product_code": payload.product_code,
        "subject": payload.subject,
        "body": payload.body,
        "fw_version_reported": payload.fw_version_reported,
        "error_code_reported": payload.error_code_reported,
    }
    created = {
        name: {"to": values[name]}
        for name in (
            "channel",
            "body_language",
            "customer_external_id",
            "product_code",
            "subject",
            "fw_version_reported",
            "error_code_reported",
        )
    }
    created["mail_message_id"] = {"to": str(payload.message_id)}
    async with connection(settings) as conn:
        existing = await repository.ticket_for_message(conn, payload.message_id)
        if existing is not None:
            raise ApplicationError(
                409,
                "CS_MAIL_ALREADY_REGISTERED",
                "This mail was registered before.",
                details=[{"field": "ticket_id", "reason": str(existing)}],
            )
        await tickets.insert_ticket(conn, values)
        await repository.insert_mail_origin(
            conn,
            {
                "ticket_id": ticket_id,
                "message_id": payload.message_id,
                "mailbox_id": payload.mailbox_id,
                "sender_address": payload.sender_address,
                "received_at": payload.received_at,
                "registered_by": principal.user_id,
            },
        )
        await tickets.insert_audit(
            conn, uuid4(), ticket_id, "create", 1, principal.user_id, created
        )
    return respond(request, detail({**values, "version": 1, "workflow_state": "RECEIVED"}, None))


@router.post("/{ticket_id}/customer-reply", response_model=SuccessResponse[CustomerReplyResult])
async def customer_reply(
    ticket_id: UUID, payload: CustomerReplyIn, request: Request
) -> SuccessResponse[CustomerReplyResult]:
    """The platform reports a reply it linked to this ticket. WAITING_CUSTOMER, ANSWERED and
    CLOSED go back to IN_REVIEW; other states keep theirs. Reporting the same mail again
    changes nothing."""
    verify_platform_caller(request)
    async with connection(request.app.state.settings) as conn:
        if await tickets.get_ticket(conn, ticket_id) is None:
            raise not_found()
        workflow, reopened, recorded = await record_customer_reply(
            conn, ticket_id, payload.message_id, payload.method, payload.received_at
        )
    return respond(
        request,
        CustomerReplyResult(
            ticket_id=ticket_id,
            message_id=payload.message_id,
            state=workflow["state"],
            reopened=reopened,
            recorded=recorded,
        ),
    )
