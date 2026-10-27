"""Queries for installed units and ticket-to-unit links. Tables come from infra/migrations/cs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.api.cs.tickets.repository import CASE_COLUMNS

Row = dict[str, Any]

UNIT_COLUMNS = (
    "u.id, u.external_id, u.data_origin, u.derivation_kind, u.serial_no, "
    "u.customer_external_id, u.product_code, u.site_code, u.commissioning_date, "
    "u.commissioning_status, u.ship_date, u.lot_no, u.fw_version_last, u.unit_status, "
    "u.replaced_by_unit_id"
)
LINK_COLUMNS = (
    "id, ticket_id, unit_id, version, link_status, link_basis, repeat_type, decided_by, decided_at"
)
# One unit's history is short; the cap keeps a bad link from returning a whole customer.
HISTORY_LIMIT = 100


async def list_ticket_units(
    conn: AsyncConnection[Row], ticket_id: UUID, customer: str, product: str
) -> list[Row]:
    """Units of the ticket's customer and product, plus any unit already linked to the ticket."""
    cursor = await conn.execute(
        f"SELECT {UNIT_COLUMNS}, "
        "(u.customer_external_id = %(customer)s AND u.product_code = %(product)s) "
        "AS matches_ticket, "
        "l.id AS link_id, l.version AS link_version, l.link_status, l.link_basis, "
        "l.repeat_type, l.decided_by, l.decided_at "
        "FROM cs.installed_units u "
        "LEFT JOIN cs.ticket_unit_links l ON l.unit_id = u.id AND l.ticket_id = %(ticket)s "
        "WHERE u.known_at <= now() AND ("
        "(u.customer_external_id = %(customer)s AND u.product_code = %(product)s) "
        "OR l.id IS NOT NULL) "
        "ORDER BY matches_ticket DESC, u.serial_no, u.id",
        {"ticket": ticket_id, "customer": customer, "product": product},
    )
    return await cursor.fetchall()


async def get_unit(conn: AsyncConnection[Row], unit_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {UNIT_COLUMNS} FROM cs.installed_units u WHERE u.id = %s AND u.known_at <= now()",
        (unit_id,),
    )
    return await cursor.fetchone()


async def lock_link(conn: AsyncConnection[Row], ticket_id: UUID, unit_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {LINK_COLUMNS} FROM cs.ticket_unit_links "
        "WHERE ticket_id = %s AND unit_id = %s FOR UPDATE",
        (ticket_id, unit_id),
    )
    return await cursor.fetchone()


async def confirmed_link(conn: AsyncConnection[Row], ticket_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {LINK_COLUMNS} FROM cs.ticket_unit_links "
        "WHERE ticket_id = %s AND link_status = 'CONFIRMED'",
        (ticket_id,),
    )
    return await cursor.fetchone()


async def insert_link(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    await conn.execute(
        f"INSERT INTO cs.ticket_unit_links ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )


async def update_link(conn: AsyncConnection[Row], link_id: UUID, version: int, values: Row) -> None:
    # Column names come from the route's fixed field list, never from request keys.
    assignments = ", ".join(f"{name} = %s" for name in values)
    await conn.execute(
        f"UPDATE cs.ticket_unit_links SET {assignments}, version = version + 1, "
        "updated_at = now() WHERE id = %s AND version = %s",
        (*values.values(), link_id, version),
    )


async def insert_link_audit(
    conn: AsyncConnection[Row],
    event_id: UUID,
    link_id: UUID,
    action: str,
    version: int,
    actor_user_id: UUID,
    changes: Row,
) -> None:
    await conn.execute(
        "INSERT INTO cs.audit_events "
        "(id, entity_type, entity_id, action, version, actor_user_id, changes) "
        "VALUES (%s, 'ticket_unit_link', %s, %s, %s, %s, %s)",
        (event_id, link_id, action, version, actor_user_id, Jsonb(changes)),
    )


async def unit_history(
    conn: AsyncConnection[Row], ticket_id: UUID, unit_id: UUID, before: datetime
) -> list[Row]:
    """Earlier tickets confirmed on this unit or on a unit it replaced.

    Tickets on any other unit of the same customer are never returned, and nothing received
    or known after `before` (this ticket's receipt) is used.
    """
    cursor = await conn.execute(
        "WITH RECURSIVE chain (id, serial_no, relation) AS ("
        "SELECT id, serial_no, 'SAME_UNIT' FROM cs.installed_units "
        "WHERE id = %(unit)s AND known_at <= now() "
        "UNION "
        "SELECT u.id, u.serial_no, 'REPLACED_UNIT' FROM cs.installed_units u "
        "JOIN chain c ON u.replaced_by_unit_id = c.id WHERE u.known_at <= now()) "
        "SELECT DISTINCT ON (t.received_at, t.id) "
        "t.id, t.external_id, t.received_at, t.channel, t.subject, t.product_code, "
        "t.fw_version_reported, t.error_code_reported, "
        "c.id AS unit_id, c.serial_no, c.relation "
        "FROM chain c "
        "JOIN cs.ticket_unit_links l ON l.unit_id = c.id AND l.link_status = 'CONFIRMED' "
        "JOIN cs.tickets t ON t.id = l.ticket_id "
        "WHERE t.id <> %(ticket)s AND t.received_at < %(before)s AND t.known_at <= %(before)s "
        "ORDER BY t.received_at DESC, t.id DESC, c.relation DESC "
        "LIMIT %(limit)s",
        {"unit": unit_id, "ticket": ticket_id, "before": before, "limit": HISTORY_LIMIT},
    )
    return await cursor.fetchall()


async def known_cases(
    conn: AsyncConnection[Row], ticket_ids: list[UUID], before: datetime
) -> dict[UUID, Row]:
    """Resolution cases that were already known when this ticket arrived."""
    if not ticket_ids:
        return {}
    cursor = await conn.execute(
        f"SELECT ticket_id, {CASE_COLUMNS} FROM cs.resolution_cases "
        "WHERE ticket_id = ANY(%s) AND known_at <= %s AND known_at <= now()",
        (ticket_ids, before),
    )
    return {row.pop("ticket_id"): row for row in await cursor.fetchall()}
