"""Context worker real-role storage and failure boundaries on the isolated test database."""

# ruff: noqa: F811
import asyncio
from uuid import uuid4

import psycopg
import pytest

from backend.agent.processor_registry import TftJob
from backend.api.tft.rfp import context_repository as db
from backend.api.tft.rfp.context_worker import process_context
from backend.api.tft.rfp.jobs import context_input_version
from backend.engine.rfp.context_graph import fake_provider
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings
from tests.tft.test_rfp_context_db import run
from tests.tft.test_rfp_http_base_db import SALES, admin, chain  # noqa: F401
from tests.tft.test_worker_processor_db import worker_dsn  # noqa: F401


def prepared(admin, worker_dsn, chain):
    source = run(admin, lambda c: db.load_source(c, chain["project"]))
    job = TftJob(
        uuid4(),
        "rfp.context",
        chain["project"],
        context_input_version(source.fingerprint, source.base_context_version),
        SALES,
    )
    return Settings(_env_file=None, environment="test", database_url=worker_dsn), job


def test_context_worker_real_role_commits_once_and_replay_does_not_call_model(
    admin, worker_dsn, chain
):
    settings, job = prepared(admin, worker_dsn, chain)
    events = []
    summary = asyncio.run(process_context(settings, job, events.append, fake_provider))
    assert summary == {"counts": {"requirements": 1, "proposals": 0}, "needs_review": True}
    assert {e["step"] for e in events} == {"context_map", "context_link", "context_validate"}
    value = run(admin, lambda c: db.load_context(c, job.project_id))
    assert value.version == 1 and value.result.assignments[0].primary_category == "UNCLASSIFIED"

    async def forbidden(*_args):
        raise AssertionError("replay must not call a model")

    assert asyncio.run(process_context(settings, job, events.append, forbidden)) == summary
    assert run(admin, lambda c: db.load_context(c, job.project_id)).version == 1


def test_context_worker_stale_admission_blocks_before_provider(admin, worker_dsn, chain):
    settings, job = prepared(admin, worker_dsn, chain)
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.document_versions SET sha256=%s WHERE id=%s", ("2" * 64, chain["docver"])
        )

    async def forbidden(*_args):
        raise AssertionError("stale input must not call a model")

    with pytest.raises(ApplicationError) as error:
        asyncio.run(process_context(settings, job, lambda _e: None, forbidden))
    assert error.value.code == "TFT_CONTEXT_SOURCE_CHANGED"
    assert run(admin, lambda c: db.load_context(c, job.project_id)).version is None


def test_context_worker_accounting_failure_never_becomes_success_or_saved_result(
    admin, worker_dsn, chain
):
    settings, job = prepared(admin, worker_dsn, chain)

    class UnknownCharge(BaseException):
        pass

    async def fail(*_args):
        raise UnknownCharge("authored accounting uncertainty")

    with pytest.raises(UnknownCharge):
        asyncio.run(process_context(settings, job, lambda _e: None, fail))
    assert run(admin, lambda c: db.load_context(c, job.project_id)).version is None


def test_context_worker_concurrent_result_discards_stale_candidate(admin, worker_dsn, chain):
    settings, job = prepared(admin, worker_dsn, chain)
    from tests.tft.test_rfp_context_db import result

    changed = False

    async def provider(stage, payload):
        nonlocal changed
        if not changed:
            changed = True
            # Separate connection emulates a competing accepted context result during model work.
            async with await psycopg.AsyncConnection.connect(
                admin, row_factory=psycopg.rows.dict_row
            ) as conn:
                source = await db.load_source(conn, job.project_id)
                await db.save_generated(
                    conn, job.project_id, SALES, uuid4(), source, result(source)
                )
        return await fake_provider(stage, payload)

    with pytest.raises(ApplicationError) as error:
        asyncio.run(process_context(settings, job, lambda _e: None, provider))
    assert error.value.code == "VERSION_CONFLICT"
    stored = run(admin, lambda c: db.load_context(c, job.project_id))
    assert stored.version == 1 and stored.result.assignments[0].primary_category == "QUALITY"
