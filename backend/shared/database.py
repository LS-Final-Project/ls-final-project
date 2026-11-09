"""Request-scoped connections. Applications never create tables or roles."""

import asyncio
import logging
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
# KAN-252: 새 인스턴스의 pool이 1개에서 한 개씩 늘어나는 1~2분 동안 요청이 연결을 기다리며 20초씩 밀렸다.
# 시작할 때 POOL_MAX까지 미리 채운다(서비스 4개 × 인스턴스 2 × 10 = 최대 80, Cloud SQL 한도 400).
WARM_TIMEOUT_SECONDS = 15.0
LOGGER = logging.getLogger(__name__)
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
                min_size=POOL_MAX,
                max_size=POOL_MAX,
                timeout=5,
                check=AsyncConnectionPool.check_connection,
                open=False,
            )
            await pool.open()
            _pools[dsn] = pool
        return _pools[dsn]


async def warm(settings: Settings) -> None:
    """Cloud Run 서비스 시작 때 pool을 열고 채워질 때까지 잠깐 기다린다. 실패해도 시작은 막지 않는다."""
    if not settings.cloud_run_service:
        return
    dsn = settings.database_url.get_secret_value() if settings.database_url else ""
    try:
        await (await _pool(dsn)).wait(timeout=WARM_TIMEOUT_SECONDS)
    except Exception as error:
        # DB가 늦으면 첫 요청이 기다린다(예전과 같다). 시작 실패로 인스턴스를 되풀이해 띄우지 않는다.
        LOGGER.warning("DATABASE_POOL_WARM_FAILED", extra={"error_type": type(error).__name__})


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
