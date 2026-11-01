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
EXPANSION = Path("infra/migrations/platform/20261110_KAN-201_notification_expansion.sql")


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
        conn.execute("DELETE FROM notify.user_notifications WHERE user_id=ANY(%s)", (ids,))
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
    for role in ("cs_app", "tft_app", "cs_worker", "tft_worker"):
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
            "SELECT dedupe_key FROM notify.notification_intents",
            "UPDATE notify.notification_intents SET expanded_at=now()",
            "DELETE FROM notify.notification_intents",
            "SELECT * FROM notify.user_notifications",
            "SELECT id FROM identity.users",
        ):
            with role_connection(jobs_database, role) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(sql)
    with psycopg.connect(jobs_database) as conn:
        for role in ("cs_app", "tft_app"):
            assert conn.execute(
                "SELECT has_schema_privilege(%s,'runtime','USAGE')", (role,)
            ).fetchone() == (False,)
            assert conn.execute(
                "SELECT has_schema_privilege(%s,'notify','USAGE')", (role,)
            ).fetchone() == (True,)
    with role_connection(jobs_database, "platform_app") as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE notify.notification_intents SET title='C/S 답변 확인 대기'")


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
            # INSERT 직후 이미 수신자가 존재한다. GET/복구 작업은 호출하지 않는다.
            row = await (
                await conn.execute(
                    "SELECT id FROM notify.notification_intents WHERE dedupe_key=%s",
                    (f"cs.safety:{subject}",),
                )
            ).fetchone()
            intent = row["id"]
            got = await (
                await conn.execute(
                    "SELECT user_id FROM notify.user_notifications WHERE intent_id=%s", (intent,)
                )
            ).fetchall()
            assert {r["user_id"] for r in got} & set(users) == set(users[1:4])
            assert len(got) == len({r["user_id"] for r in got})
            await repository.expand_pending(conn)
            assert await repository.mark_read(conn, users[1], intent)
            before = await (
                await conn.execute(
                    "SELECT read_at FROM notify.user_notifications WHERE intent_id=%s AND user_id=%s",
                    (intent, users[1]),
                )
            ).fetchone()
            assert await repository.mark_read(conn, users[1], intent)
            after = await (
                await conn.execute(
                    "SELECT read_at FROM notify.user_notifications WHERE intent_id=%s AND user_id=%s",
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
            "SELECT id FROM notify.notification_intents WHERE dedupe_key=%s",
            (f"rfp.done:{subject}",),
        ).fetchone()[0]

    # 생성 trigger 이전의 미펼침 의도를 재현한다(복구 배치의 잠금/rollback 검사).
    with psycopg.connect(jobs_database) as conn:
        conn.execute("DELETE FROM notify.user_notifications WHERE intent_id=%s", (intent,))
        conn.execute(
            "UPDATE notify.notification_intents SET expanded_at=NULL WHERE id=%s", (intent,)
        )

    async def run():
        dsn = settings.database_url.get_secret_value()
        async with (
            await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as first,
            await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as second,
        ):
            await first.execute(
                "SELECT id FROM notify.notification_intents WHERE id=%s FOR UPDATE", (intent,)
            )
            await repository.expand_pending(second)
            row = await (
                await second.execute(
                    "SELECT expanded_at FROM notify.notification_intents WHERE id=%s", (intent,)
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
                "SELECT expanded_at FROM notify.notification_intents WHERE id=%s", (intent,)
            ).fetchone() == (None,)
            assert admin.execute(
                "SELECT count(*) FROM notify.user_notifications WHERE intent_id=%s", (intent,)
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


@pytest.mark.parametrize("failure", ["missing_table", "insert_error", "expand_error"])
def test_worker_notification_sql_failure_commits_succeeded_job_and_completed_event(
    notification_worker, jobs_database, tft_job, monkeypatch, caplog, failure
):
    worker = notification_worker
    lease = job_runtime.claim(worker, "tft", 60, tft_job)
    original = job_runtime.enqueue_notification_sync

    def broken(conn, **values):
        if failure == "missing_table":
            conn.execute(
                "INSERT INTO notify.kan201_missing_notifications(id) VALUES (%s)", (uuid4(),)
            )
        elif failure == "expand_error":
            original(conn, **values)  # 수신 INSERT trigger의 실제 CHECK 오류
        else:
            original(conn, **values)
            # An actual failing INSERT aborts the subtransaction after a partial notification write.
            conn.execute("INSERT INTO notify.notification_intents(id) VALUES (%s)", (uuid4(),))

    monkeypatch.setattr(job_runtime, "enqueue_notification_sync", broken)
    if failure == "expand_error":
        with psycopg.connect(jobs_database) as conn:
            conn.execute(
                "ALTER TABLE notify.user_notifications ADD CONSTRAINT kan201_test_block CHECK(false) NOT VALID"
            )
    try:
        job_runtime.finish(worker, lease, "SUCCEEDED", {})
    finally:
        if failure == "expand_error":
            with psycopg.connect(jobs_database) as conn:
                conn.execute(
                    "ALTER TABLE notify.user_notifications DROP CONSTRAINT kan201_test_block"
                )
    with psycopg.connect(jobs_database) as conn:
        assert conn.execute(
            "SELECT status FROM runtime.jobs WHERE id=%s", (tft_job,)
        ).fetchone() == ("SUCCEEDED",)
        assert conn.execute(
            "SELECT type,payload->>'state' FROM runtime.job_events WHERE job_id=%s AND type='job.completed'",
            (tft_job,),
        ).fetchall() == [("job.completed", "SUCCEEDED")]
        assert conn.execute(
            "SELECT count(*) FROM notify.notification_intents WHERE dedupe_key=%s",
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
            "SELECT count(*) FROM notify.notification_intents WHERE dedupe_key=%s",
            (f"rfp.done:{tft_job}",),
        ).fetchone() == (1,)


def test_old_runtime_tables_move_with_rows_constraints_and_runtime_worker_rights(
    jobs_database, users
):
    """구 P30 적용 상태에서 데이터·읽음·PK/FK를 보존하고 잘못된 앱 USAGE만 회수한다."""
    sql = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("\\")
    )
    subject = uuid4()
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        assert conn.execute(
            "SELECT to_regclass('runtime.notification_intents'),to_regclass('runtime.user_notifications')"
        ).fetchone() == (None, None)
        enqueue_notification_sync(
            conn,
            kind="rfp.done",
            subject_id=subject,
            audience=[{"user": users[0]}],
            actor_id=None,
            dedupe_key=f"rfp.done:{subject}",
        )
        intent = conn.execute(
            "SELECT id FROM notify.notification_intents WHERE dedupe_key=%s",
            (f"rfp.done:{subject}",),
        ).fetchone()[0]

        # P34가 이미 만든 수신행을 실제 플랫폼 읽음 경계로 처리한다.
        # 중복 INSERT로 구 이관의 준비 단계부터 실패하지 않게 한다.
        async def mark_existing():
            async with await psycopg.AsyncConnection.connect(
                jobs_database, row_factory=dict_row
            ) as marking:
                await marking.execute("SET LOCAL ROLE platform_app")
                assert await repository.mark_read(marking, users[0], intent)

        asyncio.run(mark_existing())
        read_at = conn.execute(
            "SELECT read_at FROM notify.user_notifications WHERE intent_id=%s AND user_id=%s",
            (intent, users[0]),
        ).fetchone()[0]
        assert read_at is not None
        before = conn.execute(
            "SELECT user_id,read_at FROM notify.user_notifications WHERE intent_id=%s", (intent,)
        ).fetchall()
        runtime_rights = {
            role: conn.execute(
                "SELECT has_schema_privilege(%s,'runtime','USAGE')", (role,)
            ).fetchone()[0]
            for role in ("platform_app", "cs_worker", "tft_worker")
        }
        # Model the old P30 without rebuilding its tables or losing rows.
        conn.execute("ALTER TABLE notify.notification_intents SET SCHEMA runtime")
        conn.execute("ALTER TABLE notify.user_notifications SET SCHEMA runtime")
        conn.execute("GRANT USAGE ON SCHEMA runtime TO cs_app")
        conn.execute(sql)
        conn.execute(sql)
        assert conn.execute(
            "SELECT to_regclass('runtime.notification_intents'),to_regclass('runtime.user_notifications')"
        ).fetchone() == (None, None)
        assert conn.execute(
            "SELECT id FROM notify.notification_intents WHERE dedupe_key=%s",
            (f"rfp.done:{subject}",),
        ).fetchone() == (intent,)
        assert (
            conn.execute(
                "SELECT user_id,read_at FROM notify.user_notifications WHERE intent_id=%s",
                (intent,),
            ).fetchall()
            == before
            == [(users[0], read_at)]
        )
        constraints = conn.execute(
            "SELECT contype,confrelid::regclass::text FROM pg_constraint WHERE conrelid='notify.user_notifications'::regclass"
        ).fetchall()
        assert ("p", "-") in constraints
        assert ("f", "notify.notification_intents") in constraints and (
            "f",
            "identity.users",
        ) in constraints
        for role in ("cs_app", "tft_app"):
            assert conn.execute(
                "SELECT has_schema_privilege(%s,'runtime','USAGE')", (role,)
            ).fetchone() == (False,)
        for role, allowed in runtime_rights.items():
            assert conn.execute(
                "SELECT has_schema_privilege(%s,'runtime','USAGE')", (role,)
            ).fetchone() == (allowed,)
    with role_connection(jobs_database, "cs_app") as conn:
        assert (
            enqueue_notification_sync(
                conn,
                kind="rfp.done",
                subject_id=subject,
                audience=[{"user": users[0]}],
                actor_id=None,
                dedupe_key=f"rfp.done:{subject}",
            )
            is False
        )
    with role_connection(jobs_database, "platform_app") as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO notify.user_notifications(intent_id,user_id) VALUES (%s,%s)",
                (intent, users[0]),
            )
    with role_connection(jobs_database, "platform_app") as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO notify.user_notifications(intent_id,user_id) VALUES (%s,%s)",
                (uuid4(), users[0]),
            )


