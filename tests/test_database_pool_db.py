"""KAN-248: Cloud Run 서비스에서는 연결을 재사용하고, 블록마다 commit·rollback 경계는 그대로다."""

import asyncio
import os
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from backend.shared import database


@pytest.fixture
def dsn():
    value = os.getenv("AUTH_TEST_DATABASE_URL")
    if not value:
        pytest.skip("전용 PostgreSQL 검사는 AUTH_TEST_DATABASE_URL이 필요합니다.")
    return value


def settings(dsn, service):
    return SimpleNamespace(database_url=SecretStr(dsn), cloud_run_service=service)


async def pid_after(config, fail=False):
    async with database.connection(config) as conn:
        await conn.execute("CREATE TEMP TABLE IF NOT EXISTS kan248(x int)")
        if fail:
            await conn.execute("INSERT INTO kan248 VALUES (1)")
            raise RuntimeError("boom")
        row = await (
            await conn.execute("SELECT pg_backend_pid() AS pid, count(*) AS n FROM kan248")
        ).fetchone()
        return row["pid"], row["n"]


def test_service_reuses_connection_and_keeps_rollback(dsn):
    async def run():
        pooled = settings(dsn, "team4-platform")
        first, _ = await pid_after(pooled)
        with pytest.raises(RuntimeError):
            await pid_after(pooled, fail=True)
        second, rows = await pid_after(pooled)
        assert first == second  # 같은 연결을 다시 썼다
        assert rows == 0  # 실패한 블록의 INSERT는 rollback됐다
        plain = settings(dsn, None)
        assert (await pid_after(plain))[0] != (await pid_after(plain))[0]
        for pool in database._pools.values():
            await pool.close()
        database._pools.clear()

    asyncio.run(run())
