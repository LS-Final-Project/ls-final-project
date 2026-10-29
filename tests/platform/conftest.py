"""OAuth 인가 시도와 운영 세션에서 공유하는 전용 PostgreSQL 검사 준비."""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from pydantic import SecretStr

from backend.api.platform.auth.callback import MicrosoftSubject
from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import CONFIG_FIELDS, canonical_hash
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
            "20261023_KAN-78_manager_scopes.sql",
            "20261024_KAN-78_user_department_code.sql",
        ):
            migration = Path("infra/migrations/platform", filename).read_text()
            conn.execute(
                "\n".join(line for line in migration.splitlines() if not line.startswith("\\"))
            )
    return dsn


@pytest.fixture(scope="session")
def mailbox_database(auth_database):
    """보관 단독 검사와 실제 MSAL 연결 검사에 같은 전용 보관 스키마를 준비한다.

    Args:
        auth_database: kan77_auth_test만 허용하는 기존 관리자 검사 연결.

    Returns:
        str: 보관·재연결 상태 마이그레이션을 한 번씩 적용한 전용 검사 DB 연결.
    """
    with psycopg.connect(auth_database) as conn:
        for filename in (
            "20261027_KAN-81_mailbox_vault.sql",
            "20261028_KAN-81_mailbox_reconnect_state.sql",
        ):
            migration = Path("infra/migrations/platform", filename).read_text()
            conn.execute(
                "\n".join(line for line in migration.splitlines() if not line.startswith("\\"))
            )
    return auth_database


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


@pytest.fixture
def registered_user(auth_database):
    """실제 토큰이 없는 활성 등록 사용자를 준비하고 해당 검사 자료만 정리한다.

    Args:
        auth_database: 전용 테스트 DB의 관리자 연결 문자열.

    Returns:
        tuple[MicrosoftSubject, UUID]: 합성 Microsoft 식별자와 서비스 사용자 ID.
    """
    user_id, tid, oid = uuid4(), uuid4(), uuid4()
    with psycopg.connect(auth_database) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (user_id, tid, oid, "synthetic@example.invalid", "합성 세션 사용자", "TS_AUTO"),
        )
        conn.execute(
            "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'cs_agent')", (user_id,)
        )
    try:
        yield MicrosoftSubject(tid, oid), user_id
    finally:
        with psycopg.connect(auth_database) as conn:
            conn.execute("DELETE FROM identity.sessions WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.dev_sessions WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.manager_departments WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.manager_projects WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.users WHERE id=%s", (user_id,))


@pytest.fixture(scope="session")
def jobs_database(auth_database):
    """기존 전용 인증 검사 DB에 작업 접수 마이그레이션을 적용한다.

    Args:
        auth_database: 명시적으로 허용된 합성 검사 DB.

    Returns:
        str: 관리자 연결 문자열.
    """
    with psycopg.connect(auth_database, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('runtime.jobs')").fetchone()[0] is None:
            migration = Path(
                "infra/migrations/platform/20261025_KAN-69_job_admission.sql"
            ).read_text()
            conn.execute("\n".join(x for x in migration.splitlines() if not x.startswith("\\")))
        if conn.execute("SELECT to_regclass('runtime.dispatch_attempts')").fetchone()[0] is None:
            migration = Path(
                "infra/migrations/platform/20261026_KAN-69_dispatch_ledger.sql"
            ).read_text()
            conn.execute("\n".join(x for x in migration.splitlines() if not x.startswith("\\")))
        if not conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='runtime' "
            "AND table_name='dispatch_attempts' AND column_name='operation_name'"
        ).fetchone():
            migration = Path(
                "infra/migrations/platform/20261026_KAN-69_dispatch_operation.sql"
            ).read_text()
            conn.execute("\n".join(x for x in migration.splitlines() if not x.startswith("\\")))
        # 재실행해도 같은 상태가 되는 마이그레이션이라 매번 적용한다.
        migration = Path(
            "infra/migrations/platform/20261028_KAN-69_dispatch_recovery.sql"
        ).read_text()
        conn.execute("\n".join(x for x in migration.splitlines() if not x.startswith("\\")))
        # KAN-182 tft 작업 종류. 재실행해도 같은 상태가 된다.
        migration = Path("infra/migrations/platform/20261029_KAN-182_tft_jobs.sql").read_text()
        conn.execute("\n".join(x for x in migration.splitlines() if not x.startswith("\\")))
    return auth_database


