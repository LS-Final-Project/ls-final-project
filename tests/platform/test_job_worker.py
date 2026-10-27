"""전용 PostgreSQL 로그인으로 지정 worker와 최소 권한을 검증한다."""

import asyncio
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.platform.evaluations.admission import accept_run, get_run
from backend.api.platform.evaluations.controls import control_run
from backend.api.platform.evaluations.job_worker import execute_run, parse_run_id
from backend.api.platform.evaluations.smoke_worker import advance_run, claim_run
from backend.shared.errors import ApplicationError


@pytest.fixture
def worker(settings, evaluation_database):
    """새 권한 역할만 상속하는 전용 검사 로그인을 만든다.

    Args:
        settings: 기존 플랫폼 설정.
        evaluation_database: 관리자 연결.

    Returns:
        Settings: 전용 worker 로그인 설정.
    """
    with psycopg.connect(evaluation_database) as conn:
        if not conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname='test_evaluation_worker'"
        ).fetchone():
            conn.execute("CREATE ROLE test_evaluation_worker LOGIN PASSWORD 'test-only'")
            conn.execute("GRANT evaluation_worker TO test_evaluation_worker")
    return settings.model_copy(
        update={
            "database_url": SecretStr(
                make_conninfo(
                    evaluation_database, user="test_evaluation_worker", password="test-only"
                )
            )
        }
    )


@pytest.fixture
def runs(settings, access, experiment):
    """같은 실험에 독립 실행 두 개를 접수한다.

    Args:
        settings: API DB 설정.
        access: 승인 설정.
        experiment: 합성 실험.

    Returns:
        list: 두 실행 UUID.
    """
    return [
        asyncio.run(
            accept_run(
                settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4()
            )
        ).data.run_id
        for _ in range(2)
    ]


def test_only_target_and_duplicate_noop(worker, settings, access, experiment, runs):
    """첫 큐 항목을 탐색하지 않고 두 번째 지정 실행만 완료한다.

    Args:
        worker: 전용 worker 설정.
        settings: API 설정.
        access: 승인 설정.
        experiment: 합성 실험.
        runs: 두 실행 ID.

    Returns:
        None: 지정 실행 완료·다른 실행 보존·중복 호출 무효를 확인한다.
    """
    assert asyncio.run(execute_run(worker, access, runs[1]))
    first = asyncio.run(get_run(settings, access, experiment["actor"], runs[0]))
    second = asyncio.run(get_run(settings, access, experiment["actor"], runs[1]))
    assert first.status == "QUEUED" and second.status == "SUCCEEDED"
    assert not asyncio.run(execute_run(worker, access, runs[1]))
    assert asyncio.run(get_run(settings, access, experiment["actor"], runs[1])) == second


def test_role_and_environment_guard(worker, settings, access, runs):
    """플랫폼 계정 대체 및 production 실행을 거부한다.

    Args:
        worker: 전용 설정.
        settings: API 설정.
        access: 승인 설정.
        runs: 실행 ID.

    Returns:
        None: 잘못된 신원/환경에서 인수하지 않는다.
    """
    with pytest.raises(ApplicationError, match="WORKER_ROLE_REQUIRED"):
        asyncio.run(execute_run(settings, access, runs[0]))
    with pytest.raises(ApplicationError, match="SMOKE_WORKER_DISABLED"):
        asyncio.run(
            execute_run(worker.model_copy(update={"environment": "production"}), access, runs[0])
        )
    with pytest.raises(ApplicationError, match="NOT_FOUND"):
        asyncio.run(execute_run(worker, access, uuid4()))


def test_revocation_parks_target(worker, settings, access, experiment, runs):
    """승인 철회는 지정 실행만 보류하고 사례를 실행하지 않는다.

    Args:
        worker: 전용 설정.
        settings: API 설정.
        access: 승인 설정.
        experiment: 합성 실험.
        runs: 두 실행 ID.

    Returns:
        None: 실패 전파와 제한된 보류 쓰기 권한을 검증한다.
    """
    revoked = access.model_copy(update={"approved_config_hashes": frozenset()})
    with pytest.raises(ApplicationError, match="EXPERIMENT_NOT_APPROVED"):
        asyncio.run(execute_run(worker, revoked, runs[1]))
    assert asyncio.run(get_run(settings, access, experiment["actor"], runs[1])).needs_review
    assert not asyncio.run(get_run(settings, access, experiment["actor"], runs[0])).needs_review


