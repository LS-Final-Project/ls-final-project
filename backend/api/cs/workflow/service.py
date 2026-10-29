"""Workflow changes shared by the routes and by the mail paths (KAN-74 send, KAN-75 replies).

Every function runs inside the caller's transaction, after the ticket row is locked, and
writes the change and its audit event together.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from backend.api.cs.tickets import repository as tickets
from backend.api.cs.workflow import repository
from backend.shared.errors import ApplicationError

Row = dict[str, Any]

# States from which a customer reply brings the ticket back to review (permissions.md 2.1).
REOPEN_STATES = ("WAITING_CUSTOMER", "ANSWERED", "CLOSED")


def invalid_transition() -> ApplicationError:
    return ApplicationError(
        409, "CS_INVALID_TRANSITION", "The ticket state does not allow this action."
    )


def unreviewed_result() -> ApplicationError:
    return ApplicationError(
        409, "CS_DEPT_RESULT_UNREVIEWED", "A department result is waiting for review."
    )


def json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def not_started(ticket_id: UUID) -> Row:
    return {
        "ticket_id": ticket_id,
        "state": "RECEIVED",
        "version": 0,
        "owner_user_id": None,
        "customer_sent_at": None,
        "closed_at": None,
        "closed_by": None,
        "return_subtype_confirmed": None,
        "return_subtype_confirmed_reason": None,
        "return_subtype_confirmed_by": None,
        "return_subtype_confirmed_at": None,
    }


def check(workflow: Row, version: int, *states: str) -> None:
    if workflow["state"] not in states:
        raise invalid_transition()
    if workflow["version"] != version:
        raise ApplicationError(409, "CS_WORKFLOW_VERSION_CONFLICT", "The workflow has changed.")


async def save(
    conn: Any, workflow: Row, actor: UUID | None, action: str, values: Row, **details: Any
) -> Row:
    """Writes the workflow change and its audit event. Returns the new workflow row.

    `actor` is None only for a system event such as a received customer reply.
    """
    changes: Row = {
        name: {"from": json_value(workflow[name]), "to": json_value(value)}
        for name, value in values.items()
        if workflow[name] != value
    }
    changes["action"] = {"to": action}
    for name, value in details.items():
        changes[name] = {"to": json_value(value)}
    ticket_id = workflow["ticket_id"]
    if workflow["version"] == 0:
        await repository.insert_workflow(conn, ticket_id, {"state": workflow["state"], **values})
    else:
        await repository.update_workflow(conn, ticket_id, workflow["version"], values)
    version = workflow["version"] + 1
    await repository.insert_audit(
        conn, uuid4(), "ticket_workflow", ticket_id, "transition", version, actor, changes
    )
    return {**workflow, **values, "version": version}


async def lock(conn: AsyncConnection[Row], ticket_id: UUID) -> Row:
    if await tickets.lock_ticket(conn, ticket_id) is None:
        raise ApplicationError(404, "CS_TICKET_NOT_FOUND", "The ticket was not found.")
    return await repository.lock_workflow(conn, ticket_id) or not_started(ticket_id)


async def record_customer_send(
    conn: AsyncConnection[Row],
    ticket_id: UUID,
    kind: Literal["ANSWER", "INFO_REQUEST"],
    version: int,
    message_id: UUID,
    actor: UUID,
) -> Row:
    """IN_REVIEW → ANSWERED or WAITING_CUSTOMER once the mail provider accepted the message.

    For the send path (KAN-74) only: there is no route that marks a message as sent by hand.
    `version` is the workflow version the agent approved, and `message_id` the send record.
    A department result waiting for review blocks sending, so only reviewed content reaches
    the customer.
    """
    workflow = await lock(conn, ticket_id)
    check(workflow, version, "IN_REVIEW")
    results = await repository.list_results(conn, ticket_id)
    if any(r["review_status"] == "SUBMITTED" for r in results):
        raise unreviewed_result()
    state = "ANSWERED" if kind == "ANSWER" else "WAITING_CUSTOMER"
    return await save(
        conn,
        workflow,
        actor,
        "send_answer" if kind == "ANSWER" else "send_info_request",
        {"state": state, "customer_sent_at": datetime.now(UTC)},
        message_id=message_id,
    )


async def reopen_for_customer_reply(
    conn: AsyncConnection[Row], ticket_id: UUID, message_id: UUID, actor: UUID | None
) -> Row:
    """A customer reply linked to the ticket (KAN-75) brings it back to IN_REVIEW.

    Replies arrive at any time, so no version is compared. A reply to a ticket that is still
    being reviewed or is with a department does not change the state.
    """
    workflow = await lock(conn, ticket_id)
    if workflow["state"] not in REOPEN_STATES:
        return workflow
    values: Row = {"state": "IN_REVIEW"}
    if workflow["state"] == "CLOSED":
        values |= {"closed_at": None, "closed_by": None}
    return await save(conn, workflow, actor, "customer_reply", values, message_id=message_id)
