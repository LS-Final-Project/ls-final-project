"""Read queries for C/S tickets. Tables are created by infra/migrations/cs only."""

from dataclasses import dataclass
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
    f"t.channel, t.product_code, {STATE}, w.owner_user_id"
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
TRANSFERRED_TO = (
    "EXISTS (SELECT 1 FROM cs.ticket_transfers x WHERE x.ticket_id = t.id AND x.dept_code = %s)"
)
LATEST_TRANSFER_STATUS = (
    "(SELECT x.status FROM cs.ticket_transfers x WHERE x.ticket_id = t.id AND x.dept_code = %s "
    "ORDER BY x.round DESC LIMIT 1) = %s"
)
CASE_COLUMNS = (
    "id, external_id, data_origin, derivation_kind, resolved_at, known_at, product_code, "
    "fault_id, fw_version_at_issue, symptom, root_cause, root_cause_type, action_taken, "
    "onsite_visit, rma_no, fw_fixed_version, resolved_by_dept, recurrence_prevention, "
    "dept_result_id, responsibility, non_manufacturing_reason"
)

Row = dict[str, Any]


@dataclass(frozen=True)
class TicketFilters:
    """List filters. None means no condition. The caller has validated every value."""

    states: frozenset[str] | None = None
    channel: str | None = None
    product_code: str | None = None
    customer: str | None = None
    owner: UUID | None = None
    received_from: datetime | None = None
    received_to: datetime | None = None
    q: str | None = None
    dept: str | None = None
    transfer_status: str | None = None


def like_pattern(text: str) -> str:
    """Substring pattern with LIKE wildcards in the text escaped."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def filter_conditions(filters: TicketFilters) -> tuple[list[str], list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if filters.states is not None:
        conditions.append("COALESCE(w.state, 'RECEIVED') = ANY(%s)")
        params.append(sorted(filters.states))
    for column, value in (
        ("t.channel", filters.channel),
        ("t.product_code", filters.product_code),
        ("t.customer_external_id", filters.customer),
        ("w.owner_user_id", filters.owner),
    ):
        if value is not None:
            conditions.append(f"{column} = %s")
            params.append(value)
    if filters.received_from is not None:
        conditions.append("t.received_at >= %s")
        params.append(filters.received_from)
    if filters.received_to is not None:
        conditions.append("t.received_at < %s")
        params.append(filters.received_to)
    if filters.q is not None:
        conditions.append(
            "(t.external_id ILIKE %s ESCAPE '\\' OR t.subject ILIKE %s ESCAPE '\\' "
            "OR t.customer_external_id ILIKE %s ESCAPE '\\')"
        )
        params.extend([like_pattern(filters.q)] * 3)
    if filters.dept is not None:
        conditions.append(TRANSFERRED_TO)
        params.append(filters.dept)
    if filters.transfer_status is not None:
        conditions.append(LATEST_TRANSFER_STATUS)
        params.extend([filters.dept, filters.transfer_status])
    return conditions, params


async def latest_transfers(
    conn: AsyncConnection[Row], ticket_ids: list[UUID], dept: str
) -> dict[UUID, Row]:
    """The latest transfer of each ticket to one department. Ended ones are kept (KAN-202)."""
    cursor = await conn.execute(
        "SELECT DISTINCT ON (ticket_id) ticket_id, round, dept_code, status, transferred_at "
        "FROM cs.ticket_transfers WHERE ticket_id = ANY(%s) AND dept_code = %s "
        "ORDER BY ticket_id, round DESC",
        (ticket_ids, dept),
    )
    return {row["ticket_id"]: row for row in await cursor.fetchall()}


async def list_tickets(
    conn: AsyncConnection[Row],
    limit: int,
    after: tuple[datetime, UUID] | None,
    departments: frozenset[str] | None = None,
    *,
    filters: TicketFilters | None = None,
    ascending: bool = False,
) -> list[Row]:
    """Newest first (or oldest first). Fetches one extra row so the caller knows whether a next
    page exists.

    `departments` limits the page to tickets transferred to those departments. None means
    every ticket. Filters narrow the page further and never widen the scope.
    """
    conditions, params = filter_conditions(filters or TicketFilters())
    if departments is not None:
        conditions.append(IN_DEPARTMENTS)
        params.append(list(departments))
    if after is not None:
        conditions.append(f"(t.received_at, t.id) {'>' if ascending else '<'} (%s, %s)")
        params.extend(after)
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    order = "ASC" if ascending else "DESC"
    cursor = await conn.execute(
        f"SELECT {SUMMARY_COLUMNS} {FROM_TICKETS} {where}"
        f"ORDER BY t.received_at {order}, t.id {order} LIMIT %s",
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
