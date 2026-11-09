"""Request-scoped connections. Applications never create tables or roles."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from backend.shared.settings import Settings

# KAN-248: Cloud Run 서비스는 연결을 재사용한다. 호출마다 새로 열면 인증 계산으로 CPU가 찬다.
# worker job·로컬·시험은 K_SERVICE가 없어 지금처럼 호출마다 연결한다.
POOL_MAX = 10
_pools: dict[str, AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]]] = {}
_lock = asyncio.Lock()


async def _pool(dsn: str) -> AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]]:
    # ponytail: 프로세스당 이벤트 루프 하나(uvicorn)를 전제로 한 전역 pool.
    if dsn in _pools:
        return _pools[dsn]
    async with _lock:
        if dsn not in _pools:
            pool = AsyncConnectionPool(
                dsn,
                connection_class=psycopg.AsyncConnection[dict[str, Any]],
                kwargs={"row_factory": dict_row, "connect_timeout": 3},
                min_size=1,
                max_size=POOL_MAX,
                timeout=5,
                check=AsyncConnectionPool.check_connection,
                open=False,
            )
            await pool.open()
            _pools[dsn] = pool
        return _pools[dsn]


@asynccontextmanager
async def connection(settings: Settings) -> AsyncIterator[psycopg.AsyncConnection[dict[str, Any]]]:
    try:
        # Empty DSN lets libpq use PGHOST/PGUSER/PGPASSWORD injected by Cloud Run.
        dsn = settings.database_url.get_secret_value() if settings.database_url else ""
        if settings.cloud_run_service:
            # pool도 블록이 정상 종료하면 commit, 예외면 rollback한 뒤 연결을 돌려받는다.
            async with (await _pool(dsn)).connection() as conn:
                yield conn
            return
        async with await psycopg.AsyncConnection.connect(
            dsn, row_factory=dict_row, connect_timeout=3
        ) as conn:
            yield conn
    except psycopg.Error:
        raise HTTPException(503, "Database is unavailable") from None
