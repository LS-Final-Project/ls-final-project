"""한국어 요약 시도 저장(추가만). 표는 infra/migrations/cs C23."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

Row = dict[str, Any]
COLUMNS = (
    "id, ticket_id, outcome, error_code, summary_ko, input_version, input_sha256, prompt_version, "
    "requested_model, model_version, created_at"
)
USAGE = (
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "thought_tokens",
    "cached_input_tokens",
    "total_tokens",
    "cost_usd",
    "pricing_version",
)


async def latest(conn: AsyncConnection[Row], ticket_id: UUID, *, valid: bool) -> Row | None:
    """가장 최근 시도. valid면 성공한 것만 본다."""
    where = " AND outcome = 'VALID'" if valid else ""
    cursor = await conn.execute(
        f"SELECT {COLUMNS} FROM cs.ticket_summaries WHERE ticket_id = %s{where} "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (ticket_id,),
    )
    return await cursor.fetchone()


async def matching(
    conn: AsyncConnection[Row], ticket_id: UUID, sha: str, prompt_version: str
) -> Row | None:
    """같은 입력·프롬프트 판본의 성공한 요약. 있으면 모델을 다시 부르지 않는다."""
    cursor = await conn.execute(
        f"SELECT {COLUMNS} FROM cs.ticket_summaries WHERE ticket_id = %s AND outcome = 'VALID' "
        "AND input_sha256 = %s AND prompt_version = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (ticket_id, sha, prompt_version),
    )
    return await cursor.fetchone()


async def insert(conn: AsyncConnection[Row], values: Row) -> Row:
    names = (
        "id",
        "ticket_id",
        "outcome",
        "error_code",
        "summary_ko",
        "input_version",
        "input_sha256",
        "prompt_version",
        "requested_model",
        "model_version",
        "requested_by",
        *USAGE,
    )
    cursor = await conn.execute(
        f"INSERT INTO cs.ticket_summaries ({', '.join(names)}) "
        f"VALUES ({', '.join(['%s'] * len(names))}) RETURNING {COLUMNS}",
        tuple(values.get(name) for name in names),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row
