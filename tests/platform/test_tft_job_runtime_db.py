"""KAN-199 RFP 작업(owner=tft)의 전송 분기·상태·SSE 접근을 전용 PostgreSQL로 확인한다. 행은 합성이다."""

import asyncio
import json
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.jobs import sweep as sweep_module
from backend.api.platform.jobs.cloud_run import (
    CloudRunJobsSettings,
    DeliveryResult,
    TftWorkerJobSettings,
    dispatch_one,
)
from backend.api.platform.jobs.dispatch import (
    TFT_KINDS,
    DispatchSettings,
    begin_send,
    claim_next,
    require_local,
)
from backend.api.platform.jobs.events import EventPayload
from backend.api.platform.jobs.sweep import JobsSweepSettings, configure_jobs_sweep
from backend.api.platform.jobs.tft import TftJobRequest, TftJobSettings, admit_tft_job
from backend.api.platform.main import create_app
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from tests.platform.test_cs_job_runtime_db import add_user, cs_settings, outbox, session_client

CLOSED = EvaluationAccessSettings()
DISPATCH = DispatchSettings(_env_file=None, enabled=True)
JOBS = CloudRunJobsSettings(
    _env_file=None,
    enabled=True,
    project_number="123456789012",
    job="synthetic-worker",
    timeout_seconds=0.2,
)
WORKER = TftWorkerJobSettings(_env_file=None, enabled=True)


def tft_settings(connected=True):
    """RFP 접수·처리기 연결 설정.

    Args:
        connected: 처리기 연결 여부.

    Returns:
        TftJobSettings: 설정.
    """
    return TftJobSettings(
        _env_file=None,
        enabled=True,
        service_url="https://platform.example.run.app",
        invoker_email="tft-runtime@proj-test.iam.gserviceaccount.com",
        processor_connected=connected,
    )


@pytest.fixture
def stranger(auth_database):
    """요청자가 아닌 sales·manager 사용자(역할로는 열리지 않음을 본다).

    Args:
        auth_database: 관리자 DSN.

    Returns:
        list[tuple]: (식별자, 사용자 ID) 두 개.
    """
    made = [add_user(auth_database, ["sales"]), add_user(auth_database, ["manager"])]
    yield made
    with psycopg.connect(auth_database) as conn:
        for _, user_id in made:
            conn.execute("DELETE FROM identity.sessions WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.users WHERE id=%s", (user_id,))


def test_rfp_rows_are_dispatched_only_when_the_tft_gate_is_open(settings, jobs_database, tft_job):
    """RFP 게이트가 닫히면 장부가 닫히고, 열리면 rfp 행을 그 종류로 확보한다."""
    for tft in (None, tft_settings(connected=False)):
        with pytest.raises(ApplicationError) as error:
            asyncio.run(claim_next(settings, CLOSED, DISPATCH, None, tft))
        assert error.value.code == "DISPATCH_DISABLED"
    lease = asyncio.run(claim_next(settings, CLOSED, DISPATCH, None, tft_settings()))
    assert (lease.job_id, lease.run_id, lease.kind) == (tft_job, tft_job, "rfp.process")
    assert outbox(jobs_database, tft_job) == "CLAIMED"


@pytest.mark.parametrize(
    "off",
    ["tft-none", "tft-disabled", "not-connected", "dispatch", "jobs", "worker"],
)
def test_production_stays_closed_while_any_setting_is_off(settings, jobs_database, tft_job, off):
    """운영 환경도 설정 하나만 꺼지면 전송하지 않고 행은 PENDING이다."""
    production = settings.model_copy(update={"environment": "production"})
    tft = tft_settings(connected=off != "not-connected")
    posts = []

    def handler(request):
        posts.append(request)
        return httpx.Response(200, json={})

    async def token():
        return SecretStr("synthetic-access-token")

    if off == "tft-none":
        tft = None
    if off == "tft-disabled":
        tft = tft_settings().model_copy(update={"enabled": False})
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            dispatch_one(
                production,
                CLOSED,
                DISPATCH.model_copy(update={"enabled": off != "dispatch"}),
                JOBS.model_copy(update={"enabled": off != "jobs"}),
                token,
                transport=httpx.MockTransport(handler),
                tft=tft,
                tft_worker=WORKER.model_copy(update={"enabled": off != "worker"}),
            )
        )
    assert error.value.code in {"DISPATCH_DISABLED", "JOBS_TRANSPORT_DISABLED"}
    assert posts == [] and outbox(jobs_database, tft_job) == "PENDING"


