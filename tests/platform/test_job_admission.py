"""실제 PostgreSQL에서 작업 접수의 경합·롤백·서비스 권한을 검증한다."""

import asyncio
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.api.platform.jobs.admission import EvaluationJobRequest, admit_evaluation_job
from backend.shared.errors import ApplicationError


@pytest.fixture
def job_request(jobs_database, registered_user):
    """검사별 사용자·요청을 준비하고 해당 작업 자료만 정리한다.

    Args:
        jobs_database: 전용 검사 DB.
        registered_user: 기존 fixture의 합성 등록 사용자.

    Returns:
        EvaluationJobRequest: 모델·원문·시험 자료 없는 합성 요청.
    """
    request = EvaluationJobRequest(
        actor_id=registered_user[1],
        run_id=uuid4(),
        input_version="a" * 64,
        idempotency_key=str(uuid4()),
        request_hash="b" * 64,
    )
    yield request
    with psycopg.connect(jobs_database) as conn:
        for table in ("dispatch_outbox", "job_events"):
            conn.execute(
                psycopg.sql.SQL(
                    "DELETE FROM runtime.{} WHERE job_id IN "
                    "(SELECT id FROM runtime.jobs WHERE actor_id=%s)"
                ).format(psycopg.sql.Identifier(table)),
                (request.actor_id,),
            )
        conn.execute("DELETE FROM runtime.jobs WHERE actor_id=%s", (request.actor_id,))


async def connect_app(settings):
    """DDL 불가 실행 계정으로 실제 비동기 연결을 연다.

    Args:
        settings: 전용 플랫폼 실행 계정 설정.

    Returns:
        AsyncConnection: 자동 commit 모드 연결. 저장 시 명시적 transaction을 연다.
    """
    return await psycopg.AsyncConnection.connect(
        settings.database_url.get_secret_value(),
        autocommit=True,
        row_factory=dict_row,
    )


async def submit(settings, request):
    """호출자 소유 트랜잭션을 commit한 뒤 접수 결과를 돌려준다.

    Args:
        settings: 실행 계정 설정.
        request: 합성 작업 요청.

    Returns:
        JobAdmission: commit된 접수 결과.
    """
    async with await connect_app(settings) as conn:
        async with conn.transaction():
            return await admit_evaluation_job(conn, request)


def counts(dsn, actor):
    """검사 사용자에게 귀속된 세 종류 레코드 수를 읽는다.

    Args:
        dsn: 관리자 검사 연결.
        actor: 합성 사용자 UUID.

    Returns:
        tuple: 작업·최초 이벤트·outbox 건수.
    """
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT count(*), (SELECT count(*) FROM runtime.job_events e "
            "JOIN runtime.jobs j ON j.id=e.job_id WHERE j.actor_id=%s), "
            "(SELECT count(*) FROM runtime.dispatch_outbox o "
            "JOIN runtime.jobs j ON j.id=o.job_id WHERE j.actor_id=%s) "
            "FROM runtime.jobs WHERE actor_id=%s",
            (actor, actor, actor),
        ).fetchone()


def test_concurrent_same_key_has_one_job_and_replay(settings, jobs_database, job_request):
    """같은 요청의 동시 접수가 작업·이벤트·전송 의도를 중복 생성하지 않는다.

    Args:
        settings: 플랫폼 계정 설정.
        jobs_database: 검사 DB.
        job_request: 합성 요청.

    Returns:
        None: 신규 한 건과 재사용 한 건, 동일 식별자를 검증한다.
    """

    async def race():
        """두 연결에서 같은 요청을 동시에 제출한다.

        Args:
            없음.

        Returns:
            list: 두 접수 결과.
        """
        return await asyncio.gather(
            submit(settings, job_request),
            submit(settings, job_request.model_copy(update={"run_id": uuid4()})),
        )

    results = asyncio.run(race())
    assert sorted(x.created for x in results) == [False, True]
    assert results[0].job_id == results[1].job_id
    assert results[0].run_id == results[1].run_id
    assert counts(jobs_database, job_request.actor_id) == (1, 1, 1)


@pytest.mark.parametrize("field", ["request_hash", "input_version"])
def test_same_key_different_request_conflicts(settings, jobs_database, job_request, field):
    """동일 키의 다른 본문 또는 판본을 충돌로 거절한다.

    Args:
        settings: 플랫폼 계정 설정.
        jobs_database: 검사 DB.
        job_request: 합성 요청.
        field: 다르게 만들 hash 필드.

    Returns:
        None: 안전한 409와 기존 세 레코드 보존을 검증한다.
    """
    asyncio.run(submit(settings, job_request))
    with pytest.raises(ApplicationError) as failure:
        asyncio.run(submit(settings, job_request.model_copy(update={field: "c" * 64})))
    assert failure.value.status_code == 409
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"
    assert counts(jobs_database, job_request.actor_id) == (1, 1, 1)


