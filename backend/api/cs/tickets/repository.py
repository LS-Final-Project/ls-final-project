"""Read queries for C/S tickets. Tables are created by infra/migrations/cs only."""

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

SUMMARY_COLUMNS = (
    "id, external_id, subject, data_origin, derivation_kind, received_at, channel, product_code"
)
DETAIL_COLUMNS = (
    "id, external_id, version, data_origin, derivation_kind, received_at, known_at, channel, "
    "body_language, customer_external_id, product_code, subject, body, fw_version_reported, "
    "error_code_reported"
)
CASE_COLUMNS = (
    "id, external_id, data_origin, derivation_kind, resolved_at, known_at, product_code, "
    "fault_id, fw_version_at_issue, symptom, root_cause, root_cause_type, action_taken, "
    "onsite_visit, rma_no, fw_fixed_version, resolved_by_dept, recurrence_prevention"
)

Row = dict[str, Any]


async def list_tickets(
    conn: AsyncConnection[Row], limit: int, after: tuple[datetime, UUID] | None
) -> list[Row]:
    """Newest first. Fetches one extra row so the caller knows whether a next page exists."""
    if after is None:
        cursor = await conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM cs.tickets ORDER BY received_at DESC, id DESC LIMIT %s",
            (limit + 1,),
        )
    else:
        cursor = await conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM cs.tickets "
            "WHERE (received_at, id) < (%s, %s) "
            "ORDER BY received_at DESC, id DESC LIMIT %s",
            (*after, limit + 1),
        )
    return await cursor.fetchall()


async def get_ticket(conn: AsyncConnection[Row], ticket_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {DETAIL_COLUMNS} FROM cs.tickets WHERE id = %s", (ticket_id,)
    )
    return await cursor.fetchone()


async def get_resolution_case(conn: AsyncConnection[Row], ticket_id: UUID) -> Row | None:
    # A case that is not yet known must not be shown as evidence.
    cursor = await conn.execute(
        f"SELECT {CASE_COLUMNS} FROM cs.resolution_cases "
        "WHERE ticket_id = %s AND known_at <= now()",
        (ticket_id,),
    )
    return await cursor.fetchone()


async def insert_ticket(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    await conn.execute(
        f"INSERT INTO cs.tickets ({columns}) VALUES ({placeholders})", tuple(values.values())
    )


async def lock_ticket(conn: AsyncConnection[Row], ticket_id: UUID) -> Row | None:
    """Locks the row until the transaction ends so version checks cannot interleave."""
    cursor = await conn.execute(
        f"SELECT {DETAIL_COLUMNS} FROM cs.tickets WHERE id = %s FOR UPDATE", (ticket_id,)
    )
    return await cursor.fetchone()


async def update_ticket(
    conn: AsyncConnection[Row], ticket_id: UUID, version: int, values: Row
) -> None:
    # Column names come from schemas.EDITABLE_FIELDS, never from request keys.
    assignments = ", ".join(f"{name} = %s" for name in values)
    await conn.execute(
        f"UPDATE cs.tickets SET {assignments}, version = version + 1, updated_at = now() "
        "WHERE id = %s AND version = %s",
        (*values.values(), ticket_id, version),
    )


async def insert_audit(
    conn: AsyncConnection[Row],
    event_id: UUID,
    ticket_id: UUID,
    action: str,
    version: int,
    changes: Row,
) -> None:
    await conn.execute(
        "INSERT INTO cs.audit_events (id, entity_type, entity_id, action, version, changes) "
        "VALUES (%s, 'ticket', %s, %s, %s, %s)",
        (event_id, ticket_id, action, version, Jsonb(changes)),
    )


async def list_audit(conn: AsyncConnection[Row], ticket_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        "SELECT id, action, version, actor_user_id, changes, occurred_at FROM cs.audit_events "
        "WHERE entity_type = 'ticket' AND entity_id = %s ORDER BY version",
        (ticket_id,),
    )
    return await cursor.fetchall()