def test_cancellation_fences_worker(worker, settings, access, experiment, runs):
    """전용 역할로도 취소 후 오래된 worker 저장을 거부한다.

    Args:
        worker: 전용 설정.
        settings: API 설정.
        access: 승인 설정.
        experiment: 합성 실험.
        runs: 실행 ID.

    Returns:
        None: 기존 API와 worker 권한을 분리해 취소 경계를 검사한다.
    """
    lease = asyncio.run(claim_run(worker, access, runs[0]))
    state = asyncio.run(get_run(settings, access, experiment["actor"], runs[0]))
    asyncio.run(
        control_run(
            settings,
            access,
            experiment["actor"],
            runs[0],
            "cancel",
            str(uuid4()),
            f'"{state.version}"',
            uuid4(),
        )
    )
    with pytest.raises(ApplicationError, match="STALE_WORKER"):
        asyncio.run(advance_run(worker, access, lease))
    assert not asyncio.run(execute_run(worker, access, runs[0]))


def test_concurrent_workers(worker, access, runs):
    """동시 인수에서 하나의 실행 소유권만 발급한다.

    Args:
        worker: 전용 설정.
        access: 승인 설정.
        runs: 실행 ID.

    Returns:
        None: 별개 DB 연결의 인수 경합 결과를 확인한다.
    """

    async def race():
        """두 실행자의 동일 run 인수를 요청한다.

        Args:
            없음.

        Returns:
            list: 인수 결과.
        """
        return await asyncio.gather(
            claim_run(worker, access, runs[0]), claim_run(worker, access, runs[0])
        )

    assert sum(x is not None for x in asyncio.run(race())) == 1


@pytest.mark.parametrize("value", [None, "", "not-a-uuid", "12345678123456781234567812345678"])
def test_invalid_id_never_falls_back(value):
    """누락/잘못된 UUID를 거부한다.

    Args:
        value: 잘못된 환경 변수.

    Returns:
        None: 큐 탐색이나 DB 접근 없이 오류를 반환한다.
    """
    with pytest.raises(ApplicationError, match="INVALID_RUN_ID"):
        parse_run_id(value)


def test_forbidden_database_operations(worker, runs):
    """worker가 인증·접수·전송·원시 출력 조회 및 삭제 권한을 갖지 않음을 확인한다.

    Args:
        worker: 전용 설정.
        runs: 접수된 합성 실행.

    Returns:
        None: 실제 앱 로그인 SQL 거부를 검사한다.
    """
    statements = [
        "SELECT * FROM identity.sessions",
        "SELECT * FROM identity.user_roles",
        "SELECT * FROM runtime.dispatch_outbox",
        "SELECT * FROM runtime.dispatch_attempts",
        "SELECT raw_output FROM evaluation.case_attempts",
        "SELECT final_output FROM evaluation.cases",
        "UPDATE identity.users SET is_active=false",
        "UPDATE evaluation.experiments SET config_hash=config_hash",
        "UPDATE evaluation.run_control SET cancel_requested_at=now()",
        "INSERT INTO runtime.jobs DEFAULT VALUES",
        "INSERT INTO evaluation.runs DEFAULT VALUES",
        "DELETE FROM evaluation.cases",
        "TRUNCATE evaluation.case_attempts",
        "SET ROLE platform_app",
        "SET ROLE platform_migrator",
    ]
    with psycopg.connect(worker.database_url.get_secret_value(), autocommit=True) as conn:
        for statement in statements:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_partial_recovery_and_revocation(worker, access, runs, evaluation_database):
    """사례 저장 후 승인 철회·만료 복구에도 완료 사례를 중복 실행하지 않는다.

    Args:
        worker: 전용 설정.
        access: 승인 설정.
        runs: 실행 ID.
        evaluation_database: 검사 관리자 연결.

    Returns:
        None: 저장 경계 재검사와 사례별 복구를 확인한다.
    """
    lease = asyncio.run(claim_run(worker, access, runs[0]))
    assert not asyncio.run(advance_run(worker, access, lease))
    with pytest.raises(ApplicationError, match="EXPERIMENT_NOT_APPROVED"):
        asyncio.run(
            advance_run(
                worker, access.model_copy(update={"approved_config_hashes": frozenset()}), lease
            )
        )
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.run_control SET lease_expires_at=now()-interval '1 second' WHERE run_id=%s",
            (runs[0],),
        )
    assert asyncio.run(execute_run(worker, access, runs[0]))
    with psycopg.connect(evaluation_database) as conn:
        assert conn.execute(
            "SELECT count(*),count(DISTINCT a.case_id) FROM evaluation.case_attempts a JOIN evaluation.cases c ON c.id=a.case_id WHERE c.run_id=%s",
            (runs[0],),
        ).fetchone() == (2, 2)
    with pytest.raises(ApplicationError, match="STALE_WORKER"):
        asyncio.run(advance_run(worker, access, lease))


