"""실제 PostgreSQL로 합성 실행 완료·중복 worker·중단 복구를 검증한다."""

import asyncio
from uuid import uuid4

import psycopg
import pytest

from backend.api.platform.evaluations.admission import accept_run, get_run
from backend.api.platform.evaluations.smoke_worker import advance_run, claim_run, dispatch_one
from backend.shared.errors import ApplicationError


@pytest.fixture
def queued(settings, access, experiment):
    """검사마다 합성 접수 하나를 commit한다.

    Args:
        settings: 테스트 DB 실행 계정.
        access: 합성 실행 허용 설정.
        experiment: 합성 실험 fixture.

    Returns:
        UUID: 접수된 run 식별자.
    """
    return asyncio.run(
        accept_run(settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4())
    ).data.run_id


def evidence(dsn, run_id):
    """시도 중복·실행 종료·이벤트 순서를 확인할 저장 증거를 조회한다.

    Args:
        dsn: 검사 DB 관리자 연결.
        run_id: 합성 실행 식별자.

    Returns:
        tuple: 실행 상태, 시도 수, 서로 다른 사례 수, 이벤트 목록.
    """
    with psycopg.connect(dsn) as conn:
        status = conn.execute(
            "SELECT status FROM evaluation.runs WHERE id=%s", (run_id,)
        ).fetchone()[0]
        counts = conn.execute(
            "SELECT count(*),count(DISTINCT a.case_id) FROM evaluation.case_attempts a "
            "JOIN evaluation.cases c ON c.id=a.case_id WHERE c.run_id=%s",
            (run_id,),
        ).fetchone()
        events = conn.execute(
            "SELECT e.sequence,e.type FROM runtime.job_events e JOIN evaluation.runs r "
            "ON r.job_id=e.job_id WHERE r.id=%s ORDER BY e.sequence",
            (run_id,),
        ).fetchall()
    return status, *counts, events


def expire(dsn, run_id):
    """시계 대기 없이 합성 테스트에서만 lease 만료를 주입한다.

    Args:
        dsn: 전용 검사 관리자 연결.
        run_id: 합성 실행 식별자.

    Returns:
        None: DB 만료 시각을 과거로 설정한다.
    """
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE evaluation.run_control SET lease_expires_at=now()-interval '1 second' "
            "WHERE run_id=%s",
            (run_id,),
        )


def test_dispatch_to_completion_and_status(
    settings, access, experiment, queued, evaluation_database
):
    """outbox→사례 실행→완료→조회 전 과정을 실제 DB로 확인한다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        experiment: 합성 실험.
        queued: 접수 실행 UUID.
        evaluation_database: 전용 검사 DB.

    Returns:
        None: 두 사례 완료·단일 시도·종료 재전송 무효를 확인한다.
    """
    assert asyncio.run(dispatch_one(settings, access)) == queued
    status = asyncio.run(get_run(settings, access, experiment["actor"], queued))
    assert status.status == "SUCCEEDED" and status.requested_count == 2
    saved = evidence(evaluation_database, queued)
    assert saved[:3] == ("SUCCEEDED", 2, 2)
    assert [x[0] for x in saved[3]] == list(range(1, len(saved[3]) + 1))
    assert saved[3][-1][1] == "job.completed"
    assert asyncio.run(dispatch_one(settings, access)) is None
    assert asyncio.run(claim_run(settings, access, queued)) is None
    assert evidence(evaluation_database, queued) == saved


def test_two_workers_only_one_owner(settings, access, queued, evaluation_database):
    """동시에 인수하려는 두 worker 중 하나만 generation을 얻는다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        queued: 접수 UUID.
        evaluation_database: 검사 DB.

    Returns:
        None: 활성 소유권 중복과 시도 중복이 없음을 확인한다.
    """

    async def race():
        """동일 run의 소유권 획득을 두 연결에서 시도한다.

        Args:
            없음.

        Returns:
            list: 소유권 결과 두 개.
        """
        return await asyncio.gather(
            claim_run(settings, access, queued), claim_run(settings, access, queued)
        )

    leases = asyncio.run(race())
    assert sum(x is not None for x in leases) == 1
    assert evidence(evaluation_database, queued)[:3] == ("RUNNING", 0, 0)


