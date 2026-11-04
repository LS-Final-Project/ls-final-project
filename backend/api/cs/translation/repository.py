"""한국어 번역 시도 저장(추가만). 표는 infra/migrations/cs C28."""

import logging
from typing import Any
from uuid import UUID

import psycopg
from psycopg import AsyncConnection

from backend.api.cs.summary.repository import USAGE

Row = dict[str, Any]
logger = logging.getLogger(__name__)
COLUMNS = (
    "id, ticket_id, outcome, error_code, subject_ko, body_ko, input_version, input_sha256, "
    "prompt_version, requested_model, model_version, created_at"
)


async def latest(conn: AsyncConnection[Row], ticket_id: UUID, *, valid: bool) -> Row | None:
    """가장 최근 시도. valid면 성공한 것만 본다."""
    where = " AND outcome = 'VALID'" if valid else ""
    cursor = await conn.execute(
        f"SELECT {COLUMNS} FROM cs.ticket_translations WHERE ticket_id = %s{where} "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (ticket_id,),
    )
    return await cursor.fetchone()


async def matching(
    conn: AsyncConnection[Row], ticket_id: UUID, sha: str, prompt_version: str
) -> Row | None:
    """같은 입력·프롬프트 판본의 성공한 번역. 있으면 모델을 다시 부르지 않는다."""
    cursor = await conn.execute(
        f"SELECT {COLUMNS} FROM cs.ticket_translations WHERE ticket_id = %s AND outcome = 'VALID' "
        "AND input_sha256 = %s AND prompt_version = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (ticket_id, sha, prompt_version),
    )
    return await cursor.fetchone()


async def insert(conn: AsyncConnection[Row], values: Row) -> Row | None:
    """시도 한 행을 더한다. 같은 원문·판본의 성공한 번역이 이미 있으면(동시 생성) None."""
    names = (
        "id",
        "ticket_id",
        "outcome",
        "error_code",
        "subject_ko",
        "body_ko",
        "input_version",
        "input_sha256",
        "prompt_version",
        "requested_model",
        "model_version",
        "requested_by",
        *USAGE,
    )
    cursor = await conn.execute(
        f"INSERT INTO cs.ticket_translations ({', '.join(names)}) "
        f"VALUES ({', '.join(['%s'] * len(names))}) "
        "ON CONFLICT (ticket_id, input_sha256, prompt_version) WHERE outcome = 'VALID' DO NOTHING "
        f"RETURNING {COLUMNS}",
        tuple(values.get(name) for name in names),
    )
    return await cursor.fetchone()


async def korean_subjects(conn: AsyncConnection[Row], ticket_ids: list[UUID]) -> dict[UUID, str]:
    """목록에 보일 한국어 제목(문의별 가장 최근의 성공한 번역). 원문은 바뀌지 않아 그대로 쓴다.

    번역 표(C28)가 아직 없는 DB에서는 빈 값이다. 목록 조회를 실패로 만들지 않는다.
    """
    if not ticket_ids:
        return {}
    try:
        async with conn.transaction():
            cursor = await conn.execute(
                "SELECT DISTINCT ON (ticket_id) ticket_id, subject_ko FROM cs.ticket_translations "
                "WHERE ticket_id = ANY(%s) AND outcome = 'VALID' AND subject_ko IS NOT NULL "
                "ORDER BY ticket_id, created_at DESC, id DESC",
                (ticket_ids,),
            )
            return {row["ticket_id"]: row["subject_ko"] for row in await cursor.fetchall()}
    except psycopg.errors.UndefinedTable:
        logger.warning(
            "cs translation table missing", extra={"code": "CS_TRANSLATION_TABLE_MISSING"}
        )
        return {}
