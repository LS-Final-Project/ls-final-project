"""Request-scoped connections. Applications never create tables or roles."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from backend.shared.settings import Settings


@asynccontextmanager
async def connection(settings: Settings) -> AsyncIterator[psycopg.AsyncConnection[dict[str, Any]]]:
    try:
        # Empty DSN lets libpq use PGHOST/PGUSER/PGPASSWORD injected by Cloud Run.
        dsn = settings.database_url.get_secret_value() if settings.database_url else ""
        async with await psycopg.AsyncConnection.connect(
            dsn, row_factory=dict_row, connect_timeout=3
        ) as conn:
            yield conn
    except psycopg.Error:
        raise HTTPException(503, "Database is unavailable") from None