@pytest.fixture(scope="session")
def evaluation_database(jobs_database):
    """전용 DB에 평가 저장과 접수 보완을 실제 권한으로 적용한다.

    Args:
        jobs_database: 작업 마이그레이션이 적용된 합성 DB.

    Returns:
        str: 전용 관리자 연결 문자열.
    """
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS evaluation AUTHORIZATION platform_migrator")
        conn.execute("GRANT USAGE ON SCHEMA evaluation TO platform_app")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE platform_migrator IN SCHEMA evaluation "
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO platform_app"
        )
        for table, filename in [
            ("experiments", "20261023_KAN-85_evaluation_storage.sql"),
            ("run_control", "20261025_KAN-90_run_admission.sql"),
        ]:
            if (
                conn.execute("SELECT to_regclass(%s)", ("evaluation." + table,)).fetchone()[0]
                is None
            ):
                sql = Path("infra/migrations/platform", filename).read_text()
                conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
        sql = Path("infra/migrations/platform/20261025_KAN-90_smoke_worker.sql").read_text()
        conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
        sql = Path("infra/migrations/platform/20261025_KAN-90_user_controls.sql").read_text()
        if (
            conn.execute("SELECT to_regclass('evaluation.run_action_receipts')").fetchone()[0]
            is None
        ):
            conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='evaluation_worker'").fetchone():
            sql = Path("infra/migrations/platform/20261027_KAN-90_worker_role.sql").read_text()
            conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
        sql = Path("infra/migrations/platform/20261027_KAN-90_worker_usage_KAN-86.sql").read_text()
        conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
        sql = Path("infra/migrations/platform/20261028_KAN-86_attempt_usage_raw.sql").read_text()
        conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
    return jobs_database


@pytest.fixture
def experiment(evaluation_database, registered_user):
    """원문·정답이 없는 합성 실험 두 대상을 등록하고 검사 뒤 정리한다.

    Args:
        evaluation_database: 전용 검사 DB.
        registered_user: 합성 등록 사용자.

    Returns:
        dict: 실험 UUID·구성과 현재 사용자 UUID.
    """
    actor = registered_user[1]
    value = dict.fromkeys(CONFIG_FIELDS)
    value.update(
        purpose="smoke",
        data_origin="synthetic",
        dataset_hash="a" * 64,
        model_id="fake",
        configuration={},
        target_set_hash=hashlib.sha256(b"synthetic-1\nsynthetic-2").hexdigest(),
    )
    value["config_hash"] = canonical_hash({k: value[k] for k in CONFIG_FIELDS})
    value.update(id=uuid4(), actor=actor)
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "INSERT INTO evaluation.experiments "
            "(id,name,purpose,data_origin,dataset_manifest,dataset_hash,model_id,configuration,"
            "target_set_hash,config_hash,created_by_user_id) "
            "VALUES (%s,'synthetic','smoke','synthetic','{}',%s,'fake','{}',%s,%s,%s)",
            (
                value["id"],
                value["dataset_hash"],
                value["target_set_hash"],
                value["config_hash"],
                actor,
            ),
        )
        for ref in ("synthetic-1", "synthetic-2"):
            conn.execute(
                "INSERT INTO evaluation.experiment_targets "
                "(experiment_id,target_ref,target_kind,data_origin) "
                "VALUES (%s,%s,'smoke','synthetic')",
                (value["id"], ref),
            )
    yield value
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "DELETE FROM evaluation.case_attempts WHERE case_id IN "
            "(SELECT id FROM evaluation.cases WHERE experiment_id=%s)",
            (value["id"],),
        )
        for table in ("run_action_receipts", "admission_receipts", "run_control", "cases"):
            conn.execute(
                psycopg.sql.SQL(
                    "DELETE FROM evaluation.{} WHERE run_id IN "
                    "(SELECT id FROM evaluation.runs WHERE experiment_id=%s)"
                ).format(psycopg.sql.Identifier(table)),
                (value["id"],),
            )
        conn.execute("DELETE FROM evaluation.runs WHERE experiment_id=%s", (value["id"],))
        for table in ("job_events", "dispatch_outbox"):
            conn.execute(
                psycopg.sql.SQL(
                    "DELETE FROM runtime.{} WHERE job_id IN "
                    "(SELECT id FROM runtime.jobs WHERE actor_id=%s)"
                ).format(psycopg.sql.Identifier(table)),
                (actor,),
            )
        conn.execute("DELETE FROM runtime.jobs WHERE actor_id=%s", (actor,))
        conn.execute(
            "DELETE FROM evaluation.experiment_targets WHERE experiment_id=%s", (value["id"],)
        )
        conn.execute("DELETE FROM evaluation.experiments WHERE id=%s", (value["id"],))


@pytest.fixture
def access(experiment):
    """합성 실험 하나만 명시적으로 허용한다.

    Args:
        experiment: 합성 실험.

    Returns:
        EvaluationAccessSettings: 테스트 환경 전용 허용 설정.
    """
    return EvaluationAccessSettings(
        evaluator_user_ids={experiment["actor"]},
        approved_config_hashes={experiment["config_hash"]},
        local_admission_enabled=True,
    )
