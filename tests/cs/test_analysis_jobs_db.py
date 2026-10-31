"""KAN-107 AI 분석 작업 접수와 발송 뒤 재개를 전용 PostgreSQL로 확인한다(플랫폼은 가짜, 행은 합성).

플랫폼 쪽 접수 규칙(진행 중 409, 멱등)은 KAN-194 검사가 맡는다. 여기서는 cs가 접근·역할·상태를 먼저 검사하고,
플랫폼 답을 어떻게 옮기는지, 발송 접수 뒤 그래프를 사람 확인 다음으로 끝내는지를 본다.
"""

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.analysis import jobs as jobs_module
from backend.api.cs.analysis.jobs import CsAnalysisSettings, configure_analysis_jobs, resume_graph
from backend.api.cs.main import create_app
from backend.api.cs.workflow.mail import CustomerMailSettings, configure_customer_mail
from backend.engine.cs.graph import HUMAN_REVIEW, build_graph, initial_state, thread_config
from backend.shared.access import principal_header
from backend.shared.settings import Settings
from tests.cs.graph_fakes import (
    FILTERS,
    TICKET,
    AttemptStore,
    classification_provider,
    draft_provider,
    fts_searcher,
)
from tests.cs.test_customer_mail_db import PLATFORM, FakePlatform, last_send, send
from tests.cs.test_workflow_db import AGENT, CS_MANAGER, TRF_STAFF, act, code, ok
from tests.cs.test_workflow_db import ticket as ticket

KEY = {"Idempotency-Key": "analysis-key-0001"}


class FakeJobs:
    """플랫폼 내부 작업 접수. 같은 키는 같은 작업, 다른 키는 진행 중이면 409."""

    def __init__(self):
        self.bodies = []
        self.jobs = {}
        self.answer = None  # (status, code)로 다음 답을 바꾼다

    def handler(self, request):
        assert request.headers["authorization"] == "Bearer cs-token"
        assert request.url.path == "/internal/v1/platform/jobs/cs"
        body = json.loads(request.content)
        self.bodies.append(body)
        answer, self.answer = self.answer, None
        if answer is not None:
            status, error = answer
            return httpx.Response(status, json={"error": {"code": error, "message": "x"}})
        key = body["idempotency_key"]
        created = key not in self.jobs
        if created:
            if self.jobs:
                return httpx.Response(409, json={"error": {"code": "CS_JOB_IN_PROGRESS"}})
            self.jobs[key] = uuid4()
        job = self.jobs[key]
        return httpx.Response(
            201 if created else 200,
            json={
                "data": {
                    "job_id": str(job),
                    "ticket_id": body["ticket_id"],
                    "status": "QUEUED",
                    "status_url": f"/api/v1/platform/jobs/{job}",
                    "created": created,
                }
            },
        )


def settings_for(cs_database):
    return Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )


@pytest.fixture
def jobs():
    return FakeJobs()


@pytest.fixture
def resumed():
    return []


@pytest.fixture
def client(cs_database, jobs, resumed):
    app = create_app(settings_for(cs_database))
    platform = FakePlatform()
    app.state.fake_mail = platform
    configure_customer_mail(
        app,
        CustomerMailSettings(_env_file=None, enabled=True, platform_url=PLATFORM),
        token=lambda audience: "cs-token",
        transport=httpx.MockTransport(platform.handler),
    )

    async def resume(request, ticket_id):
        resumed.append(ticket_id)

    configure_analysis_jobs(
        app,
        CsAnalysisSettings(_env_file=None, enabled=True, platform_url=PLATFORM),
        token=lambda audience: "cs-token",
        transport=httpx.MockTransport(jobs.handler),
        resume=resume,
    )
    with TestClient(app, headers=principal_header(AGENT)) as test_client:
        yield test_client


def start(client, ticket_id, user=AGENT, headers=KEY):
    return client.post(
        f"/api/v1/cs/tickets/{ticket_id}/analysis",
        headers={**principal_header(user), **headers},
    )


def test_an_agent_starts_one_analysis_job_and_a_resend_returns_it(client, jobs, ticket):
    # Intake: the ticket has no workflow yet, and analysis is where it starts.
    first = start(client, ticket)
    assert first.status_code == 202
    job = first.json()["data"]
    assert (job["status"], job["created"]) == ("QUEUED", True)
    assert job["status_url"] == f"/api/v1/platform/jobs/{job['job_id']}"
    sent = jobs.bodies[-1]
    assert (sent["ticket_id"], sent["requested_by"]) == (str(ticket), str(AGENT.user_id))
    assert len(sent["input_version"]) == 64 and sent["idempotency_key"] == KEY["Idempotency-Key"]
    again = start(client, ticket).json()["data"]
    assert (again["job_id"], again["created"]) == (job["job_id"], False)
    # The platform keeps one job per ticket: another key while it runs is passed through.
    busy = start(client, ticket, headers={"Idempotency-Key": "analysis-key-0002"})
    assert code(busy) == (409, "CS_JOB_IN_PROGRESS")


