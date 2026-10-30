"""Queries for analysis runs and classification corrections (infra/migrations/cs KAN-107)."""

from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

Row = dict[str, Any]
RUN_COLUMNS = (
    "id, ticket_id, run_seq, job_id, outcome, classification, input_version, input_sha256, "
    "model_version, prompt_version, error_code, provenance_kind, created_at"
)
DRAFT_COLUMNS = (
    "id, ticket_id, seq, analysis_id, job_id, outcome, draft, input_version, input_sha256, "
    "model_version, prompt_version, error_code, provenance_kind, created_at"
)
# 시도 사용량(마이그레이션 20261102_KAN-107_attempt_usage.sql). 저장만 하고 조회 응답에는 내지 않는다.
USAGE_FIELDS = (
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "thought_tokens",
    "cached_input_tokens",
    "total_tokens",
    "cost_usd",
    "pricing_version",
)
USAGE_COLUMNS = ", ".join(USAGE_FIELDS)
USAGE_SLOTS = ", ".join(["%s"] * len(USAGE_FIELDS))
CORRECTION_COLUMNS = (
    "id, ticket_id, seq, based_on_analysis_id, fields, reason, corrected_by, provenance_kind, "
    "created_at"
)


def usage_params(values: Row) -> list[Any]:
    """USAGE_FIELDS 순서의 값. 없는 칸은 null이다(사람이 직접 넣은 행·옛 호출자)."""
    return [values.get(name) for name in USAGE_FIELDS]


async def list_runs(conn: AsyncConnection[Row], ticket_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {RUN_COLUMNS} FROM cs.ticket_analyses WHERE ticket_id = %s ORDER BY run_seq",
        (ticket_id,),
    )
    return await cursor.fetchall()


async def list_corrections(conn: AsyncConnection[Row], ticket_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {CORRECTION_COLUMNS} FROM cs.ticket_classification_corrections "
        "WHERE ticket_id = %s ORDER BY seq",
        (ticket_id,),
    )
    return await cursor.fetchall()


async def customer_tier(conn: AsyncConnection[Row], ticket_id: UUID) -> str | None:
    """Tier recorded with the provided ticket. Manual tickets have none."""
    cursor = await conn.execute(
        "SELECT customer_tier FROM cs.ticket_source_records WHERE ticket_id = %s", (ticket_id,)
    )
    row = await cursor.fetchone()
    return row["customer_tier"] if row else None


async def insert_run(conn: AsyncConnection[Row], ticket_id: UUID, values: Row) -> Row:
    """Next run_seq for the ticket. Call after locking the ticket row."""
    run_id = uuid4()
    cursor = await conn.execute(
        "INSERT INTO cs.ticket_analyses (id, ticket_id, run_seq, job_id, outcome, classification, "
        f"input_version, input_sha256, model_version, prompt_version, error_code, {USAGE_COLUMNS}) "
        f"SELECT %s, %s, COALESCE(max(run_seq), 0) + 1, %s, %s, %s, %s, %s, %s, %s, %s, {USAGE_SLOTS} "
        f"FROM cs.ticket_analyses WHERE ticket_id = %s RETURNING {RUN_COLUMNS}",
        (
            run_id,
            ticket_id,
            values.get("job_id"),
            values["outcome"],
            Jsonb(values["classification"]) if values.get("classification") is not None else None,
            values["input_version"],
            values.get("input_sha256"),
            values.get("model_version"),
            values.get("prompt_version"),
            values.get("error_code"),
            *usage_params(values),
            ticket_id,
        ),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row


async def insert_correction(conn: AsyncConnection[Row], values: Row) -> None:
    await conn.execute(
        "INSERT INTO cs.ticket_classification_corrections "
        "(id, ticket_id, seq, based_on_analysis_id, fields, reason, corrected_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            values["id"],
            values["ticket_id"],
            values["seq"],
            values["based_on_analysis_id"],
            Jsonb(values["fields"]),
            values["reason"],
            values["corrected_by"],
        ),
    )


async def list_drafts(conn: AsyncConnection[Row], ticket_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {DRAFT_COLUMNS} FROM cs.ticket_drafts WHERE ticket_id = %s ORDER BY seq",
        (ticket_id,),
    )
    return await cursor.fetchall()


async def get_draft(conn: AsyncConnection[Row], ticket_id: UUID, draft_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {DRAFT_COLUMNS} FROM cs.ticket_drafts WHERE ticket_id = %s AND id = %s",
        (ticket_id, draft_id),
    )
    return await cursor.fetchone()


async def insert_draft(conn: AsyncConnection[Row], ticket_id: UUID, values: Row) -> Row:
    """Next seq for the ticket. Call after locking the ticket row."""
    cursor = await conn.execute(
        "INSERT INTO cs.ticket_drafts (id, ticket_id, seq, analysis_id, job_id, outcome, draft, "
        f"input_version, input_sha256, model_version, prompt_version, error_code, {USAGE_COLUMNS}) "
        f"SELECT %s, %s, COALESCE(max(seq), 0) + 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, {USAGE_SLOTS} "
        f"FROM cs.ticket_drafts WHERE ticket_id = %s RETURNING {DRAFT_COLUMNS}",
        (
            uuid4(),
            ticket_id,
            values.get("analysis_id"),
            values.get("job_id"),
            values["outcome"],
            Jsonb(values["draft"]) if values.get("draft") is not None else None,
            values["input_version"],
            values.get("input_sha256"),
            values.get("model_version"),
            values.get("prompt_version"),
            values.get("error_code"),
            *usage_params(values),
            ticket_id,
        ),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row


async def run_belongs(conn: AsyncConnection[Row], ticket_id: UUID, run_id: UUID) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM cs.ticket_analyses WHERE id = %s AND ticket_id = %s", (run_id, ticket_id)
    )
    return await cursor.fetchone() is not None


VERIFICATION_COLUMNS = (
    "id, ticket_id, draft_id, job_id, attempt_number, outcome, sentences, input_version, "
    "model_version, prompt_version, error_code, created_at"
)


async def list_verifications(conn: AsyncConnection[Row], ticket_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {VERIFICATION_COLUMNS} FROM cs.ticket_draft_verifications "
        "WHERE ticket_id = %s ORDER BY created_at, id",
        (ticket_id,),
    )
    return await cursor.fetchall()


async def insert_verification(conn: AsyncConnection[Row], ticket_id: UUID, values: Row) -> Row:
    cursor = await conn.execute(
        "INSERT INTO cs.ticket_draft_verifications "
        "(id, ticket_id, draft_id, job_id, attempt_number, outcome, sentences, input_version, "
        "input_sha256, model_version, requested_model, prompt_version, instruction_fingerprint, "
        f"error_code, call_attempts, {USAGE_COLUMNS}) "
        f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, {USAGE_SLOTS}) "
        f"RETURNING {VERIFICATION_COLUMNS}",
        (
            uuid4(),
            ticket_id,
            values["draft_id"],
            values.get("job_id"),
            values["attempt_number"],
            values["outcome"],
            Jsonb(values["sentences"]),
            values["input_version"],
            values.get("input_sha256"),
            values.get("model_version"),
            values.get("requested_model"),
            values["prompt_version"],
            values.get("instruction_fingerprint"),
            values.get("error_code"),
            values.get("call_attempts"),
            *usage_params(values),
        ),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row
