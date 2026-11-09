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


async def close_pools():
    for pool in database._pools.values():
        await pool.close()
    database._pools.clear()


async def pid(config):
    async with database.connection(config) as conn:
        return (await (await conn.execute("SELECT pg_backend_pid() AS pid")).fetchone())["pid"]


def test_service_reuses_connections_and_keeps_rollback(dsn):
    async def run():
        pooled = settings(dsn, "team4-platform")
        async with database.connection(settings(dsn, None)) as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS kan248_rollback(x int)")
            await conn.execute("DELETE FROM kan248_rollback")
        try:
            # 25번 불러도 pool의 연결(최대 POOL_MAX)만 돈다. pool이 아니면 매번 새 연결이다.
            assert len({await pid(pooled) for _ in range(25)}) <= database.POOL_MAX
            with pytest.raises(RuntimeError):
                async with database.connection(pooled) as conn:
                    await conn.execute("INSERT INTO kan248_rollback VALUES (1)")
                    raise RuntimeError("boom")
            async with database.connection(pooled) as conn:
                rows = await (
                    await conn.execute("SELECT count(*) AS n FROM kan248_rollback")
                ).fetchone()
            assert rows["n"] == 0  # 실패한 블록의 INSERT는 rollback됐다
            plain = settings(dsn, None)
            assert await pid(plain) != await pid(plain)
        finally:
            await close_pools()
            async with database.connection(settings(dsn, None)) as conn:
                await conn.execute("DROP TABLE IF EXISTS kan248_rollback")

    asyncio.run(run())


def test_warm_fills_the_pool_before_traffic(dsn):
    """KAN-252: 시작 때 pool을 미리 채워 새 인스턴스의 첫 요청이 연결 생성을 기다리지 않는다."""

    async def run():
        try:
            await database.warm(settings(dsn, "team4-platform"))
            [pool] = database._pools.values()
            assert pool.get_stats()["pool_size"] == database.POOL_MAX
            await database.warm(settings(dsn, None))  # 서비스가 아니면 아무것도 하지 않는다
            assert len(database._pools) == 1
        finally:
            await close_pools()

    asyncio.run(run())
