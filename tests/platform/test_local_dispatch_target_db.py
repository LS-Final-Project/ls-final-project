"""Real disposable PostgreSQL: targeted local claim and unchanged default selection."""

import asyncio
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.jobs.cs import CsJobRequest, CsJobSettings, admit_cs_job
from backend.api.platform.jobs.dispatch import DispatchSettings, claim_next
from backend.shared.database import connection
from scripts.local_business.dispatcher import candidate
from scripts.local_business.setup import worker_login_sql


def test_fresh_worker_logins_need_explicit_database_connect(jobs_database):
    """Use a new disposable DB; prove denied-before/connected-after exact setup grants."""
    database = "kan159_connect_test_" + uuid4().hex
    roles = ("local_cs_worker", "local_tft_worker")
    created_roles = False
    with psycopg.connect(jobs_database, autocommit=True) as admin:
        assert not admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(roles),)
        ).fetchone(), "Dedicated test cluster must not contain local runtime worker logins"
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            admin.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
            )
            statements = worker_login_sql(dict.fromkeys(roles, "test-only"), database)
            admin.execute(statements)
            created_roles = True
            grants = [
                part.strip()
                for part in statements.split(";")
                if part.strip().startswith("GRANT CONNECT ON DATABASE")
            ]
            assert len(grants) == 2
            for role, grant in zip(roles, grants, strict=True):
                admin.execute(
                    sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}").format(
                        sql.Identifier(database), sql.Identifier(role)
                    )
                )
                options = conninfo_to_dict(jobs_database)
                options.update(
                    dbname=database, user=role, password="test-only", connect_timeout="3"
                )
                dsn = make_conninfo(**options)
                with pytest.raises(psycopg.OperationalError):
                    psycopg.connect(dsn)
                admin.execute(grant)
                with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
                    assert conn.execute("SELECT current_user").fetchone() == (role,)
                    assert conn.execute(
                        "SELECT has_database_privilege(current_user,current_database(),'CREATE'),"
                        "has_database_privilege(current_user,current_database(),'TEMP')"
                    ).fetchone() == (False, False)
                    assert conn.execute(
                        "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
                        "FROM pg_roles WHERE rolname=current_user"
                    ).fetchone() == (False, False, False, False, False)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
            if created_roles:
                for role in roles:
                    admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


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