def test_production_sends_rfp_only_and_keeps_evaluation_locked(settings, jobs_database, tft_job):
    """모든 설정을 켜면 운영에서 RFP를 보낸다. 평가는 허용을 켜도 종류에 없다."""
    production = settings.model_copy(update={"environment": "production"})
    open_access = EvaluationAccessSettings(_env_file=None, local_admission_enabled=True)
    assert require_local(production, open_access, DISPATCH, None, tft_settings()) == list(TFT_KINDS)
    with pytest.raises(ApplicationError) as error:
        require_local(production, open_access, DISPATCH)
    assert error.value.code == "DISPATCH_DISABLED"
    operation = f"projects/123456789012/locations/{JOBS.region}/operations/synthetic-op"

    async def token():
        return SecretStr("synthetic-access-token")

    result = asyncio.run(
        dispatch_one(
            production,
            CLOSED,
            DISPATCH,
            JOBS,
            token,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"name": operation})),
            tft=tft_settings(),
            tft_worker=WORKER,
        )
    )
    assert result == DeliveryResult("ACCEPTED", operation)
    assert outbox(jobs_database, tft_job) == "ACCEPTED"


def test_sweep_route_needs_the_invoker_and_sends_queued_jobs(
    settings, jobs_database, tft_job, monkeypatch
):
    """전송 경로: 꺼지면 404, 토큰이 없으면 401, 호출 계정이면 전송하고 게이트가 닫히면 0건이다."""
    operation = f"projects/123456789012/locations/{JOBS.region}/operations/synthetic-op"
    posts = []

    def handler(request):
        posts.append(request)
        return httpx.Response(200, json={"name": operation})

    class Caller:
        def verify(self, token):
            assert token == "scheduler-token"

    async def token():
        return "synthetic-access-token"

    monkeypatch.setenv("EVALUATION_DISPATCH_ENABLED", "true")
    monkeypatch.setenv("TFT_WORKER_JOBS_ENABLED", "true")
    url = "/internal/v1/platform/jobs/dispatch"
    closed = TestClient(create_app(settings, tft_job_settings=tft_settings()))
    assert closed.post(url).status_code == 404
    sweep = JobsSweepSettings(
        _env_file=None,
        enabled=True,
        service_url="https://platform.example.run.app",
        invoker_email="jobs-dispatch-invoker@proj-test.iam.gserviceaccount.com",
    )
    app = create_app(settings, tft_job_settings=tft_settings(), jobs_sweep_settings=None)
    configure_jobs_sweep(
        app,
        sweep,
        caller=Caller(),
        token=token,
        jobs=JOBS,
        transport=httpx.MockTransport(handler),
    )
    client = TestClient(app)
    assert client.post(url).status_code == 401
    bearer = {"Authorization": "Bearer scheduler-token"}
    done = client.post(url, headers=bearer)
    assert done.status_code == 200 and done.json()["data"]["sent"] == 1
    assert str(posts[0].url).endswith("/jobs/tft-worker:run")
    assert client.post(url, headers=bearer).json()["data"]["sent"] == 0
    monkeypatch.setenv("EVALUATION_DISPATCH_ENABLED", "false")
    assert client.post(url, headers=bearer).json()["data"]["sent"] == 0


def test_admission_sends_at_once_and_survives_a_failed_send(settings, monkeypatch):
    """접수 직후 전송은 켜졌을 때 한 번 부르고, 실패해도 접수를 망치지 않는다."""
    app = create_app(settings)
    calls = []

    async def failing(target, limit):
        calls.append(limit)
        raise RuntimeError("synthetic")

    monkeypatch.setattr(sweep_module, "sweep", failing)
    asyncio.run(sweep_module.dispatch_soon(app))
    assert calls == []
    app.state.jobs_sweep_caller = object()
    asyncio.run(sweep_module.dispatch_soon(app))
    assert calls == [1]


