"""Aggregate queries over stored C/S records. Null filters match everything."""

from datetime import datetime
from typing import Any, get_args
from uuid import UUID

from psycopg import AsyncConnection

from backend.api.cs.tickets.schemas import WorkflowState

Row = dict[str, Any]
STATES = get_args(WorkflowState)

# Tickets after the department, customer and owner filters, with their current state.
BASE = """
WITH base AS (
    SELECT t.id, t.received_at, t.data_origin, t.customer_external_id, t.product_code,
           COALESCE(w.state, 'RECEIVED') AS state, w.closed_at, w.owner_user_id
    FROM cs.tickets t LEFT JOIN cs.ticket_workflows w ON w.ticket_id = t.id
    WHERE (%(dept)s::text IS NULL OR EXISTS (
              SELECT 1 FROM cs.ticket_transfers x
              WHERE x.ticket_id = t.id AND x.dept_code = %(dept)s))
      AND (%(customer)s::text IS NULL OR t.customer_external_id = %(customer)s)
      AND (%(owner)s::uuid IS NULL OR w.owner_user_id = %(owner)s)
)
"""
IN_PERIOD = (
    "(%(start)s::timestamptz IS NULL OR {col} >= %(start)s) "
    "AND (%(end)s::timestamptz IS NULL OR {col} < %(end)s)"
)
RECEIVED = IN_PERIOD.format(col="received_at")
CLOSED = IN_PERIOD.format(col="closed_at")
TOP_CUSTOMERS = 10
CUSTOMER_TICKETS = 200


def params(
    start: datetime | None,
    end: datetime | None,
    dept: str | None = None,
    customer: str | None = None,
    owner: UUID | None = None,
) -> Row:
    return {"start": start, "end": end, "dept": dept, "customer": customer, "owner": owner}


async def ticket_counts(conn: AsyncConnection[Row], p: Row) -> Row:
    cursor = await conn.execute(
        BASE + "SELECT "
        f"count(*) FILTER (WHERE {RECEIVED}) AS received, "
        f"count(*) FILTER (WHERE closed_at IS NOT NULL AND {CLOSED}) AS closed, "
        f"count(*) FILTER (WHERE {RECEIVED} AND state <> 'CLOSED') AS open, "
        "min(received_at) AS first_received_at, max(received_at) AS last_received_at "
        "FROM base",
        p,
    )
    row = await cursor.fetchone()
    assert row is not None
    return row


async def by_state_and_origin(conn: AsyncConnection[Row], p: Row) -> tuple[Row, Row]:
    cursor = await conn.execute(
        BASE + f"SELECT state, data_origin, count(*) AS n FROM base WHERE {RECEIVED} "
        "GROUP BY state, data_origin",
        p,
    )
    states: Row = {}
    origins: Row = {}
    for row in await cursor.fetchall():
        states[row["state"]] = states.get(row["state"], 0) + row["n"]
        origins[row["data_origin"]] = origins.get(row["data_origin"], 0) + row["n"]
    return states, origins


async def open_age(conn: AsyncConnection[Row], p: Row) -> Row:
    """Open tickets received in the period, aged at the period end (or now)."""
    cursor = await conn.execute(
        BASE + "SELECT "
        "count(*) FILTER (WHERE age < interval '1 day') AS under_1_day, "
        "count(*) FILTER (WHERE age >= interval '1 day' AND age < interval '3 days') "
        "AS from_1_to_3_days, "
        "count(*) FILTER (WHERE age >= interval '3 days' AND age < interval '7 days') "
        "AS from_3_to_7_days, "
        "count(*) FILTER (WHERE age >= interval '7 days') AS over_7_days "
        "FROM (SELECT COALESCE(%(end)s::timestamptz, now()) - received_at AS age FROM base "
        f"WHERE {RECEIVED} AND state <> 'CLOSED') aged",
        p,
    )
    row = await cursor.fetchone()
    assert row is not None
    return row


async def departments(conn: AsyncConnection[Row], p: Row) -> list[Row]:
    """Transfers made in the period to each department, and what came of them."""
    cursor = await conn.execute(
        BASE + "SELECT x.dept_code, count(*) AS transferred, "
        "count(r.id) AS results_submitted, "
        "count(r.id) FILTER (WHERE r.review_status = 'ACCEPTED') AS results_accepted, "
        "count(*) FILTER (WHERE x.status IN ('RETURNED_BY_DEPT', 'RECALLED')) "
        "AS returned_or_recalled "
        "FROM cs.ticket_transfers x JOIN base b ON b.id = x.ticket_id "
        "LEFT JOIN cs.dept_results r ON r.transfer_id = x.id "
        f"WHERE {IN_PERIOD.format(col='x.transferred_at')} "
        "AND (%(dept)s::text IS NULL OR x.dept_code = %(dept)s) "
        "GROUP BY x.dept_code ORDER BY transferred DESC, x.dept_code",
        p,
    )
    return await cursor.fetchall()


async def top_customers(conn: AsyncConnection[Row], p: Row) -> list[Row]:
    cursor = await conn.execute(
        BASE + "SELECT customer_external_id, count(*) AS received, "
        "count(*) FILTER (WHERE state <> 'CLOSED') AS open "
        f"FROM base WHERE {RECEIVED} GROUP BY customer_external_id "
        "ORDER BY received DESC, customer_external_id LIMIT %(limit)s",
        {**p, "limit": TOP_CUSTOMERS},
    )
    return await cursor.fetchall()


