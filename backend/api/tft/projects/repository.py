"""Read queries for TFT projects. Tables are created by infra/migrations/tft only."""

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

Row = dict[str, Any]

SUMMARY_COLUMNS = (
    "p.id, p.external_id, p.name, p.company_code, p.status, p.data_origin, p.derivation_kind, "
    "p.responsible_user_id, p.sales_user_id, p.created_at"
)
DETAIL_COLUMNS = (
    "id, external_id, version, data_origin, derivation_kind, known_at, created_at, "
    "company_code, name, client_name, status, responsible_user_id, sales_user_id"
)
REQUIREMENT_COLUMNS = (
    "id, requirement_code, field_tag, title, description, document_name, document_id, "
    "document_version_id, clause, page, excerpt, data_origin, derivation_kind"
)


async def list_projects(
    conn: AsyncConnection[Row],
    limit: int,
    after: tuple[datetime, UUID] | None,
    managed_projects: list[UUID],
    user_id: UUID,
) -> list[Row]:
    """Newest first, only projects the user may see (access.sees_project).

    Fetches one extra row so the caller knows whether a next page exists.
    """
    scope = "(p.id = ANY(%s) OR p.responsible_user_id = %s OR p.sales_user_id = %s)"
    if after is None:
        cursor = await conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM tft.projects p WHERE {scope} "
            "ORDER BY p.created_at DESC, p.id DESC LIMIT %s",
            (managed_projects, user_id, user_id, limit + 1),
        )
    else:
        cursor = await conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM tft.projects p WHERE {scope} "
            "AND (p.created_at, p.id) < (%s, %s) "
            "ORDER BY p.created_at DESC, p.id DESC LIMIT %s",
            (managed_projects, user_id, user_id, *after, limit + 1),
        )
    return await cursor.fetchall()


async def get_project(conn: AsyncConnection[Row], project_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {DETAIL_COLUMNS} FROM tft.projects WHERE id = %s", (project_id,)
    )
    return await cursor.fetchone()


async def list_rfp_requirements(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {REQUIREMENT_COLUMNS} FROM tft.rfp_requirements "
        "WHERE project_id = %s ORDER BY requirement_code",
        (project_id,),
    )
    return await cursor.fetchall()
