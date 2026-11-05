"""TFT 검사용 전용 PostgreSQL. 실제 tft 마이그레이션과 런타임 권한을 적용한다."""

import os
from pathlib import Path

import psycopg
import pytest
from psycopg.sql import SQL, Identifier

MIGRATIONS = sorted(Path("infra/migrations/tft").glob("*.sql"))


@pytest.fixture(scope="session")
def tft_database():
    """모든 tft 마이그레이션을 파일명 순서로 전용 검사 DB에 적용한다.

    Returns:
        str: 검사 DB 관리자 DSN. AUTH_TEST_DATABASE_URL이 없으면 검사를 건너뛴다.
    """
    dsn = os.getenv("AUTH_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("전용 PostgreSQL 검사는 AUTH_TEST_DATABASE_URL이 필요합니다.")
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.info.dbname == "kan77_auth_test", "전용 테스트 DB만 사용할 수 있습니다."
        for role in ("tft_migrator", "tft_app"):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute(SQL("CREATE ROLE {} NOLOGIN").format(Identifier(role)))
        conn.execute("ALTER ROLE tft_app LOGIN PASSWORD 'test-only'")
        conn.execute("CREATE SCHEMA IF NOT EXISTS tft AUTHORIZATION tft_migrator")
        conn.execute("GRANT USAGE ON SCHEMA tft TO tft_app")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE tft_migrator IN SCHEMA tft "
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO tft_app"
        )
        for migration in MIGRATIONS:
            conn.execute(
                "\n".join(
                    line for line in migration.read_text().splitlines() if not line.startswith("\\")
                )
            )
    return dsn
