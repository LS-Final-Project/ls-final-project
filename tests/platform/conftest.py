"""OAuth 인가 시도와 운영 세션에서 공유하는 전용 PostgreSQL 검사 준비."""

import os
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from pydantic import SecretStr

from backend.shared.settings import Settings


@pytest.fixture(scope="session")
def auth_database():
    """명시한 전용 테스트 DB에 실제 마이그레이션과 런타임 권한을 적용한다.

    Args:
        없음.

    Returns:
        str: 전용 DB의 관리자 연결 문자열. 환경 변수가 없으면 검사를 건너뛴다.
    """
    dsn = os.getenv("AUTH_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("전용 PostgreSQL 검사는 AUTH_TEST_DATABASE_URL이 필요합니다.")
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.info.dbname == "kan77_auth_test", "전용 테스트 DB만 사용할 수 있습니다."
        for role in ("platform_migrator", "platform_app", "cs_app", "tft_app"):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute(SQL("CREATE ROLE {} NOLOGIN").format(Identifier(role)))
        conn.execute("ALTER ROLE platform_app LOGIN PASSWORD 'test-only'")
        conn.execute("CREATE SCHEMA IF NOT EXISTS identity AUTHORIZATION platform_migrator")
        conn.execute("GRANT USAGE ON SCHEMA identity TO platform_app")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE platform_migrator IN SCHEMA identity "
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO platform_app"
        )
        for filename in (
            "20261022_KAN-78_identity.sql",
            "20261023_KAN-77_oauth_authorizations.sql",
            "20261023_KAN-77_sessions.sql",
        ):
            migration = Path("infra/migrations/platform", filename).read_text()
            conn.execute(
                "\n".join(line for line in migration.splitlines() if not line.startswith("\\"))
            )
    return dsn


@pytest.fixture
def settings(auth_database):
    """DDL 권한이 없는 플랫폼 실행 사용자의 연결을 준비한다.

    Args:
        auth_database: 전용 테스트 DB의 관리자 연결 문자열.

    Returns:
        Settings: platform_app 권한으로 연결하는 테스트 설정.
    """
    return Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(
            make_conninfo(auth_database, user="platform_app", password="test-only")
        ),
    )
