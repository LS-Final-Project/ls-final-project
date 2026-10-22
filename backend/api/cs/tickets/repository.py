"""Read queries for C/S tickets. Tables are created by infra/migrations/cs only."""

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

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
