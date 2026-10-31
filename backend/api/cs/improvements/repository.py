"""Queries for improvement requests. Tables come from infra/migrations/cs."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

Row = dict[str, Any]

REQUEST_COLUMNS = (
    "id, request_type, origin, data_origin, derivation_kind, product_code, fault_id, "
    "doc_signal, doc_id, condition_status, condition_summary, fw_fixed_version, "
    "fw_released_on, status, version, current_round, cs_owner, problem_statement, "
    "confirmed_facts, inconsistencies, missing_info, not_claimed, review_task, proposed_depts, "
    "reviewed_by, reviewed_at, sent_at, followup_plan, followup_result, completed_by, "
    "completed_at, created_by, created_at"
)
# Requests a department user may read: sent to the department in any round, or (for a
# manager) proposed to a managed department.
IN_DEPARTMENTS = (
    "(EXISTS (SELECT 1 FROM cs.request_departments d "
    "WHERE d.request_id = r.id AND d.dept_code = ANY(%(departments)s)) "
    "OR (%(proposed)s AND r.proposed_depts && %(departments)s))"
)
LIST_LIMIT = 200


# One department, whether it was sent the request in any round or only proposed for it.
INVOLVES_DEPT = (
    "(EXISTS (SELECT 1 FROM cs.request_departments f "
    "WHERE f.request_id = r.id AND f.dept_code = %(dept)s) "
    "OR %(dept)s = ANY(r.proposed_depts))"
)


async def list_requests(
    conn: AsyncConnection[Row],
    departments: list[str] | None,
    proposed: bool,
    *,
    statuses: list[str] | None = None,
    request_type: str | None = None,
    dept: str | None = None,
) -> list[Row]:
    """Newest first, up to LIST_LIMIT. Filters narrow the caller's scope and never widen it."""
    conditions = [IN_DEPARTMENTS] if departments is not None else []
    if statuses is not None:
        conditions.append("r.status = ANY(%(statuses)s)")
    if request_type is not None:
        conditions.append("r.request_type = %(request_type)s")
    if dept is not None:
        conditions.append(INVOLVES_DEPT)
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    cursor = await conn.execute(
        "SELECT r.id, r.request_type, r.product_code, r.fault_id, r.doc_signal, r.status, "
        "r.version, r.cs_owner, r.problem_statement, r.created_at, "
        "(SELECT count(*) FROM cs.request_evidence e "
        "WHERE e.request_id = r.id AND e.removed_at IS NULL) AS evidence_count "
        f"FROM cs.improvement_requests r {where}"
        "ORDER BY r.created_at DESC, r.id DESC LIMIT %(limit)s",
        {
            "departments": departments,
            "proposed": proposed,
            "statuses": statuses,
            "request_type": request_type,
            "dept": dept,
            "limit": LIST_LIMIT,
        },
    )
    return await cursor.fetchall()


async def visible_to(
    conn: AsyncConnection[Row], request_id: UUID, departments: list[str], proposed: bool
) -> bool:
    cursor = await conn.execute(
        f"SELECT 1 FROM cs.improvement_requests r WHERE r.id = %(id)s AND {IN_DEPARTMENTS}",
        {"id": request_id, "departments": departments, "proposed": proposed},
    )
    return await cursor.fetchone() is not None


async def get_request(conn: AsyncConnection[Row], request_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {REQUEST_COLUMNS} FROM cs.improvement_requests WHERE id = %s", (request_id,)
    )
    return await cursor.fetchone()


async def lock_request(conn: AsyncConnection[Row], request_id: UUID) -> Row | None:
    """Orders every change to one request: content, evidence, owner and later transitions."""
    cursor = await conn.execute(
        f"SELECT {REQUEST_COLUMNS} FROM cs.improvement_requests WHERE id = %s FOR UPDATE",
        (request_id,),
    )
    return await cursor.fetchone()


