"""개선요청 AI 작성 초안 시도 저장(추가만, KAN-115). 표는 infra/migrations/cs C27."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

Row = dict[str, Any]
COLUMNS = (
    "id, request_id, request_version, outcome, error_code, draft, input_version, input_sha256, "
    "prompt_version, requested_model, model_version, created_at"
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


# Active evidence with the ticket text and its resolution record, oldest ticket first.
EVIDENCE_SQL = (
    "SELECT e.ticket_id, t.external_id AS ticket_external_id, t.received_at, "
    "t.product_code, t.subject, t.body, t.fw_version_reported, t.error_code_reported, "
    "e.case_id, c.external_id AS case_external_id, c.known_at, c.fault_id, "
    "c.fw_version_at_issue, c.root_cause, c.root_cause_type, c.action_taken, "
    "c.fw_fixed_version, c.resolved_by_dept "
    "FROM cs.request_evidence e JOIN cs.tickets t ON t.id = e.ticket_id "
    "LEFT JOIN cs.resolution_cases c ON c.id = e.case_id "
    "WHERE e.request_id = %s AND e.removed_at IS NULL ORDER BY t.received_at, e.ticket_id"
)


async def evidence_rows(conn: AsyncConnection[Row], request_id: UUID) -> list[Row]:
    cursor = await conn.execute(EVIDENCE_SQL, (request_id,))
    return await cursor.fetchall()


async def latest(conn: AsyncConnection[Row], request_id: UUID, *, valid: bool) -> Row | None:
    """가장 최근 시도. valid면 성공한 것만 본다."""
    where = " AND outcome = 'VALID'" if valid else ""
    cursor = await conn.execute(
        f"SELECT {COLUMNS} FROM cs.improvement_ai_drafts WHERE request_id = %s{where} "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (request_id,),
    )
    return await cursor.fetchone()


async def matching(
    conn: AsyncConnection[Row], request_id: UUID, sha: str, prompt_version: str
) -> Row | None:
    """같은 입력·프롬프트 판본의 성공한 초안. 있으면 모델을 다시 부르지 않는다."""
    cursor = await conn.execute(
        f"SELECT {COLUMNS} FROM cs.improvement_ai_drafts WHERE request_id = %s "
        "AND outcome = 'VALID' AND input_sha256 = %s AND prompt_version = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (request_id, sha, prompt_version),
    )
    return await cursor.fetchone()


async def insert(conn: AsyncConnection[Row], values: Row) -> Row | None:
    """시도 한 행을 더한다. 같은 입력·판본의 성공한 초안이 이미 있으면(동시 생성) None."""
    names = (
        "id",
        "request_id",
        "request_version",
        "outcome",
        "error_code",
        "draft",
        "input_version",
        "input_sha256",
        "prompt_version",
        "requested_model",
        "model_version",
        "requested_by",
        *USAGE,
    )
    params = tuple(
        Jsonb(values["draft"])
        if name == "draft" and values.get("draft") is not None
        else values.get(name)
        for name in names
    )
    cursor = await conn.execute(
        f"INSERT INTO cs.improvement_ai_drafts ({', '.join(names)}) "
        f"VALUES ({', '.join(['%s'] * len(names))}) "
        "ON CONFLICT (request_id, input_sha256, prompt_version) WHERE outcome = 'VALID' "
        f"DO NOTHING RETURNING {COLUMNS}",
        params,
    )
    return await cursor.fetchone()
