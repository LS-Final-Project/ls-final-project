"""KAN-194 9차 3/3 C/S worker를 전용 PostgreSQL·cs_worker 로그인·가짜 공급자로 확인한다. 행은 합성이다."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.agent.cs_worker import CsWorkerSettings, execute_job, parse_job_id
from backend.agent.ticket_source_registry import WorkerParts, load_parts
from backend.api.platform.jobs.events import EventPayload
from backend.engine.cs.graph import build_graph, initial_state, thread_config
from backend.shared.errors import ApplicationError
from tests.cs.graph_fakes import (
    FILTERS,
    TICKET,
    AttemptStore,
    classification_provider,
    draft_provider,
    fts_searcher,
    verification_provider,
)

WORKER = CsWorkerSettings(_env_file=None, enabled=True, lease_seconds=60)


@pytest.fixture
def worker_settings(settings, jobs_database):
    """cs_worker만 상속하는 검사 로그인.

    Args:
        settings: platform_app 설정.
        jobs_database: 관리자 DSN.

    Returns:
        Settings: worker 로그인 설정.
    """
    with psycopg.connect(jobs_database) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='test_cs_worker'").fetchone():
            conn.execute("CREATE ROLE test_cs_worker LOGIN PASSWORD 'test-only'")
            conn.execute("GRANT cs_worker TO test_cs_worker")
    return settings.model_copy(
        update={
            "database_url": SecretStr(
                make_conninfo(jobs_database, user="test_cs_worker", password="test-only")
            )
        }
    )


class Harness:
    """가짜 공급자 그래프와 공유 체크포인터. 호출 수와 호출 중 부작용을 다룬다."""

    def __init__(self):
        """빈 호출 기록과 메모리 체크포인터.

        Returns:
            None.
        """
        self.saver = InMemorySaver()
        self.calls = []
        self.on_classify = None

    def classify(self, payload):
        """분류 호출을 세고 부작용을 실행한다.

        Args:
            payload: 분류 입력.

        Returns:
            Generation: 가짜 분류 결과.
        """
        self.calls.append("classify")
        if self.on_classify:
            self.on_classify()
        return classification_provider(payload)

    def draft(self, prompt):
        """초안 호출을 센다.

        Args:
            prompt: 초안 프롬프트.

        Returns:
            Generation: 가짜 초안.
        """
        self.calls.append("draft")
        return draft_provider(prompt)

    def graph(self):
        """같은 체크포인터를 쓰는 그래프.

        Returns:
            CompiledStateGraph: 그래프.
        """
        return build_graph(
            classification_provider=self.classify,
            draft_provider=self.draft,
            verification_provider=verification_provider,
            fts_searcher=fts_searcher,
            search_filters=FILTERS,
            allowed_info_codes=frozenset({"TEST_PHOTO"}),
            load_ticket=lambda ticket_id: TICKET,
            record_attempt=AttemptStore(),
            checkpointer=self.saver,
        )

    def run(self, settings, job_id, **overrides):
        """worker를 이 그래프로 실행한다.

        Args:
            settings: worker 로그인 설정.
            job_id: 작업 ID.
            **overrides: execute_job 인자 교체.

        Returns:
            str: 실행 결과.
        """

        @contextmanager
        def saver(_settings):
            yield self.saver

        values = {
            "parts_loader": lambda _settings: WorkerParts(ticket_source=SimpleNamespace()),
            "build": lambda _settings, _parts, _saver: self.graph(),
            "checkpointer": saver,
            **overrides,
        }
        return execute_job(settings, WORKER, job_id, **values)


def admin(dsn, sql, *args):
    """관리자 연결로 한 문장을 실행한다.

    Args:
        dsn: 관리자 DSN.
        sql: SQL.
        *args: 인자.

    Returns:
        None.
    """
    with psycopg.connect(dsn) as conn:
        conn.execute(sql, args)


def snapshot(dsn, job_id):
    """작업 상태·제어 행·이벤트.

    Args:
        dsn: 관리자 DSN.
        job_id: 작업 ID.

    Returns:
        tuple: (status, holder 비었는지, generation, failure_code, [(type, payload)]).
    """
    with psycopg.connect(dsn) as conn:
        job = conn.execute(
            "SELECT j.status,c.holder_id IS NULL,c.generation,c.failure_code FROM runtime.jobs j "
            "JOIN runtime.job_control c ON c.job_id=j.id WHERE j.id=%s",
            (job_id,),
        ).fetchone()
        events = conn.execute(
            "SELECT type,payload FROM runtime.job_events WHERE job_id=%s ORDER BY sequence",
            (job_id,),
        ).fetchall()
    return (*job, [(kind, payload or {}) for kind, payload in events])


def orphan(dsn, job_id):
    """앞 worker가 lease를 잃은 실행 중 상태로 만든다.

    Args:
        dsn: 관리자 DSN.
        job_id: 작업 ID.

    Returns:
        None.
    """
    admin(dsn, "UPDATE runtime.jobs SET status='RUNNING' WHERE id=%s", job_id)
    admin(
        dsn,
        "UPDATE runtime.job_control SET holder_id=gen_random_uuid(),"
        "lease_expires_at=now()-interval '1 second' WHERE job_id=%s",
        job_id,
    )


def test_worker_runs_to_human_review_and_records_allowed_events(
    worker_settings, jobs_database, cs_job
):
    """인수·노드별 진행·완료를 남기고 SUCCEEDED로 끝낸다. 모든 payload는 SSE 허용 키다."""
    harness = Harness()
    assert harness.run(worker_settings, cs_job) == "CS_JOB_SUCCEEDED"
    status, released, generation, failure, events = snapshot(jobs_database, cs_job)
    assert (status, released, generation, failure) == ("SUCCEEDED", True, 1, None)
    assert [kind for kind, _ in events] == ["job.queued", "job.started"] + ["job.progress"] * 5 + [
        "job.completed"
    ]
    assert [p["step"] for kind, p in events if kind == "job.progress"] == [
        "classify",
        "rules",
        "retrieve",
        "draft",
        "draft",  # verify도 기존 SSE draft 단계로 기록한다.
    ]
    completed = events[-1][1]
    assert completed["state"] == "SUCCEEDED"
    assert completed["stage"] == "DRAFTED"
    assert completed["classification_outcome"] == completed["draft_outcome"] == "VALID"
    for _, payload in events:
        assert EventPayload.model_validate(payload).model_dump(exclude_none=True) == payload
    assert harness.calls == ["classify", "draft"]


def test_rerun_of_a_paused_thread_makes_no_calls(worker_settings, jobs_database, cs_job):
    """앞 worker가 사람 확인까지 간 뒤 소유권을 잃었으면 다시 호출하지 않고 끝낸다."""
    harness = Harness()
    assert harness.run(worker_settings, cs_job) == "CS_JOB_SUCCEEDED"
    orphan(jobs_database, cs_job)
    calls = list(harness.calls)
    assert harness.run(worker_settings, cs_job) == "CS_JOB_SUCCEEDED"
    assert harness.calls == calls
    status, released, generation, _, events = snapshot(jobs_database, cs_job)
    assert (status, released, generation) == ("SUCCEEDED", True, 2)
    assert [kind for kind, _ in events][-2:] == ["job.started", "job.completed"]
    # 사람 확인을 지나치지 않고 그 직전에 그대로 멈춰 있다.
    assert tuple(harness.graph().get_state(thread_config(str(cs_job))).next) == ("human_review",)


def test_rerun_continues_from_the_last_checkpoint(worker_settings, jobs_database, cs_job):
    """분류·규칙까지 저장된 스레드는 검색부터 잇고 분류를 다시 부르지 않는다."""
    harness = Harness()
    graph = harness.graph()
    config = thread_config(str(cs_job))
    ticket = snapshot_ticket(jobs_database, cs_job)
    list(graph.stream(initial_state(ticket), config, interrupt_before=["retrieve"]))
    assert harness.calls == ["classify"]
    orphan(jobs_database, cs_job)
    assert harness.run(worker_settings, cs_job) == "CS_JOB_SUCCEEDED"
    assert harness.calls == ["classify", "draft"]
    *_, events = snapshot(jobs_database, cs_job)
    assert [p["step"] for kind, p in events if kind == "job.progress"] == [
        "retrieve",
        "draft",
        "draft",
    ]


def snapshot_ticket(dsn, job_id):
    """작업의 문의 ID.

    Args:
        dsn: 관리자 DSN.
        job_id: 작업 ID.

    Returns:
        str: subject_id.
    """
    with psycopg.connect(dsn) as conn:
        return str(
            conn.execute("SELECT subject_id FROM runtime.jobs WHERE id=%s", (job_id,)).fetchone()[0]
        )


def test_a_worker_that_lost_its_lease_writes_nothing(worker_settings, jobs_database, cs_job):
    """노드 중에 다른 소유자가 가져가면 진행·종료를 쓰지 않고 멈춘다."""
    harness = Harness()
    harness.on_classify = lambda: admin(
        jobs_database,
        "UPDATE runtime.job_control SET holder_id=gen_random_uuid() WHERE job_id=%s",
        cs_job,
    )
    assert harness.run(worker_settings, cs_job) == "LEASE_LOST"
    status, released, _, failure, events = snapshot(jobs_database, cs_job)
    assert (status, released, failure) == ("RUNNING", False, None)
    assert [kind for kind, _ in events] == ["job.queued", "job.started"]


def test_cancel_request_stops_before_the_next_node(worker_settings, jobs_database, cs_job):
    """취소 요청이 보이면 진행을 남기지 않고 CANCELLED로 끝낸다."""
    harness = Harness()
    harness.on_classify = lambda: admin(
        jobs_database,
        "UPDATE runtime.job_control SET cancel_requested_at=now() WHERE job_id=%s",
        cs_job,
    )
    assert harness.run(worker_settings, cs_job) == "CS_JOB_CANCELLED"
    status, released, _, failure, events = snapshot(jobs_database, cs_job)
    assert (status, released, failure) == ("CANCELLED", True, None)
    assert events[-1] == ("job.completed", {"state": "CANCELLED"})
    assert "job.progress" not in [kind for kind, _ in events]
    assert harness.calls == ["classify"]


def broken_build(*_args):
    """처리 중 예외(내부 문구는 저장하지 않아야 한다).

    Args:
        *_args: build 인자.

    Returns:
        없음. 항상 RuntimeError다.
    """
    raise RuntimeError("synthetic internal detail")


def missing_source(name):
    """C/S 문의 출처 모듈이 없는 이미지."""
    raise ModuleNotFoundError(f"No module named '{name}'", name=name)


@pytest.mark.parametrize(
    ("override", "code"),
    [
        # KAN-107에서 모듈이 생겼으므로 배포되지 않은 이미지를 import 함수로 흉내 낸다.
        ({"parts_loader": lambda s: load_parts(s, missing_source)}, "CS_WORKER_SOURCE_MISSING"),
        ({"build": broken_build}, "CS_WORKER_GRAPH_FAILED"),
    ],
    ids=["source-missing", "graph-error"],
)
def test_failures_are_recorded_with_a_fixed_code(
    worker_settings, jobs_database, cs_job, override, code
):
    """문의 출처가 없거나 처리 중 예외가 나면 FAILED와 고정 코드를 남기고 이벤트에는 코드가 없다."""
    with pytest.raises(ApplicationError) as error:
        Harness().run(worker_settings, cs_job, **override)
    assert error.value.code == code
    status, released, _, failure, events = snapshot(jobs_database, cs_job)
    assert (status, released, failure) == ("FAILED", True, code)
    assert events[-1] == ("job.failed", {"state": "FAILED"})


def test_ticket_source_error_codes_are_replaced_by_the_fixed_code(
    worker_settings, jobs_database, cs_job
):
    """문의 출처가 올린 ApplicationError 코드는 저장·종료 코드로 쓰지 않고 고정 코드로 바꾼다."""

    def source_failure(_settings):
        raise ApplicationError(400, "not a code: with detail", "synthetic detail")

    with pytest.raises(ApplicationError) as error:
        Harness().run(worker_settings, cs_job, parts_loader=source_failure)
    assert error.value.code == "CS_WORKER_GRAPH_FAILED"
    status, released, _, failure, _ = snapshot(jobs_database, cs_job)
    assert (status, released, failure) == ("FAILED", True, "CS_WORKER_GRAPH_FAILED")


def test_source_missing_code_is_trusted_only_while_loading(worker_settings, jobs_database, cs_job):
    """처리 구간(그래프 생성·실행)에서 올라온 CS_WORKER_SOURCE_MISSING은 GRAPH_FAILED로 바꾼다."""

    def build(*_args):
        raise ApplicationError(503, "CS_WORKER_SOURCE_MISSING", "synthetic")

    with pytest.raises(ApplicationError) as error:
        Harness().run(worker_settings, cs_job, build=build)
    assert error.value.code == "CS_WORKER_GRAPH_FAILED"
    status, released, _, failure, _ = snapshot(jobs_database, cs_job)
    assert (status, released, failure) == ("FAILED", True, "CS_WORKER_GRAPH_FAILED")


def test_only_the_cs_worker_role_may_run(settings, jobs_database, cs_job):
    """cs_worker 멤버가 아닌 로그인과 platform_app 로그인으로는 돌지 않고 작업도 건드리지 않는다."""
    with psycopg.connect(jobs_database) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='test_plain_login'").fetchone():
            conn.execute("CREATE ROLE test_plain_login LOGIN PASSWORD 'test-only'")
    plain = settings.model_copy(
        update={
            "database_url": SecretStr(
                make_conninfo(jobs_database, user="test_plain_login", password="test-only")
            )
        }
    )
    for login in (plain, settings):
        with pytest.raises(ApplicationError) as error:
            Harness().run(login, cs_job)
        assert error.value.code == "WORKER_ROLE_REQUIRED"
    assert snapshot(jobs_database, cs_job)[0] == "QUEUED"


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING"])
def test_cancel_requested_after_the_worker_died_ends_cancelled(
    worker_settings, jobs_database, cs_job, status
):
    """취소 요청이 있고 살아 있는 소유자가 없으면 인수하지 않고 CANCELLED로 확정한다(이연 P3, #278)."""
    orphan(jobs_database, cs_job)
    admin(jobs_database, "UPDATE runtime.jobs SET status=%s WHERE id=%s", status, cs_job)
    admin(
        jobs_database,
        "UPDATE runtime.job_control SET cancel_requested_at=now() WHERE job_id=%s",
        cs_job,
    )
    harness = Harness()
    assert harness.run(worker_settings, cs_job) == "CS_JOB_CANCELLED"
    assert harness.calls == []
    status_after, released, _, failure, events = snapshot(jobs_database, cs_job)
    assert (status_after, released, failure) == ("CANCELLED", True, None)
    assert events[-1] == ("job.completed", {"state": "CANCELLED"})
    assert "job.started" not in [kind for kind, _ in events]


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE runtime.jobs SET status='SUCCEEDED' WHERE id=%s",
        "UPDATE runtime.job_control SET holder_id=gen_random_uuid(),"
        "lease_expires_at=now()+interval '1 minute' WHERE job_id=%s",
        # 살아 있는 소유자가 있으면 취소 요청이 있어도 그 worker가 다음 노드 전에 끝낸다.
        "UPDATE runtime.job_control SET holder_id=gen_random_uuid(),cancel_requested_at=now(),"
        "lease_expires_at=now()+interval '1 minute' WHERE job_id=%s",
    ],
    ids=["finished", "live-owner", "cancel-with-live-owner"],
)
def test_jobs_that_must_not_start_are_not_claimed(worker_settings, jobs_database, cs_job, change):
    """취소 요청·종료·살아 있는 다른 소유자면 인수하지 않는다."""
    admin(jobs_database, change, cs_job)
    harness = Harness()
    assert harness.run(worker_settings, cs_job) == "NOT_CLAIMED"
    assert harness.calls == []


def test_worker_is_gated_and_needs_a_canonical_job_id(worker_settings):
    """설정이 꺼지면 어떤 환경에서도 돌지 않고, 작업 ID는 표준 UUID만 받는다."""
    production = worker_settings.model_copy(update={"environment": "production"})
    for settings in (worker_settings, production):
        with pytest.raises(ApplicationError) as error:
            execute_job(settings, CsWorkerSettings(_env_file=None), uuid4())
        assert error.value.code == "CS_WORKER_DISABLED"
    # 환경이 아니라 설정이 연다. 켜면 운영 환경에서도 역할 검사·인수까지 간다.
    assert execute_job(production, WORKER, uuid4()) == "NOT_CLAIMED"
    job_id = uuid4()
    assert parse_job_id(str(job_id)) == job_id
    for value in (None, "", str(job_id).upper(), str(job_id).replace("-", "")):
        with pytest.raises(ApplicationError):
            parse_job_id(value)