@pytest.mark.parametrize("table", ["notification_intents", "user_notifications"])
def test_dual_schema_tables_fail_explicitly_without_dropping_either_history(jobs_database, table):
    sql = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("\\")
    )
    sentinel = uuid4()
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        assert conn.execute("SELECT to_regclass(%s)", (f"runtime.{table}",)).fetchone() == (None,)
        count_before = conn.execute(
            SQL("SELECT count(*) FROM notify.{}").format(Identifier(table))
        ).fetchone()[0]
        conn.execute(SQL("CREATE TABLE runtime.{}(id uuid PRIMARY KEY)").format(Identifier(table)))
        try:
            conn.execute(
                SQL("INSERT INTO runtime.{}(id) VALUES (%s)").format(Identifier(table)), (sentinel,)
            )
            with pytest.raises(psycopg.errors.RaiseException, match="NOTIFICATION_SCHEMA_CONFLICT"):
                conn.execute(sql)
            conn.execute("ROLLBACK")
            assert conn.execute(
                SQL("SELECT id FROM runtime.{}").format(Identifier(table))
            ).fetchall() == [(sentinel,)]
            assert conn.execute(
                SQL("SELECT count(*) FROM notify.{}").format(Identifier(table))
            ).fetchone() == (count_before,)
        finally:
            conn.execute("ROLLBACK")
            # Only this synthetic table, created by this test after asserting it was absent.
            conn.execute(SQL("DROP TABLE runtime.{}").format(Identifier(table)))


