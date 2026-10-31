"""Read queries for TFT projects. Tables are created by infra/migrations/tft only."""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection, errors

from backend.api.tft.rfp.repository import PROJECT_COUNTS

Row = dict[str, Any]
LOGGER = logging.getLogger(__name__)

SUMMARY_COLUMNS = (
    "p.id, p.external_id, p.name, p.company_code, p.status, p.data_origin, p.derivation_kind, "
    "p.responsible_user_id, p.sales_user_id, p.created_at, p.version, p.known_at, p.client_name, "
    "p.output_status, p.judgement_status, p.strategic_customer, p.integration_holder, "
    "p.supply_region, p.customer_manufacturer_list, p.off_list_permission, " + PROJECT_COUNTS
)
DETAIL_COLUMNS = (
    "id, external_id, version, data_origin, derivation_kind, known_at, created_at, "
    "company_code, name, client_name, status, responsible_user_id, sales_user_id"
)
INTEGRATION_COLUMNS = (
    "integration_holder, integration_holder_candidates, integration_note, integration_set_by, "
    "integration_set_at"
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
    department: str | None = None,
    *,
    output_status: str | None = None,
    judgement_status: str | None = None,
    q: str | None = None,
) -> list[Row]:
    """Newest first, only projects the user may see (access.sees_project).

    Fetches one extra row so the caller knows whether a next page exists. `department` is the
    dept_staff department. Filters and counts run in this one statement, never per project.
    """
    scope = "(p.id = ANY(%s) OR p.responsible_user_id = %s OR p.sales_user_id = %s"
    params: list[Any] = [managed_projects, user_id, user_id]
    if department is not None:
        scope += " OR %s = ANY(p.review_departments)"
        params.append(department)
    scope += ")"
    if output_status is not None:
        scope += " AND p.output_status = %s"
        params.append(output_status)
    if judgement_status is not None:
        scope += " AND p.judgement_status = %s"
        params.append(judgement_status)
    if q is not None:
        # 검색어의 %·_는 글자 그대로 찾는다.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        scope += " AND (p.name ILIKE %s OR p.client_name ILIKE %s)"
        params += [f"%{escaped}%", f"%{escaped}%"]
    if after is None:
        cursor = await conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM tft.projects p WHERE {scope} "
            "ORDER BY p.created_at DESC, p.id DESC LIMIT %s",
            (*params, limit + 1),
        )
    else:
        cursor = await conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM tft.projects p WHERE {scope} "
            "AND (p.created_at, p.id) < (%s, %s) "
            "ORDER BY p.created_at DESC, p.id DESC LIMIT %s",
            (*params, *after, limit + 1),
        )
    return await cursor.fetchall()


async def get_project(conn: AsyncConnection[Row], project_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {DETAIL_COLUMNS} FROM tft.projects WHERE id = %s", (project_id,)
    )
    return await cursor.fetchone()


async def review_departments(conn: AsyncConnection[Row], project_id: UUID) -> list[str]:
    """The project's review departments (dept_staff scope, permissions.md 4.2).

    Args:
        conn: dict_row connection.
        project_id: Project ID.

    Returns:
        list[str]: Department codes.
    """
    cursor = await conn.execute(
        "SELECT review_departments FROM tft.projects WHERE id = %s", (project_id,)
    )
    row = await cursor.fetchone()
    return list(row["review_departments"]) if row else []


async def integration_state(
    conn: AsyncConnection[Row], project_id: UUID
) -> tuple[Row, list[Row]] | None:
    """프로젝트의 통합 책임 입력을 읽는다. 조항에 묶인 최신 요구 판본만 근거가 될 수 있다.

    프로젝트 조회는 RFP 표보다 먼저 있던 핵심 읽기다. 마이그레이션(T7·T11·T12)이 아직 없는 DB에서 프로젝트 조회
    전체가 503이 되지 않게 savepoint로 격리하고, 없으면 사유 코드만 경고로 남긴다.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        tuple[Row, list[Row]] | None: (사람 입력 칸, 요구별 clause_id·clause_no·task·assembly_clause). 스키마가
            없으면 None이다(required를 거짓으로 꾸미지 않는다).
    """
    try:
        async with conn.transaction():
            cursor = await conn.execute(
                f"SELECT {INTEGRATION_COLUMNS} FROM tft.projects WHERE id = %s", (project_id,)
            )
            holder = await cursor.fetchone()
            cursor = await conn.execute(
                "SELECT v.clause_id, c.clause_no, v.task, v.assembly_clause FROM tft.requirements r "
                "JOIN tft.requirement_versions v "
                "ON v.requirement_id = r.id AND v.version = r.latest_version "
                "JOIN tft.clauses c ON c.id = v.clause_id WHERE r.project_id = %s",
                (project_id,),
            )
            rows = await cursor.fetchall()
    except (errors.UndefinedColumn, errors.UndefinedTable):
        LOGGER.warning("TFT_INTEGRATION_SCHEMA_MISSING")
        return None
    return (holder, rows) if holder is not None else None


async def list_rfp_requirements(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {REQUIREMENT_COLUMNS} FROM tft.rfp_requirements "
        "WHERE project_id = %s ORDER BY requirement_code",
        (project_id,),
    )
    return await cursor.fetchall()
