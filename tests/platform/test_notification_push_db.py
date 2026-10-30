"""P31 actual roles/locks/generations. Dedicated DB only; push wire is always fake."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier
from pydantic import SecretStr

from backend.api.platform.notifications import push_repository, push_worker
from backend.shared.database import connection
from backend.shared.settings import Settings
from tests.platform.test_notification_push import AUTH, ENDPOINT, PUBLIC
from tests.platform.test_notification_push_send import config

MIGRATION = Path("infra/migrations/platform/20261102_KAN-201_web_push.sql")


@pytest.fixture
def push_db(jobs_database):
    sql = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("\\")
    )
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        conn.execute(sql)
        conn.execute(sql)
    users = [uuid4(), uuid4()]
    intent = uuid4()
    with psycopg.connect(jobs_database) as conn:
        for user in users:
            conn.execute(
                "INSERT INTO identity.users(id,tid,oid,email,display_name,is_active) VALUES(%s,%s,%s,'push@example.invalid','Push',true)",
                (user, user, user),
            )
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(
            make_conninfo(jobs_database, user="platform_app", password="test-only")
        ),
    )
    try:
        yield jobs_database, settings, users, intent
    finally:
        with psycopg.connect(jobs_database) as conn:
            conn.execute("DELETE FROM notify.push_subscriptions WHERE user_id=ANY(%s)", (users,))
            conn.execute("DELETE FROM notify.user_notifications WHERE intent_id=%s", (intent,))
            conn.execute("DELETE FROM notify.notification_intents WHERE id=%s", (intent,))
            conn.execute("DELETE FROM identity.users WHERE id=ANY(%s)", (users,))


def seed(dsn, user, intent):
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO notify.notification_intents(id,owner,kind,title,link_path,audience,dedupe_key,expanded_at) VALUES(%s,'cs','cs.review_wait','C/S 답변 확인 대기',%s,jsonb_build_array(jsonb_build_object('role','cs_agent')),%s,now())",
            (intent, f"/cs/tickets/{uuid4()}", f"cs.review_wait:{intent}"),
        )
        conn.execute(
            "INSERT INTO notify.user_notifications(intent_id,user_id) VALUES(%s,%s)", (intent, user)
        )


def test_actual_role_scope_reapply_and_generation_ownership(push_db):
    dsn, settings, users, _ = push_db
    for role in ("cs_app", "tft_app", "cs_worker", "tft_worker"):
        with psycopg.connect(dsn) as conn:
            conn.execute(SQL("SET LOCAL ROLE {}").format(Identifier(role)))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM notify.push_subscriptions")
        with psycopg.connect(dsn) as conn:
            conn.execute(SQL("SET LOCAL ROLE {}").format(Identifier(role)))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO notify.push_deliveries(intent_id,subscription_id) VALUES(%s,%s)",
                    (uuid4(), uuid4()),
                )

    async def check():
        async with connection(settings) as conn:
            await push_repository.subscribe(conn, users[0], ENDPOINT, PUBLIC, AUTH)
        async with connection(settings) as conn:
            original = (
                await (
                    await conn.execute(
                        "SELECT * FROM notify.push_subscriptions WHERE endpoint=%s", (ENDPOINT,)
                    )
                ).fetchone()
            )["id"]
            await push_repository.unsubscribe(conn, users[1], ENDPOINT)
            await push_repository.subscribe(conn, users[0], ENDPOINT, PUBLIC, AUTH)
            assert (
                await (
                    await conn.execute(
                        "SELECT id FROM notify.push_subscriptions WHERE endpoint=%s", (ENDPOINT,)
                    )
                ).fetchone()
            )["id"] == original
        async with connection(settings) as conn:
            await push_repository.subscribe(conn, users[1], ENDPOINT, PUBLIC, AUTH)
            row = await (
                await conn.execute(
                    "SELECT * FROM notify.push_subscriptions WHERE endpoint=%s", (ENDPOINT,)
                )
            ).fetchone()
            assert row["id"] != original and row["user_id"] == users[1]

    asyncio.run(check())


def test_concurrent_claim_unique_and_sending_never_reclaimed(push_db):
    dsn, settings, users, intent = push_db

    async def register():
        async with connection(settings) as conn:
            await push_repository.subscribe(conn, users[0], ENDPOINT, PUBLIC, AUTH)

    asyncio.run(register())
    seed(dsn, users[0], intent)

    async def claims():
        async with connection(settings) as conn:
            await push_repository.prepare(conn)
            await push_repository.prepare(conn)

        async def claim():
            async with connection(settings) as conn:
                return await push_repository.claim(conn)

        results = await asyncio.gather(claim(), claim())
        assert sum(row is not None for row in results) == 1
        assert await claim() is None
        async with connection(settings) as conn:
            await conn.execute(
                "UPDATE notify.push_deliveries SET started_at=now()-interval '3 minutes' WHERE intent_id=%s",
                (intent,),
            )
            await push_repository.prepare(conn)
            row = await (
                await conn.execute(
                    "SELECT status,attempts FROM notify.push_deliveries WHERE intent_id=%s",
                    (intent,),
                )
            ).fetchone()
            assert row == {"status": "UNKNOWN", "attempts": 1}
        assert await claim() is None

    asyncio.run(claims())


def test_gone_old_generation_and_concurrent_owner_change_preserve_new_subscription(push_db):
    dsn, settings, users, intent = push_db

    async def register(user):
        async with connection(settings) as conn:
            await push_repository.subscribe(conn, user, ENDPOINT, PUBLIC, AUTH)

    asyncio.run(register(users[0]))
    seed(dsn, users[0], intent)
    started = Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        holder = {}

        def replace():
            started.set()
            asyncio.run(register(users[1]))

        def sender(row, push):
            # A separate transaction can observe committed SENDING before any fake send.
            with psycopg.connect(dsn) as conn:
                assert (
                    conn.execute(
                        "SELECT status FROM notify.push_deliveries WHERE intent_id=%s", (intent,)
                    ).fetchone()[0]
                    == "SENDING"
                )
            holder["future"] = executor.submit(replace)
            assert started.wait(2)
            with pytest.raises(TimeoutError):
                holder["future"].result(timeout=0.1)
            return "GONE", 0

        assert asyncio.run(push_worker.run(settings, config(), sender=sender)) == 1
        holder["future"].result(timeout=5)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT user_id FROM notify.push_subscriptions WHERE endpoint=%s", (ENDPOINT,)
        ).fetchone()
        assert row == {"user_id": users[1]}
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM notify.push_deliveries WHERE intent_id=%s", (intent,)
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.parametrize("case", ["past", "read", "inactive"])
def test_no_push_for_past_read_or_inactive(push_db, case):
    dsn, settings, users, intent = push_db
    if case == "past":
        seed(dsn, users[0], intent)

    async def register():
        async with connection(settings) as conn:
            await push_repository.subscribe(conn, users[0], ENDPOINT, PUBLIC, AUTH)

    asyncio.run(register())
    if case != "past":
        seed(dsn, users[0], intent)
    with psycopg.connect(dsn) as conn:
        if case == "read":
            conn.execute(
                "UPDATE notify.user_notifications SET read_at=now() WHERE intent_id=%s", (intent,)
            )
        if case == "inactive":
            conn.execute("UPDATE identity.users SET is_active=false WHERE id=%s", (users[0],))

    def sender(*args):
        pytest.fail("Ineligible push must not be sent")

    assert asyncio.run(push_worker.run(settings, config(), sender=sender)) == 0


def test_platform_app_lock_attempt_fake_send_and_reapply_preserve_generation(push_db):
    dsn, settings, users, intent = push_db

    async def register():
        async with connection(settings) as conn:
            role = await (await conn.execute("SELECT current_user AS role")).fetchone()
            assert role["role"] == "platform_app"
            await push_repository.subscribe(conn, users[0], ENDPOINT, PUBLIC, AUTH)
            return await (
                await conn.execute(
                    "SELECT * FROM notify.push_subscriptions WHERE endpoint=%s", (ENDPOINT,)
                )
            ).fetchone()

    original = asyncio.run(register())
    seed(dsn, users[0], intent)
    sends = []

    def sender(row, push):
        # Reaching the fake sender requires the actual platform_app connection to
        # successfully execute lock_attempt's FOR UPDATE OF s,d first.
        assert row["id"] == intent and row["endpoint"] == ENDPOINT
        sends.append(row)
        return "SENT", 0

    assert asyncio.run(push_worker.run(settings, config(), sender=sender)) == 1
    sql = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("\\")
    )
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(sql)
        conn.execute(sql)
        row = conn.execute(
            "SELECT * FROM notify.push_subscriptions WHERE id=%s", (original["id"],)
        ).fetchone()
        assert row == original
        delivery = conn.execute(
            "SELECT subscription_id,status,attempts FROM notify.push_deliveries WHERE intent_id=%s",
            (intent,),
        ).fetchone()
        assert delivery == {"subscription_id": original["id"], "status": "SENT", "attempts": 1}
    assert asyncio.run(push_worker.run(settings, config(), sender=sender)) == 0
    assert len(sends) == 1


def test_platform_app_update_is_only_nonbusiness_lock_marker_after_reapply(push_db):
    dsn, settings, users, _ = push_db
    sql = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("\\")
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        # An old column ACL must not survive table-level REVOKE on reapplication.
        conn.execute("GRANT UPDATE(auth,user_id) ON notify.push_subscriptions TO platform_app")
        conn.execute(sql)
        conn.execute(sql)

    async def check():
        async with connection(settings) as conn:
            await push_repository.subscribe(conn, users[0], ENDPOINT, PUBLIC, AUTH)
            assert await (
                await conn.execute(
                    "SELECT has_table_privilege(current_user,'notify.push_subscriptions','UPDATE') AS broad"
                )
            ).fetchone() == {"broad": False}
            cursor = await conn.execute(
                "UPDATE notify.push_subscriptions SET lock_marker=lock_marker WHERE endpoint=%s",
                (ENDPOINT,),
            )
            assert cursor.rowcount == 1
        for column in ("id", "user_id", "endpoint", "p256dh", "auth", "created_at"):
            async with connection(settings) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await conn.execute(
                        SQL("UPDATE notify.push_subscriptions SET {}={} WHERE endpoint=%s").format(
                            Identifier(column), Identifier(column)
                        ),
                        (ENDPOINT,),
                    )

    asyncio.run(check())