@pytest.mark.parametrize("role", ["cs_app", "cs_worker", "tft_app", "tft_worker", "platform_app"])
def test_every_producer_role_expands_on_insert_without_identity_privileges(
    jobs_database, users, role
):
    subject = uuid4()
    data = dict(
        kind="cs.reply",
        subject_id=subject,
        audience=[{"user": users[1]}],
        actor_id=None,
        dedupe_key=f"cs.reply:{subject}",
    )
    with role_connection(jobs_database, role) as conn:
        assert enqueue_notification_sync(conn, **data)
    with role_connection(jobs_database, "platform_app") as conn:
        intent, expanded, recipient = conn.execute(
            "SELECT i.id,i.expanded_at,n.user_id FROM notify.notification_intents i JOIN notify.user_notifications n ON n.intent_id=i.id WHERE i.dedupe_key=%s",
            (data["dedupe_key"],),
        ).fetchone()
        assert expanded is not None and recipient == users[1]
        conn.execute(
            "UPDATE notify.user_notifications SET read_at=now() WHERE intent_id=%s", (intent,)
        )
        read_at = conn.execute(
            "SELECT read_at FROM notify.user_notifications WHERE intent_id=%s", (intent,)
        ).fetchone()[0]
    with role_connection(jobs_database, role) as conn:
        assert not enqueue_notification_sync(conn, **data)
    with psycopg.connect(jobs_database) as conn:
        assert (
            conn.execute(
                "SELECT expanded_at FROM notify.notification_intents WHERE id=%s", (intent,)
            ).fetchone()[0]
            == expanded
        )
        assert conn.execute(
            "SELECT user_id,read_at FROM notify.user_notifications WHERE intent_id=%s", (intent,)
        ).fetchall() == [(users[1], read_at)]
    with role_connection(jobs_database, role) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT notify.expand_notification_intent(%s)", (intent,))
    with psycopg.connect(jobs_database) as conn:
        assert conn.execute(
            "SELECT has_function_privilege(%s,'notify.expand_new_notification()','EXECUTE')",
            (role,),
        ).fetchone() == (False,)
        assert conn.execute(
            "SELECT prosecdef,proconfig,pg_get_userbyid(proowner) FROM pg_proc "
            "WHERE oid='notify.expand_new_notification()'::regprocedure"
        ).fetchone() == (True, ["search_path=pg_catalog"], "platform_migrator")
    if role != "platform_app":
        with role_connection(jobs_database, role) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT id FROM identity.users")


