"""Queries for agent skills and assignment decisions (infra/migrations/cs C29)."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

Row = dict[str, Any]

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
    cursor = await conn.execute(
        f"SELECT {ASSIGNMENT_COLUMNS} FROM cs.ticket_assignments WHERE ticket_id = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (ticket_id,),
    )
    return await cursor.fetchone()