def test_the_input_version_follows_the_ticket_text(client, admin, jobs, ticket):
    start(client, ticket)
    before = jobs.bodies[-1]["input_version"]
    admin.execute("UPDATE cs.tickets SET body = 'Oil leak got worse.' WHERE id=%s", (ticket,))
    jobs.jobs.clear()
    start(client, ticket, headers={"Idempotency-Key": "analysis-key-0003"})
    assert jobs.bodies[-1]["input_version"] != before


def test_scope_role_state_and_key_are_checked_before_the_platform(client, jobs, ticket):
    assert start(client, ticket, user=TRF_STAFF).status_code == 404
    assert start(client, ticket, user=CS_MANAGER).status_code == 403
    assert start(client, uuid4()).status_code == 404
    assert start(client, ticket, headers={}).status_code == 422
    assert start(client, ticket, headers={"Idempotency-Key": "short"}).status_code == 422
    assert jobs.bodies == []


def test_a_closed_ticket_is_not_analysed(client, admin, jobs, ticket):
    admin.execute(
        "INSERT INTO cs.ticket_workflows (ticket_id, state, version, closed_at, closed_by) "
        "VALUES (%s, 'CLOSED', 1, now(), %s)",
        (ticket, uuid4()),
    )
    assert code(start(client, ticket)) == (409, "CS_INVALID_TRANSITION")
    assert jobs.bodies == []


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ((503, "CS_PROCESSOR_NOT_CONNECTED"), (503, "CS_ANALYSIS_UNAVAILABLE")),
        ((409, "IDEMPOTENCY_CONFLICT"), (409, "IDEMPOTENCY_CONFLICT")),
        ((403, "FORBIDDEN"), (503, "CS_PLATFORM_UNAVAILABLE")),
    ],
)
def test_platform_answers_are_mapped(client, jobs, ticket, answer, expected):
    jobs.answer = answer
    assert code(start(client, ticket)) == expected


def test_disabled_analysis_jobs_answer_not_found(cs_database, ticket):
    app = create_app(settings_for(cs_database))
    configure_analysis_jobs(app, CsAnalysisSettings(_env_file=None, enabled=False))
    with TestClient(app, headers=principal_header(AGENT)) as plain:
        assert start(plain, ticket).status_code == 404


def test_an_accepted_send_resumes_the_graph_once(client, resumed, ticket):
    ok(act(client, ticket, "start-review", version=0))
    data = ok(send(client, ticket, 1))
    send_id = last_send(data)["id"]
    ok(act(client, ticket, f"sends/{send_id}/refresh"))
    assert resumed == []  # still queued at the platform
    client.app.state.fake_mail.settle("ACCEPTED")
    assert ok(act(client, ticket, f"sends/{send_id}/refresh"))["state"] == "ANSWERED"
    assert resumed == [ticket]
    # A later refresh of the same send does not resume again.
    ok(act(client, ticket, f"sends/{send_id}/refresh"))
    assert resumed == [ticket]


def test_resume_ends_a_graph_stopped_before_human_review_without_calling_the_model(cs_database):
    settings = settings_for(cs_database)
    job = uuid4()
    dsn = settings.database_url.get_secret_value() + " options='-c search_path=checkpoint_cs'"
    with PostgresSaver.from_conn_string(dsn) as saver:
        graph = build_graph(
            classification_provider=classification_provider,
            draft_provider=draft_provider,
            fts_searcher=fts_searcher,
            search_filters=FILTERS,
            allowed_info_codes=frozenset({"TEST_PHOTO"}),
            load_ticket=lambda ticket_id: TICKET,
            record_attempt=AttemptStore(),
            checkpointer=saver,
        )
        graph.invoke(initial_state("ticket-resume"), thread_config(str(job)))
        assert graph.get_state(thread_config(str(job))).next == (HUMAN_REVIEW,)
    assert resume_graph(settings, job) == "RESUMED"
    with PostgresSaver.from_conn_string(dsn) as saver:
        graph = build_graph(
            classification_provider=classification_provider,
            draft_provider=draft_provider,
            fts_searcher=fts_searcher,
            search_filters=FILTERS,
            allowed_info_codes=frozenset({"TEST_PHOTO"}),
            load_ticket=lambda ticket_id: TICKET,
            record_attempt=AttemptStore(),
            checkpointer=saver,
        )
        state = graph.get_state(thread_config(str(job)))
    assert state.next == () and state.values["stage"] == "HUMAN_REVIEW"
    # Already ended, or a thread that never existed: nothing to do.
    assert resume_graph(settings, job) == "NOTHING_TO_RESUME"
    assert resume_graph(settings, UUID(int=7)) == "NOTHING_TO_RESUME"


