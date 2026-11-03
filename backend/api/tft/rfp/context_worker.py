"""Run context grouping under the existing TFT job, lease and model accounting boundary."""

from threading import Lock
from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.agent.processor_registry import Emit, TftJob
from backend.api.tft.rfp import context_repository as db
from backend.api.tft.rfp.jobs import context_input_version
from backend.engine.rfp.context_graph import ContextProvider, run_context
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


async def process_context(
    settings: Settings,
    job: TftJob,
    emit: Emit,
    provider: ContextProvider,
    *,
    check_input: bool = True,
) -> dict[str, Any]:
    """Snapshot source, release its transaction for AI, then CAS an immutable result.

    A later source edit makes the snapshot stale; no original requirement or decision is updated.
    The worker remains the sole owner of runtime job status and external-call accounting.
    """
    dsn = settings.database_url.get_secret_value() if settings.database_url else ""
    async with await psycopg.AsyncConnection.connect(
        dsn,
        row_factory=dict_row,
        autocommit=True,
    ) as conn:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor = await conn.execute(
                "SELECT project_id,payload FROM tft.context_versions WHERE source_job_id=%s",
                (job.job_id,),
            )
            saved = await cursor.fetchone()
            if saved:
                if saved["project_id"] != job.project_id:
                    raise ValueError("TFT_CONTEXT_JOB_PROJECT_MISMATCH")
                return {
                    "counts": {
                        "requirements": len(saved["payload"]["assignments"]),
                        "proposals": 0,
                    },
                    "needs_review": True,
                }
            source = await db.load_source(conn, job.project_id)
        if (
            check_input
            and context_input_version(source.fingerprint, source.base_context_version)
            != job.input_version
        ):
            raise ApplicationError(
                409, "TFT_CONTEXT_SOURCE_CHANGED", "Context input changed before processing."
            )
        if not source.requirements:
            # Drawing-only processing may have topology but no extracted text requirements.
            return {"counts": {"requirements": 0, "proposals": 0}, "needs_review": True}
        lock = Lock()

        def progress(step: str) -> None:
            with lock:
                emit({"step": step})

        result = await run_context(
            source.requirements,
            provider,
            diagram_source_ids=source.diagram_source_ids,
            progress=progress,
        )
        progress("context_validate")
        async with conn.transaction():
            await db.save_generated(conn, job.project_id, job.actor_id, job.job_id, source, result)
        return {
            "counts": {"requirements": len(source.requirements), "proposals": 0},
            "needs_review": True,
        }
