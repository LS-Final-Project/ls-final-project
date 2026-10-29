"""Approved customer sends: hand-over to the platform and status read-back (KAN-110).

Two steps, so that the ticket state follows what actually happened:

1. `approve` (inside the caller's transaction): the send row and the workflow audit are
   written and the workflow version advances. A repeated approval on the old version is a
   version conflict, so a client retry cannot send twice.
2. `submit` (outside any transaction): the platform is called. Same version and content
   gives the same intent, so a retry after an unknown outcome does not send twice either.

The ticket moves to ANSWERED or WAITING_CUSTOMER only when the platform reports ACCEPTED
(`refresh`), never on approval. A FAILED or REJECTED send leaves the ticket in review.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request

from backend.api.cs.workflow import repository
from backend.api.cs.workflow.mail import (
    PlatformMail,
    PlatformRejected,
    PlatformUnknown,
    PlatformUnreachable,
    platform_mail,
)
from backend.api.cs.workflow.service import record_customer_send, save
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.mail_payload import payload_sha256

Row = dict[str, Any]
RETRYABLE = ("PENDING", "REQUEST_FAILED", "REQUEST_UNKNOWN")


def business_ref(ticket_id: UUID) -> str:
    return f"cs_ticket:{ticket_id}"


async def approve(
    conn: Any,
    workflow: Row,
    actor: UUID,
    *,
    kind: str,
    mailbox_id: UUID,
    subject: str,
    body_type: str,
    body: str,
    to: list[str],
    cc: list[str],
    draft_id: UUID | None = None,
    draft_option: str | None = None,
) -> tuple[Row, Row]:
    """Writes the approved send and advances the workflow version (same transaction).

    Returns:
        tuple: (send row, workflow after).
    """
    send: Row = {
        "id": uuid4(),
        "ticket_id": workflow["ticket_id"],
        "kind": kind,
        "workflow_version": workflow["version"],
        "mailbox_id": mailbox_id,
        "subject": subject,
        "body_type": body_type,
        "body": body,
        "to_addresses": to,
        "cc_addresses": cc,
        "payload_sha256": payload_sha256(
            subject=subject, body_type=body_type, body=body, to=to, cc=cc, attachments=[]
        ),
        "approved_by": actor,
        "approved_at": await repository.database_now(conn),
        "request_status": "PENDING",
        "draft_id": draft_id,
        "draft_option": draft_option,
    }
    await repository.insert_send(conn, send)
    workflow = await save(
        conn,
        workflow,
        actor,
        "approve_send",
        {},
        send_id=send["id"],
        kind=kind,
        recipients=len(to) + len(cc),
        # Draft review trail (KAN-110): which AI draft and option the approval came from.
        draft_id=draft_id,
        draft_option=draft_option,
    )
    return send, workflow


def intent_body(send: Row) -> dict[str, Any]:
    return {
        "mailbox_id": str(send["mailbox_id"]),
        "business_ref": business_ref(send["ticket_id"]),
        "business_version": send["workflow_version"],
        "approved_by": str(send["approved_by"]),
        "approved_at": send["approved_at"].isoformat(),
        "subject": send["subject"],
        "body_type": send["body_type"],
        "body": send["body"],
        "to": list(send["to_addresses"]),
        "cc": list(send["cc_addresses"]),
        "attachments": [],
        "approved_payload_sha256": send["payload_sha256"],
    }


async def submit(request: Request, client: PlatformMail, send: Row) -> Row:
    """Calls the platform for a PENDING or retryable send and stores the outcome.

    Returns:
        Row: the fields written to the send row.
    """
    values: Row
    try:
        data = await client.create_intent(intent_body(send))
        values = {
            "request_status": "REGISTERED",
            "request_error": None,
            "intent_id": UUID(str(data["intent_id"])),
            "send_status": str(data["status"]),
            "status_checked_at": datetime.now(UTC),
        }
    except PlatformRejected as error:
        values = {"request_status": "REJECTED", "request_error": error.code}
    except PlatformUnreachable:
        values = {"request_status": "REQUEST_FAILED", "request_error": "PLATFORM_UNREACHABLE"}
    except (PlatformUnknown, KeyError, ValueError):
        values = {"request_status": "REQUEST_UNKNOWN", "request_error": "PLATFORM_NO_ANSWER"}
    async with connection(request.app.state.settings) as conn:
        await repository.update_send(conn, send["id"], values)
    return values


def status_values(data: dict[str, Any]) -> Row:
    """The cache fields from a platform status answer."""

    def when(name: str) -> datetime | None:
        value = data.get(name)
        return datetime.fromisoformat(value) if isinstance(value, str) else None

    return {
        "send_status": str(data["status"]),
        "send_error_code": data.get("error_code"),
        "attempts": int(data.get("attempts") or 0),
        "accepted_at": when("accepted_at"),
        "sent_copy_confirmed_at": when("sent_copy_confirmed_at"),
        "status_checked_at": datetime.now(UTC),
    }


async def read_status(client: PlatformMail, send: Row) -> Row:
    try:
        return status_values(await client.read_intent(send["intent_id"]))
    except (PlatformUnreachable, PlatformUnknown, PlatformRejected, KeyError, ValueError):
        raise ApplicationError(
            503, "CS_PLATFORM_UNAVAILABLE", "The platform did not answer the status request."
        ) from None


async def apply_status(conn: Any, workflow: Row, send: Row, values: Row) -> Row:
    """Stores read-back status (send row locked by the caller) and, on the first ACCEPTED,
    moves the ticket (IN_REVIEW → ANSWERED / WAITING_CUSTOMER). Returns the workflow after.

    The ticket is still IN_REVIEW here because transfer and close refuse while a send is
    unsettled. If it is not, the status is cached and the move is left for a later refresh.
    """
    if (
        values["send_status"] == "ACCEPTED"
        and send["recorded_at"] is None
        and workflow["state"] == "IN_REVIEW"
    ):
        workflow = await record_customer_send(
            conn,
            workflow["ticket_id"],
            send["kind"],
            workflow["version"],
            send["intent_id"],
            send["approved_by"],
        )
        values = {**values, "recorded_at": datetime.now(UTC)}
    await repository.update_send(conn, send["id"], values)
    return workflow


async def refresh_registered(request: Request, sends: list[Row]) -> None:
    """Reads the platform status of registered sends that are not settled yet, so a decision
    (closing) rests on the current state. Each send is updated in its own transaction."""
    from backend.api.cs.workflow.mail import unsettled
    from backend.api.cs.workflow.service import lock

    pending = [s for s in sends if s["request_status"] == "REGISTERED" and unsettled(s)]
    if not pending:
        return
    client = platform_mail(request)
    for send in pending:
        values = await read_status(client, send)
        async with connection(request.app.state.settings) as conn:
            workflow = await lock(conn, send["ticket_id"])
            locked = await repository.lock_send(conn, send["ticket_id"], send["id"])
            if locked is not None:
                await apply_status(conn, workflow, locked, values)