async def insert_request(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.improvement_requests ({columns}) "
        f"VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


async def update_request(
    conn: AsyncConnection[Row], request_id: UUID, version: int, values: Row
) -> None:
    # Column names come from schemas.CONTENT_FIELDS or fixed route values only.
    assignments = "".join(f"{name} = %s, " for name in values)
    await conn.execute(
        f"UPDATE cs.improvement_requests SET {assignments}version = version + 1, "
        "updated_at = now() WHERE id = %s AND version = %s",
        (*values.values(), request_id, version),
    )


async def insert_version(
    conn: AsyncConnection[Row], request_id: UUID, version: int, content: Row, actor: UUID
) -> None:
    await conn.execute(
        "INSERT INTO cs.improvement_request_versions (request_id, version, content, changed_by) "
        "VALUES (%s, %s, %s, %s)",
        (request_id, version, Jsonb(content), actor),
    )


async def list_versions(conn: AsyncConnection[Row], request_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        "SELECT version, content, changed_by, changed_at FROM cs.improvement_request_versions "
        "WHERE request_id = %s ORDER BY version",
        (request_id,),
    )
    return await cursor.fetchall()


async def list_evidence(conn: AsyncConnection[Row], request_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        "SELECT e.id, e.ticket_id, t.external_id AS ticket_external_id, e.case_id, "
        "c.external_id AS case_external_id, e.added_by, e.added_at, e.removed_by, "
        "e.removed_at, e.removal_reason "
        "FROM cs.request_evidence e JOIN cs.tickets t ON t.id = e.ticket_id "
        "LEFT JOIN cs.resolution_cases c ON c.id = e.case_id "
        "WHERE e.request_id = %s ORDER BY (e.removed_at IS NOT NULL), e.added_at, e.id",
        (request_id,),
    )
    return await cursor.fetchall()


async def ticket_and_case(
    conn: AsyncConnection[Row], ticket_id: UUID, case_id: UUID | None
) -> tuple[Row | None, Row | None]:
    """The evidence ticket, and its resolution case if one is named and already known."""
    cursor = await conn.execute(
        "SELECT id, product_code FROM cs.tickets WHERE id = %s", (ticket_id,)
    )
    ticket = await cursor.fetchone()
    case = None
    if case_id is not None:
        cursor = await conn.execute(
            "SELECT id, ticket_id, product_code, fault_id FROM cs.resolution_cases "
            "WHERE id = %s AND known_at <= now()",
            (case_id,),
        )
        case = await cursor.fetchone()
    return ticket, case


async def insert_evidence(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.request_evidence ({columns}) VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


async def remove_evidence(
    conn: AsyncConnection[Row], request_id: UUID, evidence_id: UUID, actor: UUID, reason: str
) -> bool:
    cursor = await conn.execute(
        "UPDATE cs.request_evidence SET removed_by = %s, removed_at = now(), "
        "removal_reason = %s WHERE id = %s AND request_id = %s AND removed_at IS NULL "
        "RETURNING id",
        (actor, reason, evidence_id, request_id),
    )
    return await cursor.fetchone() is not None


async def current_departments(conn: AsyncConnection[Row], request: Row) -> list[Row]:
    cursor = await conn.execute(
        "SELECT round, dept_code, status, carried_over, sent_version, reply_due_at, "
        "rejection_reviewed_by, rejection_reviewed_at FROM cs.request_departments "
        "WHERE request_id = %s AND round = %s ORDER BY dept_code",
        (request["id"], request["current_round"]),
    )
    return await cursor.fetchall()


async def list_replies(conn: AsyncConnection[Row], request_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        "SELECT id, round, dept_code, request_version, reply_type, reason, replied_by, "
        "replied_at FROM cs.dept_replies WHERE request_id = %s ORDER BY replied_at, id",
        (request_id,),
    )
    return await cursor.fetchall()


async def list_actions(conn: AsyncConnection[Row], request_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        "SELECT id, round, dept_code, action, action_scope, scope_kind, scope_value, action_date, "
        "action_evidence, recorded_by, recorded_at FROM cs.request_actions WHERE request_id = %s "
        "ORDER BY recorded_at, id",
        (request_id,),
    )
    return await cursor.fetchall()


async def insert_audit(
    conn: AsyncConnection[Row],
    event_id: UUID,
    request_id: UUID,
    action: str,
    version: int,
    actor: UUID,
    changes: Row,
) -> None:
    await conn.execute(
        "INSERT INTO cs.audit_events "
        "(id, entity_type, entity_id, action, version, actor_user_id, changes) "
        "VALUES (%s, 'improvement_request', %s, %s, %s, %s, %s)",
        (event_id, request_id, action, version, actor, Jsonb(changes)),
    )


# --- KAN-116: delivery rounds, department states, replies and actions ---------------------


async def departments_in_round(
    conn: AsyncConnection[Row], request_id: UUID, round: int
) -> list[Row]:
    cursor = await conn.execute(
        "SELECT round, dept_code, status, carried_over, sent_version, reply_due_at, "
        "rejection_reviewed_by, rejection_reviewed_at FROM cs.request_departments "
        "WHERE request_id = %s AND round = %s ORDER BY dept_code FOR UPDATE",
        (request_id, round),
    )
    return await cursor.fetchall()


async def insert_department(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.request_departments ({columns}) "
        f"VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


async def update_department(
    conn: AsyncConnection[Row], request_id: UUID, round: int, dept_code: str, values: Row
) -> None:
    # Column names come from fixed route values only.
    assignments = "".join(f"{name} = %s, " for name in values)
    await conn.execute(
        f"UPDATE cs.request_departments SET {assignments}updated_at = now() "
        "WHERE request_id = %s AND round = %s AND dept_code = %s",
        (*values.values(), request_id, round, dept_code),
    )


async def insert_reply(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.dept_replies ({columns}) VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


async def insert_action(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.request_actions ({columns}) VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


async def latest_rejection_at(
    conn: AsyncConnection[Row], request_id: UUID, round: int, dept_code: str
) -> Any:
    cursor = await conn.execute(
        "SELECT max(replied_at) AS at FROM cs.dept_replies WHERE request_id = %s AND round = %s "
        "AND dept_code = %s AND reply_type = 'REJECTED'",
        (request_id, round, dept_code),
    )
    row = await cursor.fetchone()
    return row["at"] if row else None


async def evidence_added_after(conn: AsyncConnection[Row], request_id: UUID, after: Any) -> int:
    cursor = await conn.execute(
        "SELECT count(*) AS n FROM cs.request_evidence WHERE request_id = %s "
        "AND removed_at IS NULL AND added_at > %s",
        (request_id, after),
    )
    row = await cursor.fetchone()
    return int(row["n"]) if row else 0


async def database_now(conn: AsyncConnection[Row]) -> Any:
    cursor = await conn.execute("SELECT now() AS now")
    row = await cursor.fetchone()
    assert row is not None
    return row["now"]


# --- KAN-117: rounds and the bundle's resolution records ---------------------------------


async def list_rounds(conn: AsyncConnection[Row], request_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        "SELECT round, kind, opened_at, opened_by, candidate_index, as_of, new_evidence_note, "
        "previous_completion FROM cs.request_rounds WHERE request_id = %s ORDER BY round",
        (request_id,),
    )
    return await cursor.fetchall()


async def insert_round(conn: AsyncConnection[Row], values: Row) -> None:
    columns = ", ".join(values)
    await conn.execute(
        f"INSERT INTO cs.request_rounds ({columns}) VALUES ({', '.join(['%s'] * len(values))})",
        tuple(Jsonb(v) if isinstance(v, dict) else v for v in values.values()),
    )


async def bundle_records(conn: AsyncConnection[Row], product_code: str, fault_id: str) -> list[Row]:
    """Resolution records of the same product and fault, with the ticket's receipt time
    (occurrence proxy) and customer. Future filtering by as_of is the engine's."""
    cursor = await conn.execute(
        "SELECT rc.id AS case_id, rc.ticket_id, t.customer_external_id AS customer_id, "
        "rc.known_at, t.received_at, rc.fw_version_at_issue FROM cs.resolution_cases rc "
        "JOIN cs.tickets t ON t.id = rc.ticket_id WHERE rc.product_code = %s AND rc.fault_id = %s "
        "ORDER BY rc.known_at, rc.id",
        (product_code, fault_id),
    )
    return await cursor.fetchall()


async def active_evidence_tickets(conn: AsyncConnection[Row], request_id: UUID) -> set[UUID]:
    cursor = await conn.execute(
        "SELECT ticket_id FROM cs.request_evidence WHERE request_id = %s AND removed_at IS NULL",
        (request_id,),
    )
    return {row["ticket_id"] for row in await cursor.fetchall()}


async def sent_by(conn: AsyncConnection[Row], request_id: UUID) -> UUID | None:
    """Who sent the first round, from the audit trail (requests sent before KAN-117)."""
    cursor = await conn.execute(
        "SELECT actor_user_id FROM cs.audit_events WHERE entity_type = 'improvement_request' "
        "AND entity_id = %s AND changes->'action'->>'to' = 'send' ORDER BY version LIMIT 1",
        (request_id,),
    )
    row = await cursor.fetchone()
    return row["actor_user_id"] if row else None
