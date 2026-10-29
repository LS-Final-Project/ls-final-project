"""Queries for ticket workflows, transfers and department results (infra/migrations/cs)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

Row = dict[str, Any]

WORKFLOW_COLUMNS = (
    "ticket_id, state, version, owner_user_id, customer_sent_at, closed_at, closed_by, "
    "return_subtype_confirmed, return_subtype_confirmed_reason, return_subtype_confirmed_by, "
    "return_subtype_confirmed_at"
)
TRANSFER_COLUMNS = (
    "id, round, dept_code, status, reason, transferred_by, transferred_at, ended_by, "
    "ended_at, end_reason"
)
RESULT_COLUMNS = (
    "id, transfer_id, dept_code, version, result, reply_content, submitted_by, submitted_at, "
    "review_status, reviewed_by, reviewed_at, review_note"
)
# Workflow columns an action may set. Routes build SQL names only from this tuple.
WORKFLOW_FIELDS = ("state", "owner_user_id", "customer_sent_at", "closed_at", "closed_by")


async def lock_workflow(conn: AsyncConnection[Row], ticket_id: UUID) -> Row | None:
    """Call after locking the ticket row, so every workflow change is ordered by that lock."""
    cursor = await conn.execute(
        f"SELECT {WORKFLOW_COLUMNS} FROM cs.ticket_workflows WHERE ticket_id = %s FOR UPDATE",
        (ticket_id,),
    )
    return await cursor.fetchone()


async def get_workflow(conn: AsyncConnection[Row], ticket_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {WORKFLOW_COLUMNS} FROM cs.ticket_workflows WHERE ticket_id = %s", (ticket_id,)
    )
    return await cursor.fetchone()


async def insert_workflow(conn: AsyncConnection[Row], ticket_id: UUID, values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.ticket_workflows (ticket_id, {columns}) "
        f"VALUES (%s, {', '.join(['%s'] * len(values))})",
        (ticket_id, *values.values()),
    )


async def update_workflow(
    conn: AsyncConnection[Row], ticket_id: UUID, version: int, values: Row
) -> None:
    assignments = ", ".join(f"{name} = %s" for name in values)
    separator = ", " if assignments else ""
    await conn.execute(
        f"UPDATE cs.ticket_workflows SET {assignments}{separator}version = version + 1, "
        "updated_at = now() WHERE ticket_id = %s AND version = %s",
        (*values.values(), ticket_id, version),
    )


async def list_transfers(conn: AsyncConnection[Row], ticket_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {TRANSFER_COLUMNS} FROM cs.ticket_transfers WHERE ticket_id = %s ORDER BY round",
        (ticket_id,),
    )
    return await cursor.fetchall()


async def list_results(conn: AsyncConnection[Row], ticket_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {RESULT_COLUMNS} FROM cs.dept_results WHERE ticket_id = %s "
        "ORDER BY submitted_at, id",
        (ticket_id,),
    )
    return await cursor.fetchall()


async def insert_transfer(conn: AsyncConnection[Row], values: Row) -> None:
    await conn.execute(
        "INSERT INTO cs.ticket_transfers (id, ticket_id, round, dept_code, status, reason, "
        "transferred_by) VALUES (%(id)s, %(ticket_id)s, "
        "(SELECT COALESCE(max(round), 0) + 1 FROM cs.ticket_transfers "
        "WHERE ticket_id = %(ticket_id)s), %(dept_code)s, 'OPEN', %(reason)s, %(transferred_by)s)",
        values,
    )


async def end_transfer(
    conn: AsyncConnection[Row],
    transfer_id: UUID,
    status: str,
    actor: UUID,
    reason: str | None,
) -> None:
    await conn.execute(
        "UPDATE cs.ticket_transfers SET status = %s, ended_by = %s, ended_at = now(), "
        "end_reason = %s WHERE id = %s AND status = 'OPEN'",
        (status, actor, reason, transfer_id),
    )


async def insert_result(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.dept_results ({columns}) VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


async def review_result(
    conn: AsyncConnection[Row],
    result_id: UUID,
    version: int,
    status: str,
    actor: UUID,
    note: str | None,
) -> None:
    await conn.execute(
        "UPDATE cs.dept_results SET review_status = %s, reviewed_by = %s, reviewed_at = now(), "
        "review_note = %s, version = version + 1 "
        "WHERE id = %s AND version = %s AND review_status = 'SUBMITTED'",
        (status, actor, note, result_id, version),
    )


async def insert_audit(
    conn: AsyncConnection[Row],
    event_id: UUID,
    entity_type: str,
    entity_id: UUID,
    action: str,
    version: int,
    actor_user_id: UUID | None,
    changes: Row,
) -> None:
    await conn.execute(
        "INSERT INTO cs.audit_events "
        "(id, entity_type, entity_id, action, version, actor_user_id, changes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (event_id, entity_type, entity_id, action, version, actor_user_id, Jsonb(changes)),
    )


async def has_case(conn: AsyncConnection[Row], ticket_id: UUID) -> bool:
    # Any case, known or not: a ticket has at most one (UNIQUE ticket_id).
    cursor = await conn.execute(
        "SELECT 1 FROM cs.resolution_cases WHERE ticket_id = %s", (ticket_id,)
    )
    return await cursor.fetchone() is not None


async def insert_case(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.resolution_cases ({columns}) VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


async def database_now(conn: AsyncConnection[Row]) -> datetime:
    """Transaction time of the database, so a value stored now passes `known_at <= now()`."""
    cursor = await conn.execute("SELECT now() AS now")
    row = await cursor.fetchone()
    assert row is not None
    return row["now"]


# --- Customer mail (KAN-110) ---------------------------------------------------------------

SEND_COLUMNS = (
    "id, ticket_id, kind, workflow_version, mailbox_id, subject, body_type, body, to_addresses, "
    "cc_addresses, payload_sha256, approved_by, approved_at, request_status, request_error, "
    "intent_id, send_status, send_error_code, attempts, accepted_at, sent_copy_confirmed_at, "
    "status_checked_at, recorded_at, created_at"
)
# Send columns the platform answer may set. SQL names are built only from this tuple.
SEND_FIELDS = (
    "request_status",
    "request_error",
    "intent_id",
    "send_status",
    "send_error_code",
    "attempts",
    "accepted_at",
    "sent_copy_confirmed_at",
    "status_checked_at",
    "recorded_at",
)


async def list_sends(conn: AsyncConnection[Row], ticket_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {SEND_COLUMNS} FROM cs.ticket_customer_sends WHERE ticket_id = %s "
        "ORDER BY created_at, id",
        (ticket_id,),
    )
    return await cursor.fetchall()


async def lock_send(conn: AsyncConnection[Row], ticket_id: UUID, send_id: UUID) -> Row | None:
    """Call after locking the ticket row."""
    cursor = await conn.execute(
        f"SELECT {SEND_COLUMNS} FROM cs.ticket_customer_sends WHERE ticket_id = %s AND id = %s "
        "FOR UPDATE",
        (ticket_id, send_id),
    )
    return await cursor.fetchone()


async def insert_send(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.ticket_customer_sends ({columns}) "
        f"VALUES ({', '.join(['%s'] * len(values))})",
        tuple(
            Jsonb(v) if name in ("to_addresses", "cc_addresses") else v
            for name, v in values.items()
        ),
    )


async def update_send(conn: AsyncConnection[Row], send_id: UUID, values: Row) -> None:
    unknown = set(values) - set(SEND_FIELDS)
    assert not unknown, unknown
    assignments = ", ".join(f"{name} = %s" for name in values)
    await conn.execute(
        f"UPDATE cs.ticket_customer_sends SET {assignments}, updated_at = now() WHERE id = %s",
        (*values.values(), send_id),
    )


async def get_mail_origin(conn: AsyncConnection[Row], ticket_id: UUID) -> Row | None:
    cursor = await conn.execute(
        "SELECT ticket_id, message_id, mailbox_id, sender_address, received_at, registered_by, "
        "created_at FROM cs.ticket_mail_origins WHERE ticket_id = %s",
        (ticket_id,),
    )
    return await cursor.fetchone()


async def ticket_for_message(conn: AsyncConnection[Row], message_id: UUID) -> UUID | None:
    cursor = await conn.execute(
        "SELECT ticket_id FROM cs.ticket_mail_origins WHERE message_id = %s", (message_id,)
    )
    row = await cursor.fetchone()
    return row["ticket_id"] if row else None


async def insert_mail_origin(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.ticket_mail_origins ({columns}) "
        f"VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


async def list_replies(conn: AsyncConnection[Row], ticket_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        "SELECT message_id, ticket_id, method, received_at, reopened, created_at "
        "FROM cs.ticket_customer_replies WHERE ticket_id = %s ORDER BY received_at, message_id",
        (ticket_id,),
    )
    return await cursor.fetchall()


async def insert_reply(conn: AsyncConnection[Row], values: Row) -> bool:
    """Records a linked reply once. Returns False when the mail was recorded before."""
    columns = ", ".join(values)
    cursor = await conn.execute(
        f"INSERT INTO cs.ticket_customer_replies ({columns}) "
        f"VALUES ({', '.join(['%s'] * len(values))}) ON CONFLICT (message_id) DO NOTHING",
        tuple(values.values()),
    )
    return cursor.rowcount == 1