def test_resume_skips_completed_case_and_fences_old_worker(
    settings, access, queued, evaluation_database
):
    """첫 사례 저장 후 중단되면 남은 사례만 처리하고 이전 worker는 거절한다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        queued: 접수 UUID.
        evaluation_database: 검사 DB.

    Returns:
        None: 세대 증가·옛 쓰기 차단·사례당 시도 하나를 검증한다.
    """
    old = asyncio.run(claim_run(settings, access, queued))
    assert old is not None
    assert not asyncio.run(advance_run(settings, access, old))
    assert evidence(evaluation_database, queued)[:3] == ("RUNNING", 1, 1)
    expire(evaluation_database, queued)
    new = asyncio.run(claim_run(settings, access, queued))
    assert new is not None and new.generation == old.generation + 1
    with pytest.raises(ApplicationError) as stale:
        asyncio.run(advance_run(settings, access, old))
    assert stale.value.code == "STALE_WORKER"
    assert not asyncio.run(advance_run(settings, access, new))
    assert asyncio.run(advance_run(settings, access, new))
    assert evidence(evaluation_database, queued)[:3] == ("SUCCEEDED", 2, 2)


def test_crash_before_commit_and_finalization_resume(
    settings, access, queued, evaluation_database, monkeypatch
):
    """사례 처리 도중 예외와 최종 종료 직전 중단에서 부분 성공을 남기지 않는다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        queued: 접수 UUID.
        evaluation_database: 검사 DB.
        monkeypatch: 합성 provider 실패 주입.

    Returns:
        None: 실패 transaction 롤백과 이미 끝난 사례 재처리 없는 종료를 확인한다.
    """
    lease = asyncio.run(claim_run(settings, access, queued))
    assert lease is not None
    with monkeypatch.context() as patch:
        from unittest.mock import Mock

        patch.setattr(
            "eval.smoke.FakeProvider.generate",
            Mock(side_effect=RuntimeError("synthetic crash")),
        )
        with pytest.raises(RuntimeError):
            asyncio.run(advance_run(settings, access, lease))
    assert evidence(evaluation_database, queued)[:3] == ("RUNNING", 0, 0)
    expire(evaluation_database, queued)
    resumed = asyncio.run(claim_run(settings, access, queued))
    assert resumed is not None
    assert not asyncio.run(advance_run(settings, access, resumed))
    assert not asyncio.run(advance_run(settings, access, resumed))
    expire(evaluation_database, queued)
    assert asyncio.run(dispatch_one(settings, access)) == queued
    assert evidence(evaluation_database, queued)[:3] == ("SUCCEEDED", 2, 2)


def test_revocation_and_production_stop_execution(settings, access, queued, evaluation_database):
    """접수 후 권한 철회와 운영 환경 전환을 worker가 다시 검사한다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        queued: 접수 UUID.
        evaluation_database: 검사 DB.

    Returns:
        None: 외부 실행이나 사례 저장 없이 거절함을 확인한다.
    """
    with pytest.raises(ApplicationError) as production:
        asyncio.run(dispatch_one(settings.model_copy(update={"environment": "production"}), access))
    assert production.value.status_code == 503
    lease = asyncio.run(claim_run(settings, access, queued))
    assert lease is not None
    access.approved_config_hashes = frozenset()
    with pytest.raises(ApplicationError) as revoked:
        asyncio.run(advance_run(settings, access, lease))
    assert revoked.value.status_code == 403
    assert evidence(evaluation_database, queued)[:3] == ("RUNNING", 0, 0)