def test_cli_uses_exact_run(worker, settings, access, experiment, runs):
    """환경 변수로 시작한 실제 별도 프로세스가 지정 실행만 완료한다.

    Args:
        worker: 전용 로그인 설정.
        settings: API 설정.
        access: 승인 설정.
        experiment: 합성 실험.
        runs: 실행 ID.

    Returns:
        None: CLI 성공과 안전한 출력·입력 누락 실패를 확인한다.
    """
    import json
    import os
    import subprocess
    import sys

    env = os.environ | {
        "ENVIRONMENT": "test",
        "DATABASE_URL": worker.database_url.get_secret_value(),
        "EVALUATION_LOCAL_ADMISSION_ENABLED": "true",
        "EVALUATION_EVALUATOR_USER_IDS": json.dumps([str(experiment["actor"])]),
        "EVALUATION_APPROVED_CONFIG_HASHES": json.dumps(list(access.approved_config_hashes)),
        "EVALUATION_RUN_ID": str(runs[1]),
    }
    command = [sys.executable, "-m", "backend.api.platform.evaluations.job_worker"]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and result.stdout.strip() == "SYNTHETIC_COMPLETED"
    assert not result.stderr
    assert asyncio.run(get_run(settings, access, experiment["actor"], runs[0])).status == "QUEUED"
    env.pop("EVALUATION_RUN_ID")
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and result.stderr.strip() == "INVALID_RUN_ID"


@pytest.mark.parametrize("permission", ["app_role", "replication"])
def test_overprivileged_worker_login_is_rejected(
    worker, access, runs, evaluation_database, permission
):
    """앱 역할 또는 복제 권한을 함께 받은 로그인도 진입점에서 거부한다.

    Args:
        worker: 전용 설정.
        access: 승인 설정.
        runs: 실행 ID.
        evaluation_database: 검사 관리자 연결.
        permission: 추가로 부여할 금지 권한.

    Returns:
        None: 역할 혼합으로 최소 권한 검사를 우회하지 못한다.
    """
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "GRANT platform_app TO test_evaluation_worker"
            if permission == "app_role"
            else "ALTER ROLE test_evaluation_worker REPLICATION"
        )
    try:
        with pytest.raises(ApplicationError, match="WORKER_ROLE_REQUIRED"):
            asyncio.run(execute_run(worker, access, runs[0]))
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute(
                "REVOKE platform_app FROM test_evaluation_worker"
                if permission == "app_role"
                else "ALTER ROLE test_evaluation_worker NOREPLICATION"
            )


def test_inactive_actor_cannot_save(worker, access, runs, experiment, evaluation_database):
    """실행 인수 뒤 계정이 비활성화되면 사례 저장을 거부한다.

    Args:
        worker: 전용 설정.
        access: 승인 설정.
        runs: 실행 ID.
        experiment: 합성 실험.
        evaluation_database: 검사 관리자 연결.

    Returns:
        None: 전송 또는 이전 인수 성공이 현재 권한을 대체하지 않는다.
    """
    lease = asyncio.run(claim_run(worker, access, runs[0]))
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE identity.users SET is_active=false WHERE id=%s", (experiment["actor"],)
        )
    with pytest.raises(ApplicationError, match="SMOKE_ACCESS_DENIED"):
        asyncio.run(advance_run(worker, access, lease))
    with psycopg.connect(evaluation_database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM evaluation.case_attempts a JOIN evaluation.cases c ON c.id=a.case_id WHERE c.run_id=%s",
                (runs[0],),
            ).fetchone()[0]
            == 0
        )


def test_set_role_cannot_hide_login_privileges(worker, access, runs, evaluation_database):
    """관리자 로그인을 SET ROLE로 가린 연결을 거부한다.

    Args:
        worker: worker 설정 기반.
        access: 승인 설정.
        runs: 실행 ID.
        evaluation_database: 테스트 관리자 연결.

    Returns:
        None: 실효 역할만 확인해 관리자 로그인을 허용하지 않는다.
    """
    disguised = worker.model_copy(
        update={
            "database_url": SecretStr(
                make_conninfo(evaluation_database, options="-c role=evaluation_worker")
            )
        }
    )
    with pytest.raises(ApplicationError, match="WORKER_ROLE_REQUIRED"):
        asyncio.run(execute_run(disguised, access, runs[0]))