def stopped_graph(settings, job, ticket_id):
    """사람 확인 직전에 멈춘 체크포인트(cs_app 권한)."""
    dsn = settings.database_url.get_secret_value() + " options='-c search_path=checkpoint_cs'"
    with PostgresSaver.from_conn_string(dsn) as saver:
        graph = build_graph(
            classification_provider=classification_provider,
            draft_provider=draft_provider,
            fts_searcher=fts_searcher,
            search_filters=FILTERS,
            allowed_info_codes=frozenset({"TEST_PHOTO"}),
            load_ticket=lambda _ticket_id: TICKET,
            record_attempt=AttemptStore(),
            checkpointer=saver,
        )
        graph.invoke(initial_state(str(ticket_id)), thread_config(str(job)))
        return graph


def test_the_real_resume_finds_the_ticket_job_after_the_send_is_accepted(
    cs_database, admin, ticket
):
    settings = settings_for(cs_database)
    job = uuid4()
    stopped_graph(settings, job, ticket)
    admin.execute(
        "INSERT INTO cs.ticket_analyses (id, ticket_id, run_seq, job_id, outcome, input_version) "
        "VALUES (%s, %s, 1, %s, 'SCHEMA_VIOLATION', 'cs-classification-input-v4')",
        (uuid4(), ticket, job),
    )
    app = create_app(settings)
    platform = FakePlatform()
    configure_customer_mail(
        app,
        CustomerMailSettings(_env_file=None, enabled=True, platform_url=PLATFORM),
        token=lambda audience: "cs-token",
        transport=httpx.MockTransport(platform.handler),
    )
    configure_analysis_jobs(
        app,
        CsAnalysisSettings(_env_file=None, enabled=True, platform_url=PLATFORM),
        token=lambda audience: "cs-token",
        transport=httpx.MockTransport(FakeJobs().handler),
    )
    with TestClient(app, headers=principal_header(AGENT)) as client:
        ok(act(client, ticket, "start-review", version=0))
        send_id = last_send(ok(send(client, ticket, 1, kind="INFO_REQUEST")))["id"]
        platform.settle("ACCEPTED")
        assert ok(act(client, ticket, f"sends/{send_id}/refresh"))["state"] == "WAITING_CUSTOMER"
    dsn = settings.database_url.get_secret_value() + " options='-c search_path=checkpoint_cs'"
    with PostgresSaver.from_conn_string(dsn) as saver:
        graph = build_graph(
            classification_provider=classification_provider,
            draft_provider=draft_provider,
            fts_searcher=fts_searcher,
            search_filters=FILTERS,
            allowed_info_codes=frozenset({"TEST_PHOTO"}),
            load_ticket=lambda _ticket_id: TICKET,
            record_attempt=AttemptStore(),
            checkpointer=saver,
        )
        state = graph.get_state(thread_config(str(job)))
    assert state.next == () and state.values["stage"] == "HUMAN_REVIEW"
    # The graph ending does not touch the workflow beyond what the send did.
    assert admin.execute(
        "SELECT state FROM cs.ticket_workflows WHERE ticket_id=%s", (ticket,)
    ).fetchone() == ("WAITING_CUSTOMER",)


@pytest.mark.parametrize(
    ("error", "failures", "expected", "calls", "sleeps"),
    [
        (psycopg.OperationalError("checkpoint database restarting"), 1, "RESUMED", 2, [0.5]),
        (psycopg.errors.AdminShutdown("terminating connection"), 3, "RESUME_FAILED", 3, [0.5, 2.0]),
        (psycopg.errors.InsufficientPrivilege("permission denied"), 1, "RESUME_FAILED", 1, []),
        (ValueError("graph state broken"), 1, "RESUME_FAILED", 1, []),
    ],
    ids=["transient-then-ok", "transient-always", "privilege", "code-error"],
)
def test_only_transient_resume_failures_are_retried(
    cs_database, admin, ticket, monkeypatch, error, failures, expected, calls, sleeps
):
    """체크포인트 DB 일시 오류만 기다려 다시 시도하고, 권한·코드 오류는 기다리지 않고 바로 포기한다(#306 리뷰 P3).

    대기가 발송 갱신 응답 안에서 일어나므로, 다시 해도 같은 오류에 응답을 늦추지 않는다.
    """
    settings = settings_for(cs_database)
    job = uuid4()
    admin.execute(
        "INSERT INTO cs.ticket_analyses (id, ticket_id, run_seq, job_id, outcome, input_version) "
        "VALUES (%s, %s, 1, %s, 'SCHEMA_VIOLATION', 'cs-classification-input-v4')",
        (uuid4(), ticket, job),
    )
    seen = []
    waited = []

    def flaky(_settings, job_id):
        seen.append(job_id)
        if len(seen) <= failures:
            raise error
        return "RESUMED"

    async def fake_sleep(delay):
        waited.append(delay)

    monkeypatch.setattr(jobs_module, "resume_graph", flaky)
    monkeypatch.setattr(jobs_module.asyncio, "sleep", fake_sleep)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(analysis_jobs=object(), settings=settings))
    )
    assert asyncio.run(jobs_module.resume_after_send(request, ticket)) == expected
    assert seen == [job] * calls
    assert waited == sleeps
