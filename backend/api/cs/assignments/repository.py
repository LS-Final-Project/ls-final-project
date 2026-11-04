"""Queries for agent skills and assignment decisions (infra/migrations/cs C29)."""

import logging
from typing import Any
from uuid import UUID

import psycopg
from psycopg import AsyncConnection

from backend.api.cs.stats.repository import REJECTION_ENDED

Row = dict[str, Any]
logger = logging.getLogger(__name__)

ASSIGNMENT_COLUMNS = (
    "id, user_id, rule_version, reason_code, matched_language, matched_family, "
    "candidate_count, chosen_open_count, trigger, decided_by, created_at"
)
# Tickets that wait for an owner: no workflow row yet, or RECEIVED without an owner.
UNASSIGNED = "COALESCE(w.state, 'RECEIVED') = 'RECEIVED' AND w.owner_user_id IS NULL"
TICKET_COLUMNS = (
    "t.id, t.external_id, t.subject, t.product_code, t.body_language, t.received_at, "
    "COALESCE(w.state, 'RECEIVED') AS state, w.owner_user_id"
)


async def list_agents(conn: AsyncConnection[Row]) -> list[Row]:
    """Every skill row with the agent's current load (owned tickets not closed)."""
    cursor = await conn.execute(
        "SELECT s.user_id, s.languages, s.families, s.active, s.data_origin, s.derivation_kind, "
        "COALESCE(o.open_count, 0)::int AS open_count FROM cs.agent_skills s "
        "LEFT JOIN (SELECT owner_user_id, count(*) AS open_count FROM cs.ticket_workflows "
        "WHERE state <> 'CLOSED' AND owner_user_id IS NOT NULL GROUP BY owner_user_id) o "
        "ON o.owner_user_id = s.user_id ORDER BY s.user_id"
    )
    return await cursor.fetchall()


async def unassigned_tickets(conn: AsyncConnection[Row], limit: int) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {TICKET_COLUMNS} FROM cs.tickets t "
        "LEFT JOIN cs.ticket_workflows w ON w.ticket_id = t.id "
        f"WHERE {UNASSIGNED} ORDER BY t.received_at, t.id LIMIT %s",
        (limit,),
    )
    return await cursor.fetchall()


async def tickets_by_ids(conn: AsyncConnection[Row], ids: list[UUID]) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {TICKET_COLUMNS} FROM cs.tickets t "
        "LEFT JOIN cs.ticket_workflows w ON w.ticket_id = t.id WHERE t.id = ANY(%s)",
        (ids,),
    )
    return await cursor.fetchall()


async def insert_assignment(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.ticket_assignments ({columns}) VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


async def latest_assignment(conn: AsyncConnection[Row], ticket_id: UUID) -> Row | None:
    """The newest rule assignment of the ticket, or None.

    Read by every workflow view, so a database without C29 (the release order puts C29 before
    this code, but a missed step must not break ticket screens) reads as "no assignment". The
    savepoint keeps the caller's transaction usable; only a missing table is tolerated.
    """
    try:
        async with conn.transaction():
            cursor = await conn.execute(
                f"SELECT {ASSIGNMENT_COLUMNS} FROM cs.ticket_assignments WHERE ticket_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (ticket_id,),
            )
            return await cursor.fetchone()
    except psycopg.errors.UndefinedTable:
        logger.warning("cs assignment table missing", extra={"code": "CS_ASSIGNMENT_TABLE_MISSING"})
        return None


# --- Improvement requests (cs_owner) ------------------------------------------------------

# Open = the stats definition (KAN-111): not completed and not ended by reviewed rejection.
OPEN_REQUEST = f"r.status <> 'COMPLETED' AND NOT {REJECTION_ENDED}"
REQUEST_COLUMNS = (
    "r.id, r.request_type, r.origin, r.product_code, r.fault_id, r.status, r.cs_owner, "
    "r.problem_statement, r.created_at"
)


async def request_loads(conn: AsyncConnection[Row]) -> dict[UUID, int]:
    """Open improvement requests per C/S owner."""
    cursor = await conn.execute(
        "SELECT r.cs_owner AS user_id, count(*)::int AS open_count "
        f"FROM cs.improvement_requests r WHERE r.cs_owner IS NOT NULL AND {OPEN_REQUEST} "
        "GROUP BY r.cs_owner"
    )
    return {row["user_id"]: row["open_count"] for row in await cursor.fetchall()}


async def unassigned_requests(conn: AsyncConnection[Row], limit: int) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {REQUEST_COLUMNS} FROM cs.improvement_requests r "
        f"WHERE r.cs_owner IS NULL AND {OPEN_REQUEST} ORDER BY r.created_at, r.id LIMIT %s",
        (limit,),
    )
    return await cursor.fetchall()


async def requests_by_ids(conn: AsyncConnection[Row], ids: list[UUID]) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {REQUEST_COLUMNS}, ({OPEN_REQUEST}) AS is_open "
        "FROM cs.improvement_requests r WHERE r.id = ANY(%s)",
        (ids,),
    )
    return await cursor.fetchall()


async def insert_request_assignment(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.improvement_assignments ({columns}) "
        f"VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )
