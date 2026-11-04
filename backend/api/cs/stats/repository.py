"""Aggregate queries over stored C/S records. Null filters match everything."""

from datetime import datetime
from typing import Any, get_args
from uuid import UUID

from psycopg import AsyncConnection

from backend.api.cs.improvements import repository as improvement_repository
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
        "AS returned_or_recalled, "
        # Still with the department now, and for more than seven days since the transfer.
        "count(*) FILTER (WHERE x.status = 'OPEN') AS waiting, "
        "count(*) FILTER (WHERE x.status = 'OPEN' AND x.transferred_at < now() - interval '7 days') "
        "AS waiting_over_7_days, "
        # Transfer to the department's submitted result (one result per transfer).
        "round((avg(extract(epoch FROM r.submitted_at - x.transferred_at)) / 86400)::numeric, 1) "
        "AS avg_reply_days "
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
REQUEST_SCOPE = """
  AND (%(dept)s::text IS NULL OR %(dept)s = ANY(r.proposed_depts) OR EXISTS (
          SELECT 1 FROM cs.request_departments d
          WHERE d.request_id = r.id AND d.dept_code = %(dept)s))
  AND (%(customer)s::text IS NULL OR EXISTS (
          SELECT 1 FROM cs.request_evidence e JOIN cs.tickets t ON t.id = e.ticket_id
          WHERE e.request_id = r.id AND e.removed_at IS NULL
            AND t.customer_external_id = %(customer)s))
"""
REQUESTS = (
    "\nFROM cs.improvement_requests r\nWHERE "
    + IN_PERIOD.format(col="r.created_at")
    + REQUEST_SCOPE
)
# A request ends by rejection once every current-round department rejected and the
# rejection was reviewed (permissions.md 2.2.1).
REJECTION_ENDED = (
    "(r.status = 'REJECTED' AND NOT EXISTS (SELECT 1 FROM cs.request_departments d "
    "WHERE d.request_id = r.id AND d.round = r.current_round "
    "AND (d.status <> 'REJECTED' OR d.rejection_reviewed_at IS NULL)))"
)
OPEN_REQUEST = f"(r.status <> 'COMPLETED' AND NOT {REJECTION_ENDED})"


# Current-round departments of an open request (not completed, not ended by rejection).
CURRENT_DEPT = (
    "EXISTS (SELECT 1 FROM cs.request_departments d WHERE d.request_id = r.id "
    "AND d.round = r.current_round AND {condition})"
)
INFO_REQUESTED = CURRENT_DEPT.format(condition="d.status = 'INFO_REQUESTED'")
DEPT_REJECTED = CURRENT_DEPT.format(condition="d.status = 'REJECTED'")
# Due and still without any reply from that department in that round.
REPLY_OVERDUE = (
    "d.reply_due_at < now() AND NOT EXISTS (SELECT 1 FROM cs.dept_replies y "
    "WHERE y.request_id = d.request_id AND y.round = d.round AND y.dept_code = d.dept_code)"
)


async def improvements(conn: AsyncConnection[Row], p: Row) -> tuple[list[Row], Row]:
    cursor = await conn.execute(
        f"SELECT r.status, r.request_type, count(*) AS n {REQUESTS} GROUP BY r.status, r.request_type",
        p,
    )
    groups = await cursor.fetchall()
    cursor = await conn.execute(
        f"SELECT count(*) FILTER (WHERE {OPEN_REQUEST}) "
        "AS open, count(*) FILTER (WHERE cardinality(r.proposed_depts) = 0) "
        "AS receiver_unconfirmed, "
        f"count(*) FILTER (WHERE {OPEN_REQUEST} AND "
        f"{INFO_REQUESTED}) "
        "AS info_requested, "
        f"count(*) FILTER (WHERE {DEPT_REJECTED}) "
        "AS rejected, "
        f"count(*) FILTER (WHERE {OPEN_REQUEST} AND "
        f"{CURRENT_DEPT.format(condition=REPLY_OVERDUE)}) AS reply_overdue "
        f"{REQUESTS}",
        p,
    )
    totals = await cursor.fetchone()
    assert totals is not None
    # Completed in the period, whenever created (the period above is the creation time).
    cursor = await conn.execute(
        "SELECT count(*) AS n FROM cs.improvement_requests r "
        "WHERE r.status = 'COMPLETED' AND "
        + IN_PERIOD.format(col="r.completed_at")
        + REQUEST_SCOPE,
        p,
    )
    completed = await cursor.fetchone()
    assert completed is not None
    return groups, {**totals, "completed_in_period": completed["n"]}


async def completed_fault_requests(conn: AsyncConnection[Row], p: Row) -> tuple[list[Row], int]:
    """Completed requests in the overview's request filter: fault requests (full rows, for the
    KAN-117 recurrence signal) and the number of document requests, which have no signal."""
    cursor = await conn.execute(
        f"SELECT {improvement_repository.REQUEST_COLUMNS} {REQUESTS} "
        "AND r.status = 'COMPLETED' AND r.request_type = 'FAULT' ORDER BY r.id",
        p,
    )
    faults = await cursor.fetchall()
    cursor = await conn.execute(
        f"SELECT count(*) AS n {REQUESTS} AND r.status = 'COMPLETED' "
        "AND r.request_type = 'DOCUMENT'",
        p,
    )
    documents = await cursor.fetchone()
    assert documents is not None
    return faults, documents["n"]


async def owner_requests(conn: AsyncConnection[Row], dept: str | None) -> dict[Any, int]:
    """Open improvement requests per C/S owner (cs_owner), now, whenever created."""
    cursor = await conn.execute(
        "SELECT r.cs_owner, count(*) AS n FROM cs.improvement_requests r "
        f"WHERE {OPEN_REQUEST} AND r.cs_owner IS NOT NULL "
        "AND (%(dept)s::text IS NULL OR %(dept)s = ANY(r.proposed_depts) OR EXISTS ("
        "SELECT 1 FROM cs.request_departments d "
        "WHERE d.request_id = r.id AND d.dept_code = %(dept)s)) "
        "GROUP BY r.cs_owner",
        {"dept": dept},
    )
    return {row["cs_owner"]: row["n"] for row in await cursor.fetchall()}


async def sla_tickets(conn: AsyncConnection[Row], p: Row) -> list[Row]:
    """Rule inputs of the tickets SLA figures can cover: open now, received in the period, or
    closed in the period. The deadlines themselves are computed in Python (stats/sla.py), the
    same way as the analysis view; nothing here is stored."""
    received = IN_PERIOD.format(col="b.received_at")
    closed = IN_PERIOD.format(col="b.closed_at")
    cursor = await conn.execute(
        BASE + "SELECT b.id, t.external_id, t.subject, b.received_at, b.product_code, b.state, "
        "b.closed_at, b.owner_user_id, "
        "coalesce(t.customer_tier, s.customer_tier) AS customer_tier, "
        "w.return_subtype_confirmed, "
        # The analysis view uses the latest VALID run and every correction, oldest first.
        "(SELECT a.classification FROM cs.ticket_analyses a WHERE a.ticket_id = b.id "
        "AND a.outcome = 'VALID' ORDER BY a.run_seq DESC LIMIT 1) AS classification, "
        "coalesce((SELECT jsonb_agg(c.fields ORDER BY c.seq) "
        "FROM cs.ticket_classification_corrections c WHERE c.ticket_id = b.id), '[]'::jsonb) "
        "AS corrections, "
        # First customer message the mail platform accepted and the workflow recorded
        # (an answer or an information request; the same rule as asked_info_codes).
        "(SELECT min(coalesce(m.accepted_at, m.recorded_at)) FROM cs.ticket_customer_sends m "
        "WHERE m.ticket_id = b.id AND m.recorded_at IS NOT NULL) AS first_response_at, "
        "coalesce((SELECT array_agg(DISTINCT x.dept_code) FROM cs.ticket_transfers x "
        "WHERE x.ticket_id = b.id), '{}') AS transfer_depts "
        "FROM base b JOIN cs.tickets t ON t.id = b.id "
        "LEFT JOIN cs.ticket_source_records s ON s.ticket_id = b.id "
        "LEFT JOIN cs.ticket_workflows w ON w.ticket_id = b.id "
        f"WHERE b.state <> 'CLOSED' OR ({received}) "
        f"OR (b.closed_at IS NOT NULL AND {closed})",
        p,
    )
    return await cursor.fetchall()


async def owners(conn: AsyncConnection[Row], p: Row) -> list[Row]:
    cursor = await conn.execute(
        BASE + "SELECT owner_user_id, "
        f"count(*) FILTER (WHERE {RECEIVED}) AS received, "
        f"count(*) FILTER (WHERE closed_at IS NOT NULL AND {CLOSED}) AS closed, "
        "count(*) FILTER (WHERE state <> 'CLOSED') AS open, "
        # Receipt to closing, over the tickets closed in the period.
        "round((avg(extract(epoch FROM closed_at - received_at)) "
        f"FILTER (WHERE closed_at IS NOT NULL AND {CLOSED}) / 86400)::numeric, 1) "
        "AS avg_handle_days, "
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
