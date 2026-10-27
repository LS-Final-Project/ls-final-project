"""Dedicated PostgreSQL for C/S tests that need the real migrations and runtime grants."""

import os
from pathlib import Path

import psycopg
import pytest
from psycopg.sql import SQL, Identifier

MIGRATIONS = sorted(Path("infra/migrations/cs").glob("*.sql"))


@pytest.fixture(scope="session")
def cs_database():
    """Applies every C/S migration, in file order, to the dedicated test DB.

    Returns:
        str: admin DSN of the test DB. Tests are skipped without AUTH_TEST_DATABASE_URL.
    """
    dsn = os.getenv("AUTH_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("전용 PostgreSQL 검사는 AUTH_TEST_DATABASE_URL이 필요합니다.")
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.info.dbname == "kan77_auth_test", "전용 테스트 DB만 사용할 수 있습니다."
        for role in ("cs_migrator", "cs_app"):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute(SQL("CREATE ROLE {} NOLOGIN").format(Identifier(role)))
        conn.execute("ALTER ROLE cs_app LOGIN PASSWORD 'test-only'")
        conn.execute("CREATE SCHEMA IF NOT EXISTS cs AUTHORIZATION cs_migrator")
        conn.execute("GRANT USAGE ON SCHEMA cs TO cs_app")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE cs_migrator IN SCHEMA cs "
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO cs_app"
        )
        for migration in MIGRATIONS:
            conn.execute(
                "\n".join(
                    line for line in migration.read_text().splitlines() if not line.startswith("\\")
                )
            )
    return dsn


@pytest.fixture
def admin(cs_database):
    with psycopg.connect(cs_database, autocommit=True) as conn:
        yield conn
