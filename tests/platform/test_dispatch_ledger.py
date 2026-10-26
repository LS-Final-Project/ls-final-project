"""전용 PostgreSQL로 전송 소유권·불명확 결과·실행 권한 분리를 검증한다."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException

from backend.api.platform.evaluations.admission import accept_run
from backend.api.platform.evaluations.smoke_worker import claim_run
from backend.api.platform.jobs.dispatch import (
    DispatchSettings,
    begin_send,
    claim_next,
    record_result,
)
from backend.shared.errors import ApplicationError


@pytest.fixture
def queued(settings, access, experiment):
    """합성 실행을 원자적으로 접수한다.

    Args:
        settings: 테스트 DB 설정.
        access: 합성 평가 허용 설정.
        experiment: 두 대상의 합성 실험.

    Returns:
        UUID: 접수한 실행 ID.
    """
    return asyncio.run(
        accept_run(settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4())
    ).data.run_id


@pytest.fixture
def dispatch():
    """검사에서만 전송 장부를 허용한다.

    Args:
        없음.

    Returns:
        DispatchSettings: 명시적 검사 설정.
    """
    return DispatchSettings(_env_file=None, enabled=True, max_attempts=2)


def saved(dsn, run_id):
    """전송 상태와 독립된 실행 상태를 함께 조회한다.

    Args:
        dsn: 전용 DB 관리자 연결.
        run_id: 합성 실행 ID.

    Returns:
        tuple: outbox 상태, 시도 번호, 실행 상태, 제어 판본, 이벤트 수.
    """
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT o.state,o.attempt_no,r.status,c.version,"
            "(SELECT count(*) FROM runtime.job_events e WHERE e.job_id=o.job_id) "
            "FROM evaluation.runs r JOIN runtime.dispatch_outbox o ON o.job_id=r.job_id "
            "JOIN evaluation.run_control c ON c.run_id=r.id WHERE r.id=%s",
            (run_id,),
        ).fetchone()


def expire(dsn, job_id):
    """대기 없이 전용 DB에서 전송 만료와 재시도 시각을 이동한다.

    Args:
        dsn: 전용 관리자 연결.
        job_id: 대상 작업.

    Returns:
        None: 실행 worker lease에는 손대지 않는다.
    """
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE runtime.dispatch_outbox SET "
            "lease_expires_at=CASE WHEN holder_id IS NOT NULL THEN now()-interval '1 second' END,"
            "next_attempt_at=now()-interval '1 second' WHERE job_id=%s",
            (job_id,),
        )


def test_race_and_acceptance_do_not_execute(
    settings, access, dispatch, queued, evaluation_database
):
    """두 dispatcher의 경합과 전송 성공이 실행 권한을 부여하지 않음을 확인한다.

    Args:
        settings: DB 설정.
        access: 평가 허용 설정.
        dispatch: 전송 설정.
        queued: 합성 실행.
        evaluation_database: 관리자 연결.

    Returns:
        None: 하나의 소유권과 변경 없는 실행·판본·이벤트를 검증한다.
    """

    async def race():
        """별개 연결로 동시에 확보한다.

        Args:
            없음.

        Returns:
            list: 두 확보 결과.
        """
        return await asyncio.gather(*(claim_next(settings, access, dispatch) for _ in range(2)))

    leases = [x for x in asyncio.run(race()) if x is not None]
    assert len(leases) == 1
    lease = leases[0]
    assert asyncio.run(begin_send(settings, access, dispatch, lease))
    asyncio.run(record_result(settings, access, dispatch, lease, "ACCEPTED"))
    assert saved(evaluation_database, queued) == ("ACCEPTED", 1, "QUEUED", 1, 1)
    assert asyncio.run(claim_next(settings, access, dispatch)) is None
    revoked = access.model_copy(update={"approved_config_hashes": frozenset()})
    with pytest.raises(ApplicationError):
        asyncio.run(claim_run(settings, revoked, queued))
    assert saved(evaluation_database, queued)[2:] == ("QUEUED", 1, 1)


@pytest.mark.parametrize("started", [False, True])
def test_expiry_fences_and_quarantines(
    settings, access, dispatch, queued, evaluation_database, started
):
    """전송 전 만료만 재확보하고 전송 후 만료는 UNKNOWN으로 보류한다.

    Args:
        settings: DB 설정.
        access: 평가 설정.
        dispatch: 전송 설정.
        queued: 합성 실행.
        evaluation_database: 관리자 연결.
        started: 전송 시작 여부.

    Returns:
        None: 이전 소유권의 시작·결과 쓰기가 거부된다.
    """
    old = asyncio.run(claim_next(settings, access, dispatch))
    if started:
        assert asyncio.run(begin_send(settings, access, dispatch, old))
    expire(evaluation_database, old.job_id)
    new = asyncio.run(claim_next(settings, access, dispatch))
    assert (new is None) == started
    assert saved(evaluation_database, queued)[:2] == (("UNKNOWN", 1) if started else ("CLAIMED", 2))
    for action in (
        begin_send(settings, access, dispatch, old),
        record_result(settings, access, dispatch, old, "ACCEPTED"),
    ):
        with pytest.raises(ApplicationError, match="STALE_DISPATCH"):
            asyncio.run(action)
    if started:
        assert asyncio.run(claim_next(settings, access, dispatch)) is None
    with psycopg.connect(evaluation_database) as conn:
        states = conn.execute(
            "SELECT state FROM runtime.dispatch_attempts WHERE job_id=%s ORDER BY attempt_no",
            (old.job_id,),
        ).fetchall()
    assert states == ([("UNKNOWN",)] if started else [("EXPIRED",), ("CLAIMED",)])


@pytest.mark.parametrize("result", ["REJECTED", "UNKNOWN"])
def test_result_retry_policy(settings, access, dispatch, queued, evaluation_database, result):
    """명확한 미접수만 지연·한도 안에서 재시도한다.

    Args:
        settings: DB 설정.
        access: 평가 설정.
        dispatch: 전송 설정.
        queued: 합성 실행.
        evaluation_database: 관리자 연결.
        result: 전송 결과.

    Returns:
        None: 불명확 결과 재전송 금지와 명확한 거부의 한도를 확인한다.
    """
    lease = asyncio.run(claim_next(settings, access, dispatch))
    assert asyncio.run(begin_send(settings, access, dispatch, lease))
    asyncio.run(record_result(settings, access, dispatch, lease, result))
    with pytest.raises(ApplicationError, match="STALE_DISPATCH"):
        asyncio.run(record_result(settings, access, dispatch, lease, "ACCEPTED"))
    assert asyncio.run(claim_next(settings, access, dispatch)) is None
    expire(evaluation_database, lease.job_id)
    lease = asyncio.run(claim_next(settings, access, dispatch))
    if result == "UNKNOWN":
        assert lease is None
        return
    assert lease.attempt_no == 2
    assert asyncio.run(begin_send(settings, access, dispatch, lease))
    asyncio.run(record_result(settings, access, dispatch, lease, "REJECTED"))
    expire(evaluation_database, lease.job_id)
    assert asyncio.run(claim_next(settings, access, dispatch)) is None
    assert saved(evaluation_database, queued) == ("EXHAUSTED", 2, "QUEUED", 1, 1)


@pytest.mark.parametrize(
    "reason", ["approval", "actor", "inactive", "terminal", "review", "worker", "version"]
)
def test_send_revalidates(settings, access, dispatch, queued, evaluation_database, reason):
    """확보 뒤 권한·실행 조건 변경을 전송 시작에서 차단한다.

    Args:
        settings: DB 설정.
        access: 평가 설정.
        dispatch: 전송 설정.
        queued: 합성 실행.
        evaluation_database: 관리자 연결.
        reason: 변경할 권한 또는 상태.

    Returns:
        None: BLOCKED로 기록하며 실행 상태를 추가 변경하지 않는다.
    """
    lease = asyncio.run(claim_next(settings, access, dispatch))
    if reason == "approval":
        access = access.model_copy(update={"approved_config_hashes": frozenset()})
    elif reason == "actor":
        access = access.model_copy(update={"evaluator_user_ids": frozenset()})
    elif reason == "worker":
        assert asyncio.run(claim_run(settings, access, queued))
    else:
        with psycopg.connect(evaluation_database) as conn:
            if reason == "inactive":
                conn.execute(
                    "UPDATE identity.users SET is_active=false WHERE id=(SELECT requested_by_user_id FROM evaluation.runs WHERE id=%s)",
                    (queued,),
                )
            elif reason == "terminal":
                conn.execute(
                    "UPDATE evaluation.runs SET status='CANCELLED',finished_at=now() WHERE id=%s",
                    (queued,),
                )
            elif reason == "review":
                conn.execute("UPDATE evaluation.runs SET needs_review=true WHERE id=%s", (queued,))
            else:
                conn.execute(
                    "UPDATE runtime.jobs SET input_version=%s WHERE id=%s", ("b" * 64, lease.job_id)
                )
    before = saved(evaluation_database, queued)[2:]
    assert not asyncio.run(begin_send(settings, access, dispatch, lease))
    assert saved(evaluation_database, queued) == ("BLOCKED", 1, *before)


def test_disabled_and_forged_lease(settings, access, dispatch, queued):
    """기본 비활성·운영 환경·다른 holder와 run의 소유권을 거부한다.

    Args:
        settings: DB 설정.
        access: 평가 설정.
        dispatch: 전송 설정.
        queued: 합성 실행.

    Returns:
        None: 외부 실행 없이 저장 계층 경계를 확인한다.
    """
    with pytest.raises(ApplicationError, match="DISPATCH_DISABLED"):
        asyncio.run(claim_next(settings, access, DispatchSettings(_env_file=None)))
    with pytest.raises(ApplicationError, match="DISPATCH_DISABLED"):
        asyncio.run(
            claim_next(settings.model_copy(update={"environment": "production"}), access, dispatch)
        )
    lease = asyncio.run(claim_next(settings, access, dispatch))
    for forged in (replace(lease, holder_id=uuid4()), replace(lease, run_id=uuid4())):
        with pytest.raises(ApplicationError, match="STALE_DISPATCH"):
            asyncio.run(begin_send(settings, access, dispatch, forged))


def test_failed_insert_rolls_back_claim(settings, access, dispatch, queued, evaluation_database):
    """시도 기록 실패 시 outbox 확보도 롤백된다.

    Args:
        settings: DB 설정.
        access: 평가 설정.
        dispatch: 전송 설정.
        queued: 합성 실행.
        evaluation_database: 관리자 연결.

    Returns:
        None: 부분 확보 없이 원래 PENDING을 유지한다.
    """
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("REVOKE INSERT ON runtime.dispatch_attempts FROM platform_app")
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(claim_next(settings, access, dispatch))
        assert error.value.status_code == 503
        assert saved(evaluation_database, queued) == ("PENDING", 0, "QUEUED", 1, 1)
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute("GRANT INSERT ON runtime.dispatch_attempts TO platform_app")


def test_result_failure_preserves_unknown_recovery(
    settings, access, dispatch, queued, evaluation_database
):
    """전송 결과 기록 실패 뒤 미접수로 오인하지 않고 만료 후 보류한다.

    Args:
        settings: DB 설정.
        access: 평가 설정.
        dispatch: 전송 설정.
        queued: 합성 실행.
        evaluation_database: 관리자 연결.

    Returns:
        None: SENDING commit과 결과 롤백, UNKNOWN 복구를 확인한다.
    """
    lease = asyncio.run(claim_next(settings, access, dispatch))
    assert asyncio.run(begin_send(settings, access, dispatch, lease))
    # 반환 시 연결·잠금이 해제되고 SENDING이 다른 연결에서 보여야 한다.
    with psycopg.connect(evaluation_database) as conn:
        assert conn.execute(
            "SELECT state FROM runtime.dispatch_outbox WHERE job_id=%s FOR UPDATE NOWAIT",
            (lease.job_id,),
        ).fetchone() == ("SENDING",)
        conn.execute(
            "REVOKE UPDATE (state, sent_at, finished_at) ON runtime.dispatch_attempts FROM platform_app"
        )
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(record_result(settings, access, dispatch, lease, "ACCEPTED"))
        assert error.value.status_code == 503
        assert saved(evaluation_database, queued) == ("SENDING", 1, "QUEUED", 1, 1)
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute(
                "GRANT UPDATE (state, sent_at, finished_at) ON runtime.dispatch_attempts TO platform_app"
            )
    expire(evaluation_database, lease.job_id)
    assert asyncio.run(claim_next(settings, access, dispatch)) is None
    assert saved(evaluation_database, queued) == ("UNKNOWN", 1, "QUEUED", 1, 1)


def test_ledger_role_privileges(evaluation_database):
    """다른 서비스와 앱 역할의 삭제·소유자 변조 권한이 없음을 확인한다.

    Args:
        evaluation_database: 전용 관리자 연결.

    Returns:
        None: 마이그레이션의 실제 권한을 검사한다.
    """
    with psycopg.connect(evaluation_database) as conn:
        for role in ("cs_app", "tft_app"):
            for table in ("runtime.dispatch_outbox", "runtime.dispatch_attempts"):
                assert not conn.execute(
                    "SELECT has_any_column_privilege(%s,%s,'SELECT,INSERT,UPDATE')", (role, table)
                ).fetchone()[0]
        for table in ("runtime.dispatch_outbox", "runtime.dispatch_attempts"):
            assert not conn.execute(
                "SELECT has_table_privilege('platform_app',%s,'DELETE,TRUNCATE')", (table,)
            ).fetchone()[0]
        for column in ("job_id", "attempt_no", "holder_id", "created_at"):
            assert not conn.execute(
                "SELECT has_column_privilege('platform_app','runtime.dispatch_attempts',%s,'UPDATE')",
                (column,),
            ).fetchone()[0]
