"""Queries for department staffing requests. Tables come from infra/migrations/tft only."""

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

Row = dict[str, Any]

REQUEST_COLUMNS = (
    "r.id, r.project_id, r.required_role_id, k.title AS role_name, r.organization_id, "
    "o.name AS organization_name, o.dept_code AS organization_dept_code, r.participation, "
    "r.status, r.version, r.due_at, r.due_undecided, r.message, r.issued_by_user_id, "
    "r.issued_at, r.created_at, r.updated_at, r.data_origin, r.derivation_kind"
)
REQUEST_FROM = (
    "FROM tft.staffing_requests r "
    "JOIN tft.organizations o ON o.id = r.organization_id "
    "JOIN tft.required_roles k ON k.id = r.required_role_id"
)


async def list_for_project(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {REQUEST_COLUMNS} {REQUEST_FROM} WHERE r.project_id = %s "
        "ORDER BY r.created_at, r.id",
        (project_id,),
    )
    return await cursor.fetchall()


async def get_request(conn: AsyncConnection[Row], request_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {REQUEST_COLUMNS} {REQUEST_FROM} WHERE r.id = %s", (request_id,)
    )
    return await cursor.fetchone()


async def lock_request(conn: AsyncConnection[Row], request_id: UUID) -> Row | None:
    """Locks the request row until the transaction ends so version checks cannot interleave."""
    cursor = await conn.execute(
        f"SELECT {REQUEST_COLUMNS} {REQUEST_FROM} WHERE r.id = %s FOR UPDATE OF r", (request_id,)
    )
    return await cursor.fetchone()


async def get_project_scope(conn: AsyncConnection[Row], project_id: UUID) -> Row | None:
    """The columns the action checks need: who the responsible sales user is and which
    company the project belongs to."""
    cursor = await conn.execute(
        "SELECT id, company_code, sales_user_id FROM tft.projects WHERE id = %s", (project_id,)
    )
    return await cursor.fetchone()


async def get_organization(conn: AsyncConnection[Row], organization_id: UUID) -> Row | None:
    cursor = await conn.execute(
        "SELECT id, company_code, name, dept_code FROM tft.organizations WHERE id = %s",
        (organization_id,),
    )
    return await cursor.fetchone()


async def update_request(
    conn: AsyncConnection[Row], request_id: UUID, version: int, values: Row
) -> None:
    # Column names come from schemas.EDITABLE_FIELDS or this module, never from request keys.
    assignments = ", ".join(f"{name} = %s" for name in values)
    await conn.execute(
        f"UPDATE tft.staffing_requests SET {assignments}, version = version + 1, "
        "updated_at = now() WHERE id = %s AND version = %s",
        (*values.values(), request_id, version),
    )


async def mark_role_issued(
    conn: AsyncConnection[Row], role_id: UUID, actor_user_id: UUID, issued_at: datetime
) -> None:
    """A role still in DRAFTING moves to ISSUED with the issuing sales user as reviewer.
    Roles already past DRAFTING (a second department request) are left as they are."""
    await conn.execute(
        "UPDATE tft.required_roles SET status = 'ISSUED', reviewed_by_user_id = %s, "
        "reviewed_at = %s, updated_at = now() WHERE id = %s AND status = 'DRAFTING'",
        (actor_user_id, issued_at, role_id),
    )


async def insert_revision(
    conn: AsyncConnection[Row],
    revision_id: UUID,
    request_id: UUID,
    version: int,
    action: str,
    actor_user_id: UUID,
    changes: Row,
) -> None:
    await conn.execute(
        "INSERT INTO tft.staffing_request_revisions "
        "(id, staffing_request_id, version, action, actor_user_id, changes) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (revision_id, request_id, version, action, actor_user_id, Jsonb(changes)),
    )