# Requests matching the department filter (sent to or proposed to it) and the customer
# filter (an active evidence ticket of that customer).
REQUESTS = """
FROM cs.improvement_requests r
WHERE (%(start)s::timestamptz IS NULL OR r.created_at >= %(start)s)
  AND (%(end)s::timestamptz IS NULL OR r.created_at < %(end)s)
  AND (%(dept)s::text IS NULL OR %(dept)s = ANY(r.proposed_depts) OR EXISTS (
          SELECT 1 FROM cs.request_departments d
          WHERE d.request_id = r.id AND d.dept_code = %(dept)s))
  AND (%(customer)s::text IS NULL OR EXISTS (
          SELECT 1 FROM cs.request_evidence e JOIN cs.tickets t ON t.id = e.ticket_id
          WHERE e.request_id = r.id AND e.removed_at IS NULL
            AND t.customer_external_id = %(customer)s))
"""
# A request ends by rejection once every current-round department rejected and the
# rejection was reviewed (permissions.md 2.2.1).
REJECTION_ENDED = (
    "(r.status = 'REJECTED' AND NOT EXISTS (SELECT 1 FROM cs.request_departments d "
    "WHERE d.request_id = r.id AND d.round = r.current_round "
    "AND (d.status <> 'REJECTED' OR d.rejection_reviewed_at IS NULL)))"
)


async def improvements(conn: AsyncConnection[Row], p: Row) -> tuple[list[Row], Row]:
    cursor = await conn.execute(
        f"SELECT r.status, r.request_type, count(*) AS n {REQUESTS} GROUP BY r.status, r.request_type",
        p,
    )
    groups = await cursor.fetchall()
    cursor = await conn.execute(
        f"SELECT count(*) FILTER (WHERE r.status <> 'COMPLETED' AND NOT {REJECTION_ENDED}) "
        "AS open, count(*) FILTER (WHERE cardinality(r.proposed_depts) = 0) "
        f"AS receiver_unconfirmed {REQUESTS}",
        p,
    )
    totals = await cursor.fetchone()
    assert totals is not None
    return groups, totals


async def owners(conn: AsyncConnection[Row], p: Row) -> list[Row]:
    cursor = await conn.execute(
        BASE + "SELECT owner_user_id, "
        f"count(*) FILTER (WHERE {RECEIVED}) AS received, "
        f"count(*) FILTER (WHERE closed_at IS NOT NULL AND {CLOSED}) AS closed, "
        "count(*) FILTER (WHERE state <> 'CLOSED') AS open, "
        # Current state of each owned ticket, whenever received (like `open`).
        + ", ".join(f"count(*) FILTER (WHERE state = '{s}') AS state_{s.lower()}" for s in STATES)
        + " FROM base GROUP BY owner_user_id "
        f"HAVING count(*) FILTER (WHERE {RECEIVED}) > 0 "
        f"OR count(*) FILTER (WHERE closed_at IS NOT NULL AND {CLOSED}) > 0 "
        "OR count(*) FILTER (WHERE state <> 'CLOSED') > 0 "
        "ORDER BY open DESC, received DESC, owner_user_id NULLS LAST",
        p,
    )
    return await cursor.fetchall()


async def customer_tickets(conn: AsyncConnection[Row], p: Row) -> list[Row]:
    cursor = await conn.execute(
        "SELECT t.id, t.external_id, t.received_at, t.channel, t.product_code, t.subject, "
        "COALESCE(w.state, 'RECEIVED') AS workflow_state, t.data_origin "
        "FROM cs.tickets t LEFT JOIN cs.ticket_workflows w ON w.ticket_id = t.id "
        "WHERE t.customer_external_id = %(customer)s "
        f"AND {IN_PERIOD.format(col='t.received_at')} "
        "ORDER BY t.received_at DESC, t.id DESC LIMIT %(limit)s",
        {**p, "limit": CUSTOMER_TICKETS},
    )
    return await cursor.fetchall()


async def customer_units(conn: AsyncConnection[Row], customer: str) -> list[Row]:
    cursor = await conn.execute(
        "SELECT u.id, u.serial_no, u.product_code, u.unit_status, u.derivation_kind, "
        "count(l.id) AS confirmed_tickets FROM cs.installed_units u "
        "LEFT JOIN cs.ticket_unit_links l ON l.unit_id = u.id AND l.link_status = 'CONFIRMED' "
        "WHERE u.customer_external_id = %s AND u.known_at <= now() "
        "GROUP BY u.id ORDER BY u.product_code, u.serial_no",
        (customer,),
    )
    return await cursor.fetchall()


async def customer_requests(conn: AsyncConnection[Row], customer: str) -> list[Row]:
    cursor = await conn.execute(
        "SELECT r.id, r.request_type, r.product_code, r.fault_id, r.status, "
        "count(e.id) AS evidence_tickets FROM cs.improvement_requests r "
        "JOIN cs.request_evidence e ON e.request_id = r.id AND e.removed_at IS NULL "
        "JOIN cs.tickets t ON t.id = e.ticket_id AND t.customer_external_id = %s "
        "GROUP BY r.id ORDER BY r.created_at DESC, r.id",
        (customer,),
    )
    return await cursor.fetchall()


async def by_product(conn: AsyncConnection[Row], p: Row) -> Row:
    cursor = await conn.execute(
        BASE + f"SELECT product_code, count(*) AS n FROM base WHERE {RECEIVED} "
        "GROUP BY product_code ORDER BY n DESC, product_code",
        p,
    )
    return {row["product_code"]: row["n"] for row in await cursor.fetchall()}