def test_creation_migration_expands_legacy_pending_and_reapplication_preserves_read(
    jobs_database, users
):
    subject = uuid4()
    data = dict(
        kind="rfp.done",
        subject_id=subject,
        audience=[{"user": users[1]}],
        actor_id=None,
        dedupe_key=f"rfp.done:{subject}",
    )
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "ALTER TABLE notify.notification_intents DISABLE TRIGGER notification_expand_on_insert"
        )
        enqueue_notification_sync(conn, **data)
        conn.execute(
            "ALTER TABLE notify.notification_intents ENABLE TRIGGER notification_expand_on_insert"
        )
    migration = "\n".join(
        line for line in EXPANSION.read_text().splitlines() if not line.startswith("\\")
    )
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        conn.execute(migration)
        intent = conn.execute(
            "SELECT id FROM notify.notification_intents WHERE dedupe_key=%s", (data["dedupe_key"],)
        ).fetchone()[0]
        conn.execute(
            "UPDATE notify.user_notifications SET read_at=now() WHERE intent_id=%s", (intent,)
        )
        before = conn.execute(
            "SELECT user_id,read_at FROM notify.user_notifications WHERE intent_id=%s", (intent,)
        ).fetchall()
        assert len(before) == 1 and before[0][0] == users[1]
        conn.execute(migration)
        assert (
            conn.execute(
                "SELECT user_id,read_at FROM notify.user_notifications WHERE intent_id=%s",
                (intent,),
            ).fetchall()
            == before
        )


def test_recipient_history_blocks_hard_delete_but_deactivation_preserves_it(jobs_database, users):
    subject = uuid4()
    data = dict(
        kind="rfp.done",
        subject_id=subject,
        audience=[{"user": users[1]}],
        actor_id=None,
        dedupe_key=f"rfp.done:{subject}",
    )
    with role_connection(jobs_database, "tft_worker") as conn:
        assert enqueue_notification_sync(conn, **data)
    with psycopg.connect(jobs_database) as conn:
        before = conn.execute(
            "SELECT intent_id,read_at FROM notify.user_notifications WHERE user_id=%s", (users[1],)
        ).fetchall()
        assert len(before) == 1
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as error:
            with conn.transaction():
                # 역할 FK는 savepoint 안에서만 제거해 수신 FK의 동작을 독립 검증한다.
                conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (users[1],))
                conn.execute("DELETE FROM identity.users WHERE id=%s", (users[1],))
        assert error.value.diag.constraint_name == "user_notifications_user_id_fkey"
        conn.execute("UPDATE identity.users SET is_active=false WHERE id=%s", (users[1],))
    later = uuid4()
    with role_connection(jobs_database, "tft_worker") as conn:
        assert enqueue_notification_sync(
            conn, **{**data, "subject_id": later, "dedupe_key": f"rfp.done:{later}"}
        )
        assert not enqueue_notification_sync(conn, **data)
    with psycopg.connect(jobs_database) as conn:
        assert (
            conn.execute(
                "SELECT intent_id,read_at FROM notify.user_notifications WHERE user_id=%s",
                (users[1],),
            ).fetchall()
            == before
        )
        assert conn.execute(
            "SELECT is_active FROM identity.users WHERE id=%s", (users[1],)
        ).fetchone() == (False,)
        assert conn.execute(
            "SELECT count(*) FROM notify.user_notifications n JOIN notify.notification_intents i ON i.id=n.intent_id WHERE i.dedupe_key=%s",
            (f"rfp.done:{later}",),
        ).fetchone() == (0,)
