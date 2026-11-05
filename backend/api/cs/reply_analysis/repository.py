"""고객 회신 해석 시도 저장(추가만)과 해석 맥락 조회. 표는 infra/migrations/cs C31."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.api.cs.summary.repository import USAGE

Row = dict[str, Any]
COLUMNS = (
    "id, ticket_id, message_id, outcome, error_code, interpretation, reply_update, "
    "input_version, input_sha256, prompt_version, requested_model, model_version, created_at"
)
REPLY_COLUMNS = "message_id, ticket_id, received_at, body"


async def reply(conn: AsyncConnection[Row], ticket_id: UUID, message_id: UUID | None) -> Row | None:
    """해석할 연결된 회신. message_id가 없으면 본문이 있는 가장 최근 회신이다.

    message_id가 다른 문의의 회신이면 None(문의 없음과 같게 404). 본문이 없는 회신은 그대로 준다.
    """
    if message_id is not None:
        cursor = await conn.execute(
            f"SELECT {REPLY_COLUMNS} FROM cs.ticket_customer_replies "
            "WHERE ticket_id = %s AND message_id = %s",
            (ticket_id, message_id),
        )
    else:
        cursor = await conn.execute(
            f"SELECT {REPLY_COLUMNS} FROM cs.ticket_customer_replies "
            "WHERE ticket_id = %s AND body IS NOT NULL "
            "ORDER BY received_at DESC, message_id DESC LIMIT 1",
            (ticket_id,),
        )
    return await cursor.fetchone()


async def confirmed_serial(conn: AsyncConnection[Row], ticket_id: UUID) -> str | None:
    """문의에 확정 연결된 장비의 시리얼. 없으면 None. 대조에만 쓰고 모델 입력에 넣지 않는다."""
    cursor = await conn.execute(
        "SELECT u.serial_no FROM cs.ticket_unit_links l "
        "JOIN cs.installed_units u ON u.id = l.unit_id "
        "WHERE l.ticket_id = %s AND l.link_status = 'CONFIRMED'",
        (ticket_id,),
    )
    row = await cursor.fetchone()
    return row["serial_no"] if row else None


async def latest(conn: AsyncConnection[Row], message_id: UUID, *, valid: bool) -> Row | None:
    """그 회신의 가장 최근 시도. valid면 성공한 것만 본다."""
    where = " AND outcome = 'VALID'" if valid else ""
    cursor = await conn.execute(
        f"SELECT {COLUMNS} FROM cs.ticket_reply_analyses WHERE message_id = %s{where} "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (message_id,),
    )
    return await cursor.fetchone()


async def matching(
    conn: AsyncConnection[Row], message_id: UUID, sha: str, prompt_version: str
) -> Row | None:
    """같은 회신·입력·프롬프트 판본의 성공한 해석. 있으면 모델을 다시 부르지 않는다."""
    cursor = await conn.execute(
        f"SELECT {COLUMNS} FROM cs.ticket_reply_analyses WHERE message_id = %s "
        "AND outcome = 'VALID' AND input_sha256 = %s AND prompt_version = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (message_id, sha, prompt_version),
    )
    return await cursor.fetchone()


async def insert(conn: AsyncConnection[Row], values: Row) -> Row | None:
    """시도 한 행을 더한다. 같은 회신·입력·판본의 성공한 해석이 이미 있으면(동시 생성) None."""
    names = (
        "id",
        "ticket_id",
        "message_id",
        "outcome",
        "error_code",
        "interpretation",
        "reply_update",
        "input_version",
        "input_sha256",
        "prompt_version",
        "requested_model",
        "model_version",
        "requested_by",
        *USAGE,
    )
    params = tuple(
        Jsonb(values[name])
        if name in ("interpretation", "reply_update") and values.get(name) is not None
        else values.get(name)
        for name in names
    )
    cursor = await conn.execute(
        f"INSERT INTO cs.ticket_reply_analyses ({', '.join(names)}) "
        f"VALUES ({', '.join(['%s'] * len(names))}) "
        "ON CONFLICT (message_id, input_sha256, prompt_version) WHERE outcome = 'VALID' "
        f"DO NOTHING RETURNING {COLUMNS}",
        params,
    )
    return await cursor.fetchone()