def sweep_app(settings, handler):
    """전송 트리거를 켠 앱(합성 Jobs API).

    Args:
        settings: DB 설정.
        handler: Jobs API 응답.

    Returns:
        FastAPI: 앱.
    """

    class Caller:
        def verify(self, token):
            assert token == "scheduler-token"

    async def token():
        return "synthetic-access-token"

    app = create_app(settings, tft_job_settings=tft_settings())
    configure_jobs_sweep(
        app,
        JobsSweepSettings(
            _env_file=None,
            enabled=True,
            service_url="https://platform.example.run.app",
            invoker_email="jobs-dispatch-invoker@proj-test.iam.gserviceaccount.com",
        ),
        caller=Caller(),
        token=token,
        jobs=JOBS,
        transport=httpx.MockTransport(handler),
    )
    return app


def run_sweep(app):
    response = TestClient(app).post(
        "/internal/v1/platform/jobs/dispatch", headers={"Authorization": "Bearer scheduler-token"}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def job_state(database, job_id):
    with psycopg.connect(database) as conn:
        return conn.execute(
            "SELECT j.status,c.failure_code,c.holder_id,"
            "(SELECT array_agg(type ORDER BY sequence) FROM runtime.job_events WHERE job_id=j.id) "
            "FROM runtime.jobs j JOIN runtime.job_control c ON c.job_id=j.id WHERE j.id=%s",
            (job_id,),
        ).fetchone()


def age_events(database, job_id):
    """작업 이벤트를 오래전 일로 만든다."""
    with psycopg.connect(database) as conn:
        conn.execute(
            "UPDATE runtime.job_events SET occurred_at=now()-interval '1 hour' WHERE job_id=%s",
            (job_id,),
        )


def test_unknown_rfp_send_is_retried_after_the_delay(settings, jobs_database, tft_job, monkeypatch):
    """결과 불명은 재시도 간격 전에는 보내지 않고, 지나면 같은 작업을 다시 보낸다."""
    monkeypatch.setenv("EVALUATION_DISPATCH_ENABLED", "true")
    monkeypatch.setenv("TFT_WORKER_JOBS_ENABLED", "true")
    operation = f"projects/123456789012/locations/{JOBS.region}/operations/synthetic-op"
    answers = [httpx.Response(503, json={}), httpx.Response(200, json={"name": operation})]
    posts = []

    def handler(request):
        posts.append(json.loads(request.content))
        return answers.pop(0)

    app = sweep_app(settings, handler)
    assert run_sweep(app)["sent"] == 1
    assert outbox(jobs_database, tft_job) == "UNKNOWN"
    assert run_sweep(app)["sent"] == 0 and len(posts) == 1
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "UPDATE runtime.dispatch_outbox SET next_attempt_at=now() WHERE job_id=%s", (tft_job,)
        )
    assert run_sweep(app)["sent"] == 1
    assert outbox(jobs_database, tft_job) == "ACCEPTED"
    assert posts[0] == posts[1]


def test_unknown_retries_stop_at_the_attempt_limit(settings, jobs_database, tft_job, monkeypatch):
    """결과 불명도 시도 한도를 넘지 않는다."""
    monkeypatch.setenv("EVALUATION_DISPATCH_ENABLED", "true")
    monkeypatch.setenv("TFT_WORKER_JOBS_ENABLED", "true")
    posts = []

    def handler(request):
        posts.append(request)
        return httpx.Response(503, json={})

    app = sweep_app(settings, handler)
    for _ in range(DISPATCH.max_attempts + 1):
        run_sweep(app)
        with psycopg.connect(jobs_database) as conn:
            conn.execute(
                "UPDATE runtime.dispatch_outbox SET next_attempt_at=now() WHERE job_id=%s",
                (tft_job,),
            )
    assert len(posts) == DISPATCH.max_attempts
    assert outbox(jobs_database, tft_job) == "EXHAUSTED"


