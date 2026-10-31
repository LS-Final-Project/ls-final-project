"""명시한 초안 판본만 모델로 작성한다. 호출 중 DB 트랜잭션·프로젝트 잠금을 보유하지 않는다."""

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.agent.processor_registry import Emit, TftJob
from backend.api.tft.rfp import commands, repository
from backend.api.tft.rfp import draft_repository as db
from backend.api.tft.rfp import schemas as s
from backend.engine.rfp.draft_rewrite import PROMPT_VERSION, Call, Result, rewrite
from backend.shared.ai import Generation, usage_cost
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
ZERO = {"requirements": 0, "proposals": 0}


def request_id(project: UUID, actor: UUID, key: str) -> UUID:
    return uuid5(project, f"draft_rewrite:{actor}:{key}")


def input_version(id: UUID, version: int) -> str:
    return hashlib.sha256(json.dumps(["draft_rewrite", str(id), version]).encode()).hexdigest()


async def reserve(
    conn: AsyncConnection[Row], project: UUID, actor: UUID, key: str, version: int
) -> str:
    id = request_id(project, actor, key)
    digest = input_version(id, version)
    await db.lock(conn, project)
    cursor = await conn.execute("SELECT * FROM tft.draft_rewrites WHERE id=%s", (id,))
    existing = await cursor.fetchone()
    if existing is not None:
        if existing["base_version"] != version:
            raise ApplicationError(409, "IDEMPOTENCY_CONFLICT", "The key has different content.")
        return existing["input_version"]
    if await db.head(conn, project) != version:
        raise repository.version_conflict()
    snapshot = await db.load_snapshot(conn, project, version)
    if snapshot is None:
        raise ApplicationError(404, "TFT_DRAFT_NOT_FOUND", "The draft was not found.")
    await conn.execute(
        "INSERT INTO tft.draft_rewrites(id,project_id,base_version,actor_id,request_key,input_version) VALUES (%s,%s,%s,%s,%s,%s)",
        (id, project, version, actor, key, digest),
    )
    return digest


async def record_calls(conn: AsyncConnection[Row], id: UUID, calls: list[Call], model: str) -> None:
    for call in calls:
        cost, price = (
            (Decimal(0), "not_called") if call.attempt == 0 else usage_cost(call.usage, model)
        )
        await conn.execute(
            "INSERT INTO tft.draft_rewrite_calls(rewrite_id,section,attempt,outcome,reasons,prompt_version,requested_model,returned_model,model_version,"
            "input_tokens,output_tokens,thought_tokens,cached_input_tokens,total_tokens,cost_usd,price_basis,elapsed_ms,provider_attempts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                id,
                call.section,
                call.attempt,
                call.outcome,
                Jsonb(call.reasons),
                PROMPT_VERSION,
                model,
                call.model,
                call.model_version,
                call.usage.input_tokens,
                call.usage.output_tokens,
                call.usage.thought_tokens,
                call.usage.cached_input_tokens,
                call.usage.total_tokens,
                cost,
                price,
                call.elapsed_ms,
                call.provider_attempts,
            ),
        )


def completed(draft: s.Draft | None = None, fallback: bool = False) -> Row:
    return {
        "counts": ZERO,
        "needs_review": fallback
        or bool(
            draft
            and any(
                row.needs_rereview or row.evidence_missing
                for part in draft.sections
                for row in part.sentences
            )
        ),
    }


async def snapshot_for(conn: AsyncConnection[Row], job: TftJob) -> tuple[Row, s.Draft | None]:
    """잠금 안에서 입력을 읽고 트랜잭션을 끝낸다. 끝난 작업 재시도에는 모델을 부르지 않는다."""
    async with conn.transaction():
        await db.lock(conn, job.project_id)
        cursor = await conn.execute(
            "SELECT * FROM tft.draft_rewrites WHERE project_id=%s AND input_version=%s",
            (job.project_id, job.input_version),
        )
        record = await cursor.fetchone()
        if (
            record is None
            or record["actor_id"] != job.actor_id
            or record["job_id"] not in (None, job.job_id)
        ):
            raise ValueError("TFT_DRAFT_REWRITE_JOB_MISMATCH")
        if (
            record["state"] != "reserved"
            or await db.generated(conn, job.project_id, job.job_id) is not None
        ):
            return record, None
        if await db.head(conn, job.project_id) != record["base_version"]:
            await conn.execute(
                "UPDATE tft.draft_rewrites SET state='discarded' WHERE id=%s", (record["id"],)
            )
            return record, None
        snapshot = await db.load_snapshot(conn, job.project_id, record["base_version"])
        if snapshot is None:
            raise ValueError("TFT_DRAFT_VERSION_MISSING")
        return record, snapshot


async def apply_result(
    conn: AsyncConnection[Row], job: TftJob, record: Row, result: Result, model: str
) -> bool:
    """사람 저장이 앞섰으면 모델 결과를 버린다. 기존 판본/이력을 변경하지 않는다."""
    async with conn.transaction():
        await db.lock(conn, job.project_id)
        if await db.generated(conn, job.project_id, job.job_id) is not None:
            return False
        await record_calls(conn, record["id"], result.calls, model)
        if await db.head(conn, job.project_id) != record["base_version"]:
            await conn.execute(
                "UPDATE tft.draft_rewrites SET state='discarded' WHERE id=%s", (record["id"],)
            )
            return False
        result.draft.version = record["base_version"] + 1
        result.draft.known_at = datetime.now(UTC)
        human_keys = [
            row.sentence_id
            for part in result.draft.sections
            for row in part.sentences
            if row.provenance_kind == "human_input"
        ]
        reasons, _ = await db.human_lineage(
            conn, job.project_id, record["base_version"], human_keys
        )
        await db.save(
            conn, result.draft, actor=job.actor_id, source_job_id=job.job_id, reasons=reasons
        )
        await conn.execute(
            "UPDATE tft.draft_rewrites SET state='saved',result_version=%s WHERE id=%s",
            (result.draft.version, record["id"]),
        )
        await commands.audit(
            conn,
            project_id=job.project_id,
            actor=job.actor_id,
            entity="draft",
            entity_id=result.draft.version,
            action="draft.rewrite.complete",
            detail={"rewritten": result.rewritten, "fallback": result.fallback},
        )
    return True


async def process(
    conn: AsyncConnection[Row],
    job: TftJob,
    emit: Emit,
    provider: Callable[[str], Generation],
    model: str,
) -> Row:
    record, snapshot = await snapshot_for(conn, job)
    if snapshot is None:
        return completed()
    result = await asyncio.to_thread(
        rewrite, snapshot, provider, progress=lambda: emit({"step": "draft", "counts": ZERO})
    )
    # 모델 호출이 끝나고 소유권/취소를 재검사한 뒤에만 저장한다.
    emit({"step": "draft", "counts": ZERO})
    applied = await apply_result(conn, job, record, result, model)
    return completed(result.draft if applied else None, bool(result.fallback) if applied else False)