def test_caller_failure_rolls_back_all_records(settings, jobs_database, job_request):
    """후속 업무 저장 실패 시 접수 함수가 먼저 commit한 흔적을 남기지 않는다.

    Args:
        settings: 플랫폼 계정 설정.
        jobs_database: 검사 DB.
        job_request: 합성 요청.

    Returns:
        None: 세 테이블 모두 롤백되고 같은 키 재접수가 가능한지 확인한다.
    """

    async def fail():
        """접수 뒤 후속 SQL 오류를 일으킨다.

        Args:
            없음.

        Returns:
            None: DB 오류가 호출자에게 전파된다.
        """
        async with await connect_app(settings) as conn:
            async with conn.transaction():
                await admit_evaluation_job(conn, job_request)
                await conn.execute("SELECT 1/0")

    with pytest.raises(psycopg.errors.DivisionByZero):
        asyncio.run(fail())
    assert counts(jobs_database, job_request.actor_id) == (0, 0, 0)
    assert asyncio.run(submit(settings, job_request)).created


def test_outbox_insert_failure_rolls_back_job_and_event(settings, jobs_database, job_request):
    """전송 의도 저장 실패를 주입해 앞선 작업·이벤트도 롤백되는지 검사한다.

    Args:
        settings: 플랫폼 계정 설정.
        jobs_database: 검사 DB.
        job_request: 합성 요청.

    Returns:
        None: 실패 후 부분 접수 기록이 없는지 확인한다.
    """
    with psycopg.connect(jobs_database) as conn:
        conn.execute("REVOKE INSERT ON runtime.dispatch_outbox FROM platform_app")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            asyncio.run(submit(settings, job_request))
        assert counts(jobs_database, job_request.actor_id) == (0, 0, 0)
    finally:
        with psycopg.connect(jobs_database) as conn:
            conn.execute("GRANT INSERT ON runtime.dispatch_outbox TO platform_app")


def test_no_implicit_transaction_or_other_service_access(settings, jobs_database, job_request):
    """트랜잭션 없는 저장과 다른 서비스의 접근 권한 확대를 막는다.

    Args:
        settings: 플랫폼 계정 설정.
        jobs_database: 검사 DB.
        job_request: 합성 요청.

    Returns:
        None: 명시적 트랜잭션 요구와 DB 권한 경계를 검증한다.
    """

    async def idle():
        """자동 commit 연결에서 접수를 시도한다.

        Args:
            없음.

        Returns:
            None: 트랜잭션 요구 오류가 발생한다.
        """
        async with await connect_app(settings) as conn:
            await admit_evaluation_job(conn, job_request)

    with pytest.raises(ValueError, match="active caller transaction"):
        asyncio.run(idle())
    with psycopg.connect(jobs_database) as conn:
        for role in ("cs_app", "tft_app"):
            assert not conn.execute(
                "SELECT has_schema_privilege(%s,'runtime','USAGE')",
                (role,),
            ).fetchone()[0]
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not conn.execute(
                "SELECT has_table_privilege('platform_app','runtime.jobs',%s)",
                (privilege,),
            ).fetchone()[0]
    assert counts(jobs_database, job_request.actor_id) == (0, 0, 0)


def test_waiting_request_takes_over_after_first_transaction_rolls_back(
    settings, jobs_database, job_request
):
    """먼저 키를 확보한 요청이 실패하면 대기 요청이 새 접수를 완성한다.

    Args:
        settings: 플랫폼 계정 설정.
        jobs_database: 검사 DB.
        job_request: 합성 요청.

    Returns:
        None: 실패한 작업 대신 한 개의 완전한 접수가 남는지 확인한다.
    """

    async def race_rollback():
        """첫 접수의 미commit 상태에서 두 번째 연결의 대기를 재현한다.

        Args:
            없음.

        Returns:
            tuple: 폐기된 작업 ID와 대기 후 성공한 접수 결과.
        """
        async with await connect_app(settings) as conn:
            try:
                async with conn.transaction():
                    first = await admit_evaluation_job(conn, job_request)
                    waiting = asyncio.create_task(submit(settings, job_request))
                    _, pending = await asyncio.wait({waiting}, timeout=0.1)
                    assert waiting in pending
                    raise RuntimeError("synthetic caller failure")
            except RuntimeError:
                pass
            return first.job_id, await asyncio.wait_for(waiting, timeout=5)

    abandoned_id, accepted = asyncio.run(race_rollback())
    assert accepted.created
    assert accepted.job_id != abandoned_id
    assert counts(jobs_database, job_request.actor_id) == (1, 1, 1)


def test_new_key_allows_intentional_repeat(settings, jobs_database, job_request):
    """입력 판본이 같아도 다른 키·run이면 의도한 별도 반복 실행으로 기록한다.

    Args:
        settings: 플랫폼 계정 설정.
        jobs_database: 검사 DB.
        job_request: 합성 요청.

    Returns:
        None: 서로 다른 두 접수가 각자의 이벤트·outbox를 갖는지 확인한다.
    """
    first = asyncio.run(submit(settings, job_request))
    second = asyncio.run(
        submit(
            settings,
            job_request.model_copy(
                update={
                    "run_id": uuid4(),
                    "idempotency_key": str(uuid4()),
                }
            ),
        )
    )
    assert first.created and second.created
    assert first.job_id != second.job_id
    assert counts(jobs_database, job_request.actor_id) == (2, 2, 2)