def test_stale_job_is_closed_and_the_project_accepts_again(
    settings, jobs_database, tft_job, monkeypatch
):
    """전송이 소진돼 시작 못 한 작업은 실패로 닫히고, 그 프로젝트의 다음 접수가 열린다."""
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "UPDATE runtime.dispatch_outbox SET state='EXHAUSTED' WHERE job_id=%s", (tft_job,)
        )
        subject, actor = conn.execute(
            "SELECT subject_id,actor_id FROM runtime.jobs WHERE id=%s", (tft_job,)
        ).fetchone()
    app = sweep_app(settings, lambda _: httpx.Response(200, json={}))
    assert run_sweep(app)["reaped"] == 0
    assert job_state(jobs_database, tft_job)[0] == "QUEUED"
    age_events(jobs_database, tft_job)
    assert run_sweep(app)["reaped"] == 1
    assert job_state(jobs_database, tft_job) == (
        "FAILED",
        "JOB_NOT_STARTED",
        None,
        ["job.queued", "job.failed"],
    )
    assert run_sweep(app)["reaped"] == 0

    async def admit_again():
        async with connection(settings) as conn, conn.transaction():
            return await admit_tft_job(
                conn,
                TftJobRequest(
                    kind="rfp.process",
                    project_id=subject,
                    input_version="b" * 64,
                    actor_id=actor,
                    idempotency_key=uuid4().hex,
                    request_hash="c" * 64,
                ),
            )

    again = asyncio.run(admit_again())
    with psycopg.connect(jobs_database) as conn:
        for table in ("job_control", "dispatch_outbox", "job_events"):
            conn.execute(f"DELETE FROM runtime.{table} WHERE job_id=%s", (again.job_id,))
        conn.execute("DELETE FROM runtime.jobs WHERE id=%s", (again.job_id,))
    assert again.created is True


def test_reaper_leaves_live_workers_and_closed_owners_alone(
    settings, jobs_database, tft_job, monkeypatch
):
    """살아 있는 lease가 있거나 그 owner 게이트가 닫혔으면 오래돼도 닫지 않는다."""
    age_events(jobs_database, tft_job)
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "UPDATE runtime.job_control SET holder_id=gen_random_uuid(),"
            "lease_expires_at=now()+interval '1 minute' WHERE job_id=%s",
            (tft_job,),
        )
    app = sweep_app(settings, lambda _: httpx.Response(200, json={}))
    assert run_sweep(app)["reaped"] == 0
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "UPDATE runtime.job_control SET lease_expires_at=now()-interval '1 minute' "
            "WHERE job_id=%s",
            (tft_job,),
        )
    app.state.tft_jobs = tft_settings(connected=False)
    assert run_sweep(app)["reaped"] == 0
    assert job_state(jobs_database, tft_job)[0] == "QUEUED"
    app.state.tft_jobs = tft_settings()
    assert run_sweep(app)["reaped"] == 1
    assert job_state(jobs_database, tft_job)[2] is None


def test_cs_dispatch_leaves_rfp_rows_pending(settings, jobs_database, cs_job, tft_job):
    """C/S 게이트만 열면 C/S 행만 집고 RFP 행은 PENDING으로 둔다."""
    lease = asyncio.run(claim_next(settings, CLOSED, DISPATCH, cs_settings()))
    assert (lease.job_id, lease.kind) == (cs_job, "cs.analyze")
    assert asyncio.run(claim_next(settings, CLOSED, DISPATCH, cs_settings())) is None
    assert outbox(jobs_database, tft_job) == "PENDING"


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE runtime.job_control SET cancel_requested_at=now() WHERE job_id=%s",
        "UPDATE runtime.job_control SET holder_id=gen_random_uuid(),"
        "lease_expires_at=now()+interval '1 minute' WHERE job_id=%s",
        "UPDATE runtime.jobs SET status='FAILED' WHERE id=%s",
    ],
    ids=["cancel-requested", "live-worker-lease", "finished"],
)
def test_begin_send_blocks_rfp_jobs_that_must_not_start(settings, jobs_database, tft_job, change):
    """RFP도 C/S와 같은 job_control 규칙으로 막는다(취소 요청·살아 있는 lease·종료)."""
    lease = asyncio.run(claim_next(settings, CLOSED, DISPATCH, None, tft_settings()))
    with psycopg.connect(jobs_database) as conn:
        conn.execute(change, (tft_job,))
    assert asyncio.run(begin_send(settings, CLOSED, DISPATCH, lease, None, tft_settings())) is False
    assert outbox(jobs_database, tft_job) == "BLOCKED"


