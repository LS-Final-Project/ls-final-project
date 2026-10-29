"""Customer mail actions on a ticket (KAN-110): approve a send, refresh or retry it.

Only cs_agent. The platform (KAN-74) sends; these routes hand over and read back. All routes
answer 404 until the operator enables `CS_MAIL_ENABLED` with the platform URL.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.api.cs.workflow import repository, sends
from backend.api.cs.workflow.mail import platform_mail, unsettled
from backend.api.cs.workflow.routes import begin, require_role, view
from backend.api.cs.workflow.schemas import Action, CustomerSendRequest, Workflow
from backend.api.cs.workflow.service import check, lock, send_pending, unreviewed_result
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind_path_ids
from backend.shared.models import ErrorResponse, SuccessResponse

Row = dict[str, Any]
router = APIRouter(
    prefix="/api/v1/cs/tickets/{ticket_id}/workflow",
    tags=["workflow"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Not found, or customer mail disabled"},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
ACTIVATION = {**PARTIAL, "x-activation": "CS_MAIL_ENABLED"}


def send_not_found() -> ApplicationError:
    return ApplicationError(404, "CS_CUSTOMER_SEND_NOT_FOUND", "The send was not found.")


@router.post(
    "/send",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_send_customer_message",
    openapi_extra=ACTIVATION,
)
async def send_message(
    ticket_id: UUID,
    payload: CustomerSendRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """Approves an answer or an information request and hands it to the platform.

    IN_REVIEW only, one unsettled send at a time, and no department result waiting for
    review. The workflow version advances on approval; the state changes only once the
    platform reports ACCEPTED (refresh). The outcome of the hand-over is in the returned
    send's `request_status`, not in the HTTP status.
    """
    client = platform_mail(request)
    async with connection(request.app.state.settings) as conn:
        workflow, _, results = await begin(conn, principal, ticket_id)
        require_role(principal, "cs_agent")
        check(workflow, payload.version, "IN_REVIEW")
        if any(r["review_status"] == "SUBMITTED" for r in results):
            raise unreviewed_result()
        if any(unsettled(s) for s in await repository.list_sends(conn, ticket_id)):
            raise send_pending()
        to = payload.to
        if not to:
            origin = await repository.get_mail_origin(conn, ticket_id)
            if origin is None:
                raise ApplicationError(
                    422,
                    "CS_RECIPIENT_REQUIRED",
                    "Give recipients: this ticket was not opened from a mail.",
                )
            to = [origin["sender_address"]]
        send, _ = await sends.approve(
            conn,
            workflow,
            principal.user_id,
            kind=payload.kind,
            mailbox_id=payload.mailbox_id,
            subject=payload.subject,
            body_type=payload.body_type,
            body=payload.body,
            to=to,
            cc=payload.cc,
        )
    await sends.submit(request, client, send)
    return await current(request, principal, ticket_id)


@router.post(
    "/sends/{send_id}/refresh",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_refresh_customer_send",
    openapi_extra=ACTIVATION,
)
async def refresh_send(
    ticket_id: UUID,
    send_id: UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """Reads the platform status of a registered send. The first ACCEPTED moves the ticket
    to ANSWERED (answer) or WAITING_CUSTOMER (information request)."""
    client = platform_mail(request)
    async with connection(request.app.state.settings) as conn:
        _, _, _ = await begin(conn, principal, ticket_id)
        require_role(principal, "cs_agent")
        send = await repository.lock_send(conn, ticket_id, send_id)
        if send is None:
            raise send_not_found()
        if send["request_status"] != "REGISTERED":
            raise ApplicationError(
                409, "CS_CUSTOMER_SEND_NOT_REGISTERED", "The platform holds no intent yet."
            )
    values = await sends.read_status(client, send)
    async with connection(request.app.state.settings) as conn:
        workflow = await lock(conn, ticket_id)
        send = await repository.lock_send(conn, ticket_id, send_id)
        if send is None:
            raise send_not_found()
        workflow = await sends.apply_status(conn, workflow, send, values)
        data = await view(conn, workflow, principal)
    return respond(request, data)


@router.post(
    "/sends/{send_id}/retry",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_retry_customer_send",
    openapi_extra=ACTIVATION,
)
async def retry_send(
    ticket_id: UUID,
    send_id: UUID,
    payload: Action,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """Calls the platform again for a send it never answered (PENDING, REQUEST_FAILED,
    REQUEST_UNKNOWN). Same version and content, so the platform returns the existing intent
    if the first call did get through."""
    client = platform_mail(request)
    async with connection(request.app.state.settings) as conn:
        workflow, _, _ = await begin(conn, principal, ticket_id)
        require_role(principal, "cs_agent")
        check(workflow, payload.version, "IN_REVIEW")
        send = await repository.lock_send(conn, ticket_id, send_id)
        if send is None:
            raise send_not_found()
        if send["request_status"] not in sends.RETRYABLE:
            raise ApplicationError(
                409, "CS_CUSTOMER_SEND_NOT_RETRYABLE", "This send cannot be requested again."
            )
        other_sends = await repository.list_sends(conn, ticket_id)
        if any(s["workflow_version"] > send["workflow_version"] for s in other_sends):
            raise ApplicationError(
                409,
                "CS_CUSTOMER_SEND_NOT_RETRYABLE",
                "A newer message has already been approved.",
            )
        if any(s["id"] != send_id and unsettled(s) for s in other_sends):
            raise send_pending()
        # Reserve the pending send while holding the workflow lock. In particular,
        # REQUEST_FAILED otherwise permits a new approval during the network call.
        await repository.update_send(
            conn, send_id, {"request_status": "PENDING", "request_error": None}
        )
    await sends.submit(request, client, send)
    return await current(request, principal, ticket_id)


async def current(request: Request, principal: Principal, ticket_id: UUID) -> SuccessResponse[Any]:
    async with connection(request.app.state.settings) as conn:
        workflow = await lock(conn, ticket_id)
        data = await view(conn, workflow, principal)
    return respond(request, data)
