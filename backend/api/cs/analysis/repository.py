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
CORRECTION_COLUMNS = (
    "id, ticket_id, seq, based_on_analysis_id, fields, reason, corrected_by, provenance_kind, "
    "created_at"
)


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
        "input_version, input_sha256, model_version, prompt_version, error_code) "
        "SELECT %s, %s, COALESCE(max(run_seq), 0) + 1, %s, %s, %s, %s, %s, %s, %s, %s "
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
        "input_version, input_sha256, model_version, prompt_version, error_code) "
        "SELECT %s, %s, COALESCE(max(seq), 0) + 1, %s, %s, %s, %s, %s, %s, %s, %s, %s "
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
            ticket_id,
        ),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row
