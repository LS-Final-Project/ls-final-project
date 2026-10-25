"""부서 범위 조회와 추가 전용 추천/검증 저장. caller transaction을 사용한다."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection, sql
from psycopg.types.json import Jsonb

Row = dict[str, Any]


async def request_scope(conn: AsyncConnection[Row], request_id: UUID) -> Row | None:
    cursor = await conn.execute(
        "SELECT r.*, o.dept_code, o.company_code AS organization_company, "
        "p.company_code, p.status AS project_status, p.data_origin AS project_origin "
        "FROM tft.staffing_requests r JOIN tft.organizations o ON o.id = r.organization_id "
        "JOIN tft.projects p ON p.id = r.project_id WHERE r.id = %s FOR UPDATE OF r",
        (request_id,),
    )
    return await cursor.fetchone()


async def candidates(
    conn: AsyncConnection[Row], organization_id: UUID, cursor: UUID | None, limit: int
) -> list[Row]:
    result = await conn.execute(
        "SELECT id, organization_id, display_name, job_title, data_origin, derivation_kind "
        "FROM tft.candidates WHERE organization_id = %s AND (%s::uuid IS NULL OR id > %s) "
        "ORDER BY id LIMIT %s",
        (organization_id, cursor, cursor, limit + 1),
    )
    return await result.fetchall()


async def candidate(conn: AsyncConnection[Row], candidate_id: UUID) -> Row | None:
    result = await conn.execute(
        "SELECT id, organization_id, data_origin FROM tft.candidates WHERE id = %s",
        (candidate_id,),
    )
    return await result.fetchone()


async def recommendation(conn: AsyncConnection[Row], recommendation_id: UUID) -> Row | None:
    result = await conn.execute(
        "SELECT r.*, c.organization_id FROM tft.candidate_recommendations r "
        "JOIN tft.candidates c ON c.id = r.candidate_id WHERE r.id = %s",
        (recommendation_id,),
    )
    return await result.fetchone()


async def exists_candidate(
    conn: AsyncConnection[Row], request_id: UUID, candidate_id: UUID
) -> bool:
    result = await conn.execute(
        "SELECT 1 FROM tft.candidate_recommendations WHERE staffing_request_id = %s "
        "AND candidate_id = %s",
        (request_id, candidate_id),
    )
    return await result.fetchone() is not None


async def competency_role(conn: AsyncConnection[Row], competency_id: UUID) -> UUID | None:
    result = await conn.execute(
        "SELECT required_role_id FROM tft.role_competencies WHERE id = %s", (competency_id,)
    )
    row = await result.fetchone()
    return row["required_role_id"] if row else None


async def replay(
    conn: AsyncConnection[Row], actor: UUID, request_id: UUID, operation: str, key: str
) -> Row | None:
    result = await conn.execute(
        "SELECT payload, result FROM tft.recommendation_commands WHERE actor_user_id = %s "
        "AND request_id = %s AND operation = %s AND idempotency_key = %s",
        (actor, request_id, operation, key),
    )
    return await result.fetchone()


async def remember(
    conn: AsyncConnection[Row],
    actor: UUID,
    request_id: UUID,
    operation: str,
    key: str,
    payload: Row,
    result: Row,
) -> None:
    await conn.execute(
        "INSERT INTO tft.recommendation_commands "
        "(actor_user_id, request_id, operation, idempotency_key, payload, result) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (actor, request_id, operation, key, Jsonb(payload), Jsonb(result)),
    )


async def insert(conn: AsyncConnection[Row], table: str, values: Row) -> None:
    # table and columns come exclusively from fixed route code and validated schemas.
    if table not in {
        "candidate_recommendations",
        "experience_evidence",
        "availability_checks",
        "competency_checks",
    }:
        raise ValueError("Unsupported recommendation table")
    await conn.execute(
        sql.SQL("INSERT INTO tft.{} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
        ),
        tuple(values.values()),
    )