def test_rfp_job_is_sent_to_the_tft_worker_job_with_its_job_id(settings, jobs_database, tft_job):
    """RFP 작업은 tft worker Job에 TFT_JOB_ID만 넘기고, worker Job 설정이 없으면 장부가 닫힌다."""
    posts = []
    operation = f"projects/123456789012/locations/{JOBS.region}/operations/synthetic-op"

    def handler(request):
        posts.append(request)
        return httpx.Response(200, json={"name": operation})

    async def token():
        return SecretStr("synthetic-access-token")

    transport = httpx.MockTransport(handler)
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            dispatch_one(
                settings, CLOSED, DISPATCH, JOBS, token, transport=transport, tft=tft_settings()
            )
        )
    assert error.value.code == "DISPATCH_DISABLED"
    assert posts == [] and outbox(jobs_database, tft_job) == "PENDING"
    result = asyncio.run(
        dispatch_one(
            settings,
            CLOSED,
            DISPATCH,
            JOBS,
            token,
            transport=transport,
            tft=tft_settings(),
            tft_worker=WORKER,
        )
    )
    assert result == DeliveryResult("ACCEPTED", operation)
    assert str(posts[0].url).endswith("/jobs/tft-worker:run")
    assert json.loads(posts[0].content)["overrides"]["containerOverrides"] == [
        {"name": "worker", "env": [{"name": "TFT_JOB_ID", "value": str(tft_job)}]}
    ]
    assert outbox(jobs_database, tft_job) == "ACCEPTED"


def test_rfp_status_is_visible_to_the_requester_only(settings, registered_user, stranger, tft_job):
    """요청자만 RFP 상태·SSE를 보고, sales·manager라도 남의 작업은 404다(커서 오류보다 먼저)."""
    url = f"/api/v1/platform/jobs/{tft_job}"
    with session_client(settings, registered_user[0]) as requester:
        response = requester.get(url)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data == {
            "job_id": str(tft_job),
            "project_id": data["project_id"],
            "owner": "tft",
            "kind": "rfp.process",
            "status": "QUEUED",
            "version": 1,
            "last_event_sequence": 1,
        }
    for subject, _ in stranger:
        with session_client(settings, subject) as other:
            assert other.get(url).status_code == 404
            bad = {"Last-Event-ID": "not-a-cursor"}
            assert other.get(url + "/events", headers=bad).status_code == 404


def test_rfp_events_stream_only_allowed_keys(settings, jobs_database, registered_user, tft_job):
    """RFP 진행 이벤트는 step·문서 판본·counts·needs_review만 SSE로 나가고 모르는 키는 버린다."""
    payload = {
        "step": "compare",
        "document_version_id": str(uuid4()),
        "counts": {"requirements": 12, "proposals": 3},
        "needs_review": True,
        "error_code": "TFT_SECRET_CODE",
    }
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "INSERT INTO runtime.job_events(job_id,sequence,type,payload) "
            "VALUES (%s,2,'job.progress',%s)",
            (tft_job, json.dumps(payload)),
        )
        conn.execute("UPDATE runtime.jobs SET status='SUCCEEDED' WHERE id=%s", (tft_job,))
    with session_client(settings, registered_user[0]) as client:
        response = client.get(f"/api/v1/platform/jobs/{tft_job}/events?after=1")
    assert response.status_code == 200, response.text
    data = [line for line in response.text.splitlines() if line.startswith("data:")]
    assert len(data) == 1
    sent = json.loads(data[0].removeprefix("data:"))["payload"]
    assert sent == {key: value for key, value in payload.items() if key != "error_code"}
    assert "TFT_SECRET_CODE" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"step": "judge"},
        {"counts": {"requirements": -1, "proposals": 0}},
        {"counts": {"requirements": 1}},
        {"counts": {"requirements": 1, "proposals": 0, "raw": 1}},
        {"document_version_id": "not-a-uuid"},
    ],
)
def test_event_payload_refuses_bad_rfp_values(payload):
    """계약 표에 없는 step·음수·빠진·모르는 counts 키·UUID가 아닌 문서 판본은 거부한다."""
    with pytest.raises(ValueError):
        EventPayload.model_validate(payload)
