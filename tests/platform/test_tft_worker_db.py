"""KAN-199 2/2 RFP worker를 전용 PostgreSQL·tft_worker 로그인·합성 처리 함수로 확인한다. 행은 합성이다."""

from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.agent.processor_registry import load_processor
from backend.agent.tft_worker import TftWorkerSettings, execute_job, parse_job_id
from backend.api.platform.jobs.events import EventPayload
from backend.shared.errors import ApplicationError

WORKER = TftWorkerSettings(_env_file=None, enabled=True, lease_seconds=60)
COUNTS = {"requirements": 4, "proposals": 2}


def login(settings, dsn, name, role):
    """그 역할만 상속하는 검사 로그인 설정.

    Args:
        settings: platform_app 설정.
        dsn: 관리자 DSN.
        name: 로그인 이름.
        role: 상속할 역할(None이면 없음).

    Returns:
        Settings: 로그인 설정.
    """
    with psycopg.connect(dsn) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
            conn.execute(f"CREATE ROLE {name} LOGIN PASSWORD 'test-only'")
            if role:
                conn.execute(f"GRANT {role} TO {name}")
    return settings.model_copy(
        update={"database_url": SecretStr(make_conninfo(dsn, user=name, password="test-only"))}
    )


@pytest.fixture
def worker_settings(settings, jobs_database):
    """tft_worker만 상속하는 검사 로그인.

    Args:
        settings: platform_app 설정.
        jobs_database: 관리자 DSN.

    Returns:
        Settings: worker 로그인 설정.
    """
    return login(settings, jobs_database, "test_tft_worker", "tft_worker")


def snapshot(dsn, job_id):
    """작업 상태·제어 행·이벤트.

    Args:
        dsn: 관리자 DSN.
        job_id: 작업 ID.

    Returns:
        tuple: (status, holder 비었는지, failure_code, [(type, payload)]).
    """
    with psycopg.connect(dsn) as conn:
        job = conn.execute(
            "SELECT j.status,c.holder_id IS NULL,c.failure_code FROM runtime.jobs j "
            "JOIN runtime.job_control c ON c.job_id=j.id WHERE j.id=%s",
            (job_id,),
        ).fetchone()
        events = conn.execute(
            "SELECT type,payload FROM runtime.job_events WHERE job_id=%s ORDER BY sequence",
            (job_id,),
        ).fetchall()
    return (*job, [(kind, payload or {}) for kind, payload in events])


def run(settings, job_id, processor):
    """합성 처리 함수로 worker를 실행한다.

    Args:
        settings: worker 로그인 설정.
        job_id: 작업 ID.
        processor: 처리 함수.

    Returns:
        str: 실행 결과.
    """
    return execute_job(settings, WORKER, job_id, processor_loader=lambda _settings: processor)


def test_worker_runs_the_processor_and_records_only_allowed_keys(
    worker_settings, jobs_database, registered_user, tft_job
):
    """단계마다 진행을 남기고 SUCCEEDED로 끝낸다. 처리 함수가 넘긴 모르는 키는 버린다."""
    seen = []
    document = str(uuid4())

    def processor(job, emit):
        seen.append(job)
        emit({"step": "parse", "document_version_id": document, "raw_text": "secret"})
        emit({"step": "compare", "counts": COUNTS, "error_code": "TFT_SECRET"})
        return {"counts": COUNTS, "needs_review": True, "prompt": "secret"}

    assert run(worker_settings, tft_job, processor) == "TFT_JOB_SUCCEEDED"
    job = seen[0]
    assert (job.job_id, job.kind, job.actor_id, job.input_version) == (
        tft_job,
        "rfp.process",
        registered_user[1],
        "a" * 64,
    )
    status, released, failure, events = snapshot(jobs_database, tft_job)
    assert (status, released, failure) == ("SUCCEEDED", True, None)
    assert events[1:] == [
        ("job.started", {"state": "RUNNING"}),
        ("job.progress", {"step": "parse", "document_version_id": document}),
        ("job.progress", {"step": "compare", "counts": COUNTS}),
        ("job.completed", {"state": "SUCCEEDED", "counts": COUNTS, "needs_review": True}),
    ]
    for _, payload in events:
        assert EventPayload.model_validate(payload).model_dump(exclude_none=True) == payload


@pytest.mark.parametrize(
    "bad",
    [
        {"step": "judge"},
        {"step": "parse", "counts": {"requirements": -1, "proposals": 0}},
        {"step": "parse", "counts": {"requirements": 1}},
        {"step": "parse", "needs_review": "yes"},
        {"step": "parse", "document_version_id": "not-a-uuid"},
    ],
)
def test_bad_processor_events_fail_the_job_with_a_fixed_code(
    worker_settings, jobs_database, tft_job, bad
):
    """계약에 없는 step·형식이 틀린 값은 저장하지 않고 FAILED·고정 코드다."""

    def processor(job, emit):
        emit(bad)

    with pytest.raises(ApplicationError) as error:
        run(worker_settings, tft_job, processor)
    assert error.value.code == "TFT_WORKER_PROCESSING_FAILED"
    status, released, failure, events = snapshot(jobs_database, tft_job)
    assert (status, released, failure) == ("FAILED", True, "TFT_WORKER_PROCESSING_FAILED")
    assert [kind for kind, _ in events] == ["job.queued", "job.started", "job.failed"]
    assert events[-1][1] == {"state": "FAILED"}


