"""Read queries for C/S tickets. Tables are created by infra/migrations/cs only."""

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

# A ticket without a workflow row has not started review (KAN-108).
STATE = "COALESCE(w.state, 'RECEIVED') AS workflow_state"
FROM_TICKETS = "FROM cs.tickets t LEFT JOIN cs.ticket_workflows w ON w.ticket_id = t.id"
SUMMARY_COLUMNS = (
    "t.id, t.external_id, t.subject, t.data_origin, t.derivation_kind, t.received_at, "
    f"t.channel, t.product_code, {STATE}"
)
DETAIL_COLUMNS = (
    "t.id, t.external_id, t.version, t.data_origin, t.derivation_kind, t.received_at, "
    "t.known_at, t.channel, t.body_language, t.customer_external_id, t.product_code, "
    f"t.subject, t.body, t.fw_version_reported, t.error_code_reported, {STATE}"
)
# Tickets transferred to one of the given departments, now or earlier.
IN_DEPARTMENTS = (
    "EXISTS (SELECT 1 FROM cs.ticket_transfers x "
    "WHERE x.ticket_id = t.id AND x.dept_code = ANY(%s))"
)
CASE_COLUMNS = (
    "id, external_id, data_origin, derivation_kind, resolved_at, known_at, product_code, "
    "fault_id, fw_version_at_issue, symptom, root_cause, root_cause_type, action_taken, "
    "onsite_visit, rma_no, fw_fixed_version, resolved_by_dept, recurrence_prevention, "
    "dept_result_id"
)

Row = dict[str, Any]


async def list_tickets(
    conn: AsyncConnection[Row],
    limit: int,
    after: tuple[datetime, UUID] | None,
    departments: frozenset[str] | None = None,
) -> list[Row]:
    """Newest first. Fetches one extra row so the caller knows whether a next page exists.

    `departments` limits the page to tickets transferred to those departments. None means
    every ticket.
    """
    conditions: list[str] = []
    params: list[Any] = []
    if departments is not None:
        conditions.append(IN_DEPARTMENTS)
        params.append(list(departments))
    if after is not None:
        conditions.append("(t.received_at, t.id) < (%s, %s)")
        params.extend(after)
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    cursor = await conn.execute(
        f"SELECT {SUMMARY_COLUMNS} {FROM_TICKETS} {where}"
        "ORDER BY t.received_at DESC, t.id DESC LIMIT %s",
        (*params, limit + 1),
    )
    return await cursor.fetchall()


async def transferred_to(
    conn: AsyncConnection[Row], ticket_id: UUID, departments: frozenset[str]
) -> bool:
    cursor = await conn.execute(
        f"SELECT 1 FROM cs.tickets t WHERE t.id = %s AND {IN_DEPARTMENTS}",
        (ticket_id, list(departments)),
    )
    return await cursor.fetchone() is not None


async def get_ticket(conn: AsyncConnection[Row], ticket_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {DETAIL_COLUMNS} {FROM_TICKETS} WHERE t.id = %s", (ticket_id,)
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
        f"SELECT {DETAIL_COLUMNS} {FROM_TICKETS} WHERE t.id = %s FOR UPDATE OF t", (ticket_id,)
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
    actor_user_id: UUID,
    changes: Row,
) -> None:
    await conn.execute(
        "INSERT INTO cs.audit_events "
        "(id, entity_type, entity_id, action, version, actor_user_id, changes) "
        "VALUES (%s, 'ticket', %s, %s, %s, %s, %s)",
        (event_id, ticket_id, action, version, actor_user_id, Jsonb(changes)),
    )


async def list_audit(conn: AsyncConnection[Row], ticket_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        "SELECT id, action, version, actor_user_id, changes, occurred_at FROM cs.audit_events "
        "WHERE entity_type = 'ticket' AND entity_id = %s ORDER BY version",
        (ticket_id,),
    )
    return await cursor.fetchall()