def test_case_write_failure_does_not_leave_attempt(settings, access, queued, evaluation_database):
    """합성 결과 시도 INSERT 뒤 사례 갱신이 실패하면 시도까지 롤백한다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        queued: 합성 접수 UUID.
        evaluation_database: 전용 DB.

    Returns:
        None: 부분 결과 부재와 인수 후 단일 결과 완료를 확인한다.
    """
    from fastapi import HTTPException

    lease = asyncio.run(claim_run(settings, access, queued))
    assert lease is not None
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("REVOKE UPDATE ON evaluation.cases FROM platform_app")
    try:
        with pytest.raises(HTTPException) as failure:
            asyncio.run(advance_run(settings, access, lease))
        assert failure.value.status_code == 503
        assert evidence(evaluation_database, queued)[:3] == ("RUNNING", 0, 0)
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute("GRANT UPDATE ON evaluation.cases TO platform_app")
    expire(evaluation_database, queued)
    assert asyncio.run(dispatch_one(settings, access)) == queued
    assert evidence(evaluation_database, queued)[:3] == ("SUCCEEDED", 2, 2)


def test_expired_worker_without_takeover_cannot_write(
    settings, access, queued, evaluation_database
):
    """새 worker가 아직 없어도 만료된 소유권의 결과 저장을 거부한다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        queued: 합성 실행 UUID.
        evaluation_database: 전용 DB.

    Returns:
        None: 만료 소유권 쓰기 거절과 사례 기록 부재를 확인한다.
    """
    lease = asyncio.run(claim_run(settings, access, queued))
    assert lease is not None
    expire(evaluation_database, queued)
    with pytest.raises(ApplicationError) as stale:
        asyncio.run(advance_run(settings, access, lease))
    assert stale.value.code == "STALE_WORKER"
    assert evidence(evaluation_database, queued)[:3] == ("RUNNING", 0, 0)


def test_process_exit_mid_case_is_recoverable(
    settings, access, experiment, queued, evaluation_database
):
    """별도 프로세스를 사례 transaction 도중 종료해 DB 롤백과 복구를 확인한다.

    Args:
        settings: 전용 실행 계정 설정.
        access: 합성 허용 설정.
        experiment: 합성 실험.
        queued: 접수 실행 UUID.
        evaluation_database: 전용 DB.

    Returns:
        None: 비정상 종료 뒤 남은 사례의 단일 저장과 완료를 검증한다.
    """
    import os
    import subprocess
    import sys

    code = """
import asyncio, os
from uuid import UUID
from backend.shared.settings import Settings
from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.smoke_worker import claim_run, advance_run
from eval.smoke import FakeProvider
settings = Settings(_env_file=None, environment="test", database_url=os.environ["SMOKE_TEST_DSN"])
access = EvaluationAccessSettings(evaluator_user_ids={UUID(os.environ["SMOKE_TEST_ACTOR"])},
    approved_config_hashes={os.environ["SMOKE_TEST_HASH"]}, local_admission_enabled=True)
lease = asyncio.run(claim_run(settings, access, UUID(os.environ["SMOKE_TEST_RUN"])))
assert lease is not None
FakeProvider.generate = lambda self, prompt: os._exit(17)
asyncio.run(advance_run(settings, access, lease))
"""
    env = {
        **os.environ,
        "SMOKE_TEST_DSN": settings.database_url.get_secret_value(),
        "SMOKE_TEST_ACTOR": str(experiment["actor"]),
        "SMOKE_TEST_HASH": experiment["config_hash"],
        "SMOKE_TEST_RUN": str(queued),
    }
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=15)
    assert result.returncode == 17, result.stderr.decode()
    assert evidence(evaluation_database, queued)[:3] == ("RUNNING", 0, 0)
    expire(evaluation_database, queued)
    assert asyncio.run(dispatch_one(settings, access)) == queued
    assert evidence(evaluation_database, queued)[:3] == ("SUCCEEDED", 2, 2)
