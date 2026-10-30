"""KAN-201 실제 역할·동시 펼침·원자성. AUTH_TEST_DATABASE_URL이 없으면 CI에 위임한다."""

import asyncio
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier
from pydantic import SecretStr

from backend.agent import job_runtime
from backend.api.platform.notifications import repository
from backend.shared.notifications import enqueue_notification, enqueue_notification_sync

MIGRATION = Path("infra/migrations/platform/20261101_KAN-201_notifications.sql")


@pytest.fixture
def users(jobs_database):
    ids = [uuid4() for _ in range(5)]
    with psycopg.connect(jobs_database) as conn:
        for index, user in enumerate(ids):
            conn.execute(
                "INSERT INTO identity.users(id,tid,oid,email,display_name,dept,is_active) VALUES (%s,%s,%s,'notification@example.invalid','Notification test','TS_AUTO',%s)",
                (user, uuid4(), uuid4(), index != 4),
            )
            role = ("cs_agent", "cs_agent", "dept_staff", "manager", "cs_agent")[index]
            conn.execute(
                "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,%s)", (user, role)
            )
        conn.execute(
            "INSERT INTO identity.manager_departments(user_id,dept_code) VALUES (%s,'TS_AUTO')",
            (ids[3],),
        )
    yield ids
    with psycopg.connect(jobs_database) as conn:
        conn.execute("DELETE FROM runtime.user_notifications WHERE user_id=ANY(%s)", (ids,))
        conn.execute("DELETE FROM identity.manager_departments WHERE user_id=ANY(%s)", (ids,))
        conn.execute("DELETE FROM identity.user_roles WHERE user_id=ANY(%s)", (ids,))
        conn.execute("DELETE FROM identity.users WHERE id=ANY(%s)", (ids,))


def role_connection(dsn, role):
    conn = psycopg.connect(dsn)
    conn.execute(SQL("SET ROLE {}").format(Identifier(role)))
    return conn


def test_migration_reapplication_and_actual_writer_roles_need_no_select(jobs_database):
    with psycopg.connect(jobs_database) as conn:
        sql = "\n".join(
            line for line in MIGRATION.read_text().splitlines() if not line.startswith("\\")
        )
        conn.execute(sql)
        conn.execute(sql)
    for role in ("cs_app", "cs_worker", "tft_worker"):
        subject = uuid4()
        values = {
            "kind": "cs.review_wait",
            "subject_id": subject,
            "audience": [{"role": "cs_agent"}],
            "actor_id": None,
            "dedupe_key": f"cs.review_wait:{subject}",
        }
        with role_connection(jobs_database, role) as conn:
            assert enqueue_notification_sync(conn, **values) is True
            assert enqueue_notification_sync(conn, **values) is False
        for sql in (
            "SELECT dedupe_key FROM runtime.notification_intents",
            "UPDATE runtime.notification_intents SET expanded_at=now()",
            "DELETE FROM runtime.notification_intents",
            "SELECT * FROM runtime.user_notifications",
            "SELECT id FROM identity.users",
        ):
            with role_connection(jobs_database, role) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(sql)
    with role_connection(jobs_database, "platform_app") as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE runtime.notification_intents SET title='C/S 답변 확인 대기'")


def test_expansion_filters_active_scopes_dedupes_excludes_actor_and_preserves_read_time(
    settings, jobs_database, users
):
    subject = uuid4()
    audience = [
        {"user": users[1]},
        {"role": "cs_agent"},
        {"dept": "TS_AUTO"},
        {"manager_dept": "TS_AUTO"},
        {"user": users[4]},
    ]

    async def run():
        async with await psycopg.AsyncConnection.connect(
            settings.database_url.get_secret_value(), row_factory=dict_row
        ) as conn:
            await enqueue_notification(
                conn,
                kind="cs.safety",
                subject_id=subject,
                audience=audience,
                actor_id=users[0],
                dedupe_key=f"cs.safety:{subject}",
            )
            await repository.expand_pending(conn)
            row = await (
                await conn.execute(
                    "SELECT id FROM runtime.notification_intents WHERE dedupe_key=%s",
                    (f"cs.safety:{subject}",),
                )
            ).fetchone()
            intent = row["id"]
            got = await (
                await conn.execute(
                    "SELECT user_id FROM runtime.user_notifications WHERE intent_id=%s", (intent,)
                )
            ).fetchall()
            assert {r["user_id"] for r in got} & set(users) == set(users[1:4])
            assert len(got) == len({r["user_id"] for r in got})
            await repository.expand_pending(conn)
            assert await repository.mark_read(conn, users[1], intent)
            before = await (
                await conn.execute(
                    "SELECT read_at FROM runtime.user_notifications WHERE intent_id=%s AND user_id=%s",
                    (intent, users[1]),
                )
            ).fetchone()
            assert await repository.mark_read(conn, users[1], intent)
            after = await (
                await conn.execute(
                    "SELECT read_at FROM runtime.user_notifications WHERE intent_id=%s AND user_id=%s",
                    (intent, users[1]),
                )
            ).fetchone()
            assert before == after
            assert not await repository.mark_read(conn, users[0], intent)
            assert not any(
                r["id"] == intent for r in await repository.list_mine(conn, users[0], 100, 0)
            )

    asyncio.run(run())


