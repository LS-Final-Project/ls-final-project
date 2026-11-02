"""Real disposable PostgreSQL: targeted local claim and unchanged default selection."""

import asyncio
from uuid import uuid4

import psycopg

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.jobs.cs import CsJobRequest, CsJobSettings, admit_cs_job
from backend.api.platform.jobs.dispatch import DispatchSettings, claim_next
from backend.shared.database import connection
from scripts.local_business.dispatcher import candidate


def test_targeted_new_ui_job_excludes_old_queued_but_default_still_selects_old(
    settings, jobs_database, registered_user, cs_job
):
    gate = CsJobSettings(
        _env_file=None,
        enabled=True,
        processor_connected=True,
        service_url="https://platform.example.run.app",
        invoker_email="cs-runtime@proj-test.iam.gserviceaccount.com",
    )
    access = EvaluationAccessSettings(_env_file=None, local_admission_enabled=False)
    limits = DispatchSettings(enabled=True, max_attempts=1)
    with psycopg.connect(jobs_database) as conn:
        after = conn.execute("SELECT clock_timestamp()").fetchone()[0]

    async def admit():
        async with connection(settings) as conn, conn.transaction():
            return (
                await admit_cs_job(
                    conn,
                    CsJobRequest(
                        ticket_id=uuid4(),
                        input_version="a" * 64,
                        idempotency_key=str(uuid4()),
                        requested_by=registered_user[1],
                    ),
                )
            ).job_id

    fresh = asyncio.run(admit())
    try:
        assert candidate(settings, "cs", after)["id"] == fresh
        assert asyncio.run(claim_next(settings, access, limits, cs=gate, job_id=uuid4())) is None
        targeted = asyncio.run(claim_next(settings, access, limits, cs=gate, job_id=fresh))
        assert targeted.job_id == fresh
        with psycopg.connect(jobs_database) as conn:
            assert conn.execute(
                "SELECT state,attempt_no FROM runtime.dispatch_outbox WHERE job_id=%s", (cs_job,)
            ).fetchone() == ("PENDING", 0)
        default = asyncio.run(claim_next(settings, access, limits, cs=gate))
        assert default.job_id == cs_job
    finally:
        with psycopg.connect(jobs_database) as conn:
            for table in ("job_control", "dispatch_attempts", "dispatch_outbox", "job_events"):
                conn.execute(f"DELETE FROM runtime.{table} WHERE job_id=%s", (fresh,))
            conn.execute("DELETE FROM runtime.jobs WHERE id=%s", (fresh,))