@pytest.mark.parametrize("code", ["TFT_TEAM_SPECIFIC_CODE", "not a code: with detail"])
def test_processor_error_codes_are_replaced_by_the_fixed_code(
    worker_settings, jobs_database, tft_job, code
):
    """처리 함수가 올린 ApplicationError 코드는 저장·종료 코드로 쓰지 않고 고정 코드로 바꾼다."""

    def processor(job, emit):
        raise ApplicationError(409, code, "synthetic detail")

    with pytest.raises(ApplicationError) as error:
        run(worker_settings, tft_job, processor)
    assert error.value.code == "TFT_WORKER_PROCESSING_FAILED"
    assert snapshot(jobs_database, tft_job)[:3] == ("FAILED", True, "TFT_WORKER_PROCESSING_FAILED")


def test_missing_processor_module_fails_with_its_code(worker_settings, jobs_database, tft_job):
    """처리 함수 모듈이 아직 없으면 FAILED·TFT_WORKER_PROCESSOR_MISSING이다."""
    with pytest.raises(ApplicationError) as error:
        execute_job(worker_settings, WORKER, tft_job, processor_loader=load_processor)
    assert error.value.code == "TFT_WORKER_PROCESSOR_MISSING"
    assert snapshot(jobs_database, tft_job)[:3] == ("FAILED", True, "TFT_WORKER_PROCESSOR_MISSING")


def test_cancel_request_stops_the_processor(worker_settings, jobs_database, tft_job):
    """취소 요청이 보이면 emit이 처리 함수를 멈추고 CANCELLED로 끝낸다."""
    steps = []

    def processor(job, emit):
        with psycopg.connect(jobs_database) as conn:
            conn.execute(
                "UPDATE runtime.job_control SET cancel_requested_at=now() WHERE job_id=%s",
                (job.job_id,),
            )
        emit({"step": "parse"})
        steps.append("after-parse")

    assert run(worker_settings, tft_job, processor) == "TFT_JOB_CANCELLED"
    assert steps == []
    status, released, _, events = snapshot(jobs_database, tft_job)
    assert (status, released) == ("CANCELLED", True)
    assert events[-1] == ("job.completed", {"state": "CANCELLED"})


def test_a_worker_that_lost_its_lease_writes_nothing(worker_settings, jobs_database, tft_job):
    """처리 중 다른 소유자가 가져가면 진행·종료를 쓰지 않고 멈춘다."""

    def processor(job, emit):
        with psycopg.connect(jobs_database) as conn:
            conn.execute(
                "UPDATE runtime.job_control SET holder_id=gen_random_uuid() WHERE job_id=%s",
                (job.job_id,),
            )
        emit({"step": "parse"})

    assert run(worker_settings, tft_job, processor) == "LEASE_LOST"
    status, released, failure, events = snapshot(jobs_database, tft_job)
    assert (status, released, failure) == ("RUNNING", False, None)
    assert [kind for kind, _ in events] == ["job.queued", "job.started"]


def test_only_the_tft_worker_role_may_run(settings, jobs_database, tft_job):
    """cs_worker·역할 없는 로그인·platform_app으로는 돌지 않고 작업도 건드리지 않는다."""
    for login_settings in (
        login(settings, jobs_database, "test_cs_worker", "cs_worker"),
        login(settings, jobs_database, "test_plain_login", None),
        settings,
    ):
        with pytest.raises(ApplicationError) as error:
            run(login_settings, tft_job, lambda job, emit: None)
        assert error.value.code == "WORKER_ROLE_REQUIRED"
    assert snapshot(jobs_database, tft_job)[0] == "QUEUED"


def test_tft_worker_does_not_claim_other_owners_jobs(worker_settings, jobs_database, cs_job):
    """C/S 작업 ID를 받아도 인수하지 않는다."""
    assert run(worker_settings, cs_job, lambda job, emit: None) == "NOT_CLAIMED"
    assert snapshot(jobs_database, cs_job)[0] == "QUEUED"


def test_tft_worker_role_has_only_runtime_column_privileges(jobs_database):
    """tft_worker는 제어·이벤트·상태 열만 쓰고 작업 생성·전송 장부·취소 열 권한이 없다."""
    with psycopg.connect(jobs_database) as conn:

        def has(privilege, table, column=None):
            if column is None:
                query, args = (
                    "SELECT has_table_privilege(%s,%s,%s)",
                    ("tft_worker", table, privilege),
                )
            else:
                query = "SELECT has_column_privilege(%s,%s,%s,%s)"
                args = ("tft_worker", table, column, privilege)
            return conn.execute(query, args).fetchone()[0]

        assert has("UPDATE", "runtime.job_control", "lease_expires_at")
        assert has("UPDATE", "runtime.jobs", "status")
        assert has("INSERT", "runtime.job_events", "payload")
        assert not has("INSERT", "runtime.jobs")
        assert not has("UPDATE", "runtime.jobs", "actor_id")
        assert not has("SELECT", "runtime.dispatch_outbox")
        assert not has("UPDATE", "runtime.job_control", "cancel_requested_at")


def test_worker_is_gated_and_needs_a_canonical_job_id(worker_settings):
    """기본 비활성·운영 환경이면 돌지 않고, 작업 ID는 표준 UUID만 받는다."""
    for worker, settings in (
        (TftWorkerSettings(_env_file=None), worker_settings),
        (WORKER, worker_settings.model_copy(update={"environment": "production"})),
    ):
        with pytest.raises(ApplicationError) as error:
            execute_job(settings, worker, uuid4())
        assert error.value.code == "TFT_WORKER_DISABLED"
    job_id = uuid4()
    assert parse_job_id(str(job_id)) == job_id
    with pytest.raises(ApplicationError):
        parse_job_id(str(job_id).upper())