def test_skip_locked_and_failed_expansion_roll_back_both_metadata_and_recipients(
    settings, jobs_database, users
):
    subject = uuid4()
    with psycopg.connect(jobs_database) as conn:
        enqueue_notification_sync(
            conn,
            kind="rfp.done",
            subject_id=subject,
            audience=[{"user": users[1]}],
            actor_id=None,
            dedupe_key=f"rfp.done:{subject}",
        )
        intent = conn.execute(
            "SELECT id FROM runtime.notification_intents WHERE dedupe_key=%s",
            (f"rfp.done:{subject}",),
        ).fetchone()[0]

    async def run():
        dsn = settings.database_url.get_secret_value()
        async with (
            await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as first,
            await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as second,
        ):
            await first.execute(
                "SELECT id FROM runtime.notification_intents WHERE id=%s FOR UPDATE", (intent,)
            )
            await repository.expand_pending(second)
            row = await (
                await second.execute(
                    "SELECT expanded_at FROM runtime.notification_intents WHERE id=%s", (intent,)
                )
            ).fetchone()
            assert (
                row["expanded_at"] is None
            )  # first owns the row; second does not wait or steal it.
            await second.rollback()
            await repository.expand_pending(first)
            await (
                first.rollback()
            )  # Simulate failure of the enclosing transaction after INSERT/marker.
        with psycopg.connect(jobs_database) as admin:
            assert admin.execute(
                "SELECT expanded_at FROM runtime.notification_intents WHERE id=%s", (intent,)
            ).fetchone() == (None,)
            assert admin.execute(
                "SELECT count(*) FROM runtime.user_notifications WHERE intent_id=%s", (intent,)
            ).fetchone() == (0,)
        async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
            await repository.expand_pending(conn)
            assert await repository.unread_count(conn, users[1]) >= 1

    asyncio.run(run())


@pytest.fixture
def notification_worker(settings, jobs_database):
    worker_name = "test_notification_tft_worker"
    with psycopg.connect(jobs_database) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (worker_name,)).fetchone():
            conn.execute(
                SQL("CREATE ROLE {} LOGIN PASSWORD 'test-only'").format(Identifier(worker_name))
            )
        conn.execute(SQL("GRANT tft_worker TO {}").format(Identifier(worker_name)))
    return settings.model_copy(
        update={
            "database_url": SecretStr(
                make_conninfo(jobs_database, user=worker_name, password="test-only")
            )
        }
    )


@pytest.mark.parametrize("failure", ["missing_table", "insert_error"])
def test_worker_notification_sql_failure_commits_succeeded_job_and_completed_event(
    notification_worker, jobs_database, tft_job, monkeypatch, caplog, failure
):
    worker = notification_worker
    lease = job_runtime.claim(worker, "tft", 60, tft_job)
    original = job_runtime.enqueue_notification_sync

    def broken(conn, **values):
        if failure == "missing_table":
            conn.execute(
                "INSERT INTO runtime.kan201_missing_notifications(id) VALUES (%s)", (uuid4(),)
            )
        else:
            original(conn, **values)
            # An actual failing INSERT aborts the subtransaction after a partial notification write.
            conn.execute("INSERT INTO runtime.notification_intents(id) VALUES (%s)", (uuid4(),))

    monkeypatch.setattr(job_runtime, "enqueue_notification_sync", broken)
    job_runtime.finish(worker, lease, "SUCCEEDED", {})
    with psycopg.connect(jobs_database) as conn:
        assert conn.execute(
            "SELECT status FROM runtime.jobs WHERE id=%s", (tft_job,)
        ).fetchone() == ("SUCCEEDED",)
        assert conn.execute(
            "SELECT type,payload->>'state' FROM runtime.job_events WHERE job_id=%s AND type='job.completed'",
            (tft_job,),
        ).fetchall() == [("job.completed", "SUCCEEDED")]
        assert conn.execute(
            "SELECT count(*) FROM runtime.notification_intents WHERE dedupe_key=%s",
            (f"rfp.done:{tft_job}",),
        ).fetchone() == (0,)
    assert [r.getMessage() for r in caplog.records if r.name == job_runtime.__name__] == [
        "NOTIFICATION_INTENT_FAILED"
    ]
    with pytest.raises(job_runtime.LeaseLost):
        job_runtime.finish(worker, lease, "SUCCEEDED", {})


def test_worker_notification_success_and_duplicate_finish_are_preserved(
    notification_worker, jobs_database, tft_job
):
    lease = job_runtime.claim(notification_worker, "tft", 60, tft_job)
    job_runtime.finish(notification_worker, lease, "SUCCEEDED", {})
    with pytest.raises(job_runtime.LeaseLost):
        job_runtime.finish(notification_worker, lease, "SUCCEEDED", {})
    with psycopg.connect(jobs_database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM runtime.notification_intents WHERE dedupe_key=%s",
            (f"rfp.done:{tft_job}",),
        ).fetchone() == (1,)
