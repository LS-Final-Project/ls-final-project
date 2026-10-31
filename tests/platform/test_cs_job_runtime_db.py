"""KAN-194 9차 2/3 cs 작업의 전송 분기·상태·SSE 접근을 전용 PostgreSQL로 확인한다. 행은 합성이다."""

import asyncio
import json
from uuid import uuid4

import httpx
import psycopg
import pytest
from pydantic import SecretStr

from backend.api.platform.auth.callback import MicrosoftSubject
from backend.api.platform.auth.http import SESSION_COOKIE
from backend.api.platform.auth.sessions import issue_session
from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import accept_run
from backend.api.platform.jobs.cloud_run import (
    CloudRunJobsSettings,
    CsWorkerJobSettings,
    DeliveryResult,
    dispatch_one,
)
from backend.api.platform.jobs.cs import CsJobSettings
from backend.api.platform.jobs.dispatch import DispatchSettings, begin_send, claim_next
from backend.api.platform.jobs.events import EventPayload
from backend.shared.errors import ApplicationError
from tests.platform.test_evaluation_access import client_for

CLOSED = EvaluationAccessSettings()
DISPATCH = DispatchSettings(_env_file=None, enabled=True)
JOBS = CloudRunJobsSettings(
    _env_file=None,
    enabled=True,
    project_number="123456789012",
    job="synthetic-worker",
    timeout_seconds=0.2,
)
WORKER = CsWorkerJobSettings(_env_file=None, enabled=True, job="cs-worker")


def cs_settings(connected=True):
    """C/S 접수·처리기 연결 설정.

    Args:
        connected: 처리기 연결 여부.

    Returns:
        CsJobSettings: 설정.
    """
    return CsJobSettings(
        _env_file=None,
        enabled=True,
        service_url="https://platform.example.run.app",
        invoker_email="cs-runtime@proj-test.iam.gserviceaccount.com",
        processor_connected=connected,
    )


def add_user(dsn, roles):
    """합성 등록 사용자를 만든다.

    Args:
        dsn: 관리자 DSN.
        roles: 부여할 역할.

    Returns:
        tuple[MicrosoftSubject, UUID]: 식별자와 사용자 ID.
    """
    user_id, tid, oid = uuid4(), uuid4(), uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) "
            "VALUES (%s,%s,%s,'other@example.invalid','합성 사용자','TS_AUTO')",
            (user_id, tid, oid),
        )
        for role in roles:
            conn.execute(
                "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,%s)", (user_id, role)
            )
    return MicrosoftSubject(tid, oid), user_id


@pytest.fixture
def others(auth_database):
    """cs_agent가 아닌 사용자와 cs_agent 사용자를 하나씩 만들고 정리한다.

    Args:
        auth_database: 관리자 DSN.

    Returns:
        dict: 'sales'·'agent' 이름의 (식별자, 사용자 ID).
    """
    made = {
        "sales": add_user(auth_database, ["sales"]),
        "agent": add_user(auth_database, ["cs_agent"]),
    }
    yield made
    with psycopg.connect(auth_database) as conn:
        for _, user_id in made.values():
            conn.execute("DELETE FROM identity.sessions WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.users WHERE id=%s", (user_id,))


def outbox(dsn, job_id):
    """전송 의도 상태.

    Args:
        dsn: 관리자 DSN.
        job_id: 작업 ID.

    Returns:
        str: outbox state.
    """
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT state FROM runtime.dispatch_outbox WHERE job_id=%s", (job_id,)
        ).fetchone()[0]


def test_cs_rows_are_dispatched_only_when_the_cs_gate_is_open(settings, jobs_database, cs_job):
    """C/S 게이트가 닫히면 전송 장부 자체가 닫히고, 열리면 cs 행을 cs 종류로 확보한다."""
    for cs in (None, cs_settings(connected=False)):
        with pytest.raises(ApplicationError) as error:
            asyncio.run(claim_next(settings, CLOSED, DISPATCH, cs))
        assert error.value.code == "DISPATCH_DISABLED"
    lease = asyncio.run(claim_next(settings, CLOSED, DISPATCH, cs_settings()))
    assert (lease.job_id, lease.run_id, lease.kind) == (cs_job, cs_job, "cs.analyze")
    assert outbox(jobs_database, cs_job) == "CLAIMED"


def test_evaluation_dispatch_leaves_cs_rows_pending(
    settings, access, experiment, jobs_database, cs_job
):
    """C/S 설정 없이 도는 평가 전송은 평가 행만 집고 cs 행은 PENDING으로 둔다."""
    run = asyncio.run(
        accept_run(settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4())
    ).data
    first = asyncio.run(claim_next(settings, access, DISPATCH))
    assert (first.job_id, first.kind) == (run.job_id, "evaluation.run")
    assert asyncio.run(claim_next(settings, access, DISPATCH)) is None
    assert outbox(jobs_database, cs_job) == "PENDING"


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
def test_begin_send_blocks_cs_jobs_that_must_not_start(settings, jobs_database, cs_job, change):
    """취소 요청·살아 있는 worker lease·종료된 작업은 전송하지 않고 BLOCKED로 남긴다."""
    lease = asyncio.run(claim_next(settings, CLOSED, DISPATCH, cs_settings()))
    with psycopg.connect(jobs_database) as conn:
        conn.execute(change, (cs_job,))
    assert asyncio.run(begin_send(settings, CLOSED, DISPATCH, lease, cs_settings())) is False
    assert outbox(jobs_database, cs_job) == "BLOCKED"


def test_cs_job_is_sent_to_the_cs_worker_job_with_its_job_id(settings, jobs_database, cs_job):
    """cs 작업은 cs worker Job에 CS_JOB_ID만 넘기고, worker Job 설정이 없으면 집지 않는다."""
    posts = []
    operation = f"projects/123456789012/locations/{JOBS.region}/operations/synthetic-op"

    def handler(request):
        posts.append(request)
        return httpx.Response(200, json={"name": operation})

    async def token():
        return SecretStr("synthetic-access-token")

    transport = httpx.MockTransport(handler)
    # worker Job 설정이 없으면 C/S는 허용 종류가 아니다. 평가도 닫혀 있으니 장부가 닫힌다.
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            dispatch_one(
                settings, CLOSED, DISPATCH, JOBS, token, transport=transport, cs=cs_settings()
            )
        )
    assert error.value.code == "DISPATCH_DISABLED"
    assert posts == [] and outbox(jobs_database, cs_job) == "PENDING"
    result = asyncio.run(
        dispatch_one(
            settings,
            CLOSED,
            DISPATCH,
            JOBS,
            token,
            transport=transport,
            cs=cs_settings(),
            cs_worker=WORKER,
        )
    )
    assert result == DeliveryResult("ACCEPTED", operation)
    assert str(posts[0].url).endswith("/jobs/cs-worker:run")
    assert json.loads(posts[0].content)["overrides"]["containerOverrides"] == [
        {"name": "worker", "env": [{"name": "CS_JOB_ID", "value": str(cs_job)}]}
    ]
    assert outbox(jobs_database, cs_job) == "ACCEPTED"


def session_client(settings, subject):
    """그 사용자의 실제 DB 세션을 가진 클라이언트(평가자 지정 없음).

    Args:
        settings: platform_app 설정.
        subject: Microsoft 식별자.

    Returns:
        TestClient: 클라이언트.
    """
    grant = asyncio.run(issue_session(settings, subject))
    client = client_for(())
    client.app.state.settings = settings
    client.cookies.set(SESSION_COOKIE, grant.token.get_secret_value())
    return client


def test_cs_status_is_visible_to_the_requester_and_cs_agents_only(
    settings, jobs_database, registered_user, others, cs_job
):
    """요청자·cs_agent는 C/S 상태를 보고, 그 밖의 사용자·없는 작업·tft 작업은 404다."""
    url = f"/api/v1/platform/jobs/{cs_job}"
    with psycopg.connect(jobs_database) as conn:
        conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (registered_user[1],))
        conn.execute(
            "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'sales')",
            (registered_user[1],),
        )
    with session_client(settings, registered_user[0]) as requester:
        response = requester.get(url)
        assert response.status_code == 200, response.text
        assert response.json()["data"] == {
            "job_id": str(cs_job),
            "ticket_id": response.json()["data"]["ticket_id"],
            "owner": "cs",
            "kind": "cs.analyze",
            "status": "QUEUED",
            "version": 1,
            "last_event_sequence": 1,
        }
        assert requester.get(f"/api/v1/platform/jobs/{uuid4()}").status_code == 404
    with session_client(settings, others["agent"][0]) as agent:
        assert agent.get(url).status_code == 200
    with session_client(settings, others["sales"][0]) as stranger:
        assert stranger.get(url).status_code == 404
        assert stranger.get(url + "/events").status_code == 404


def test_cs_events_stream_only_allowed_keys(settings, jobs_database, registered_user, cs_job):
    """C/S 진행 이벤트는 허용 키만 SSE로 나가고 오류 코드 같은 모르는 키는 버린다."""
    payload = {
        "step": "draft",
        "stage": "DRAFTED",
        "retried": 1,
        "recovered": 0,
        "draft_outcome": "VALID",
        "code": "CS_SECRET_CODE",
    }
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "INSERT INTO runtime.job_events(job_id,sequence,type,payload) "
            "VALUES (%s,2,'job.progress',%s)",
            (cs_job, json.dumps(payload)),
        )
        # 종료 상태여야 스트림이 마지막 이벤트 뒤에 닫힌다.
        conn.execute("UPDATE runtime.jobs SET status='SUCCEEDED' WHERE id=%s", (cs_job,))
    with session_client(settings, registered_user[0]) as client:
        response = client.get(f"/api/v1/platform/jobs/{cs_job}/events?after=1")
    assert response.status_code == 200, response.text
    data = [line for line in response.text.splitlines() if line.startswith("data:")]
    assert len(data) == 1
    sent = json.loads(data[0].removeprefix("data:"))["payload"]
    assert {key: value for key, value in sent.items() if value is not None} == {
        key: value for key, value in payload.items() if key != "code"
    }
    assert "CS_SECRET_CODE" not in response.text


def test_event_payload_refuses_unknown_step_values():
    """step·stage·outcome은 정한 값만 받는다."""
    with pytest.raises(ValueError):
        EventPayload.model_validate({"step": "rewrite"})
    with pytest.raises(ValueError):
        EventPayload.model_validate({"draft_outcome": "SOMETHING"})


def test_access_is_checked_before_the_event_cursor(
    settings, access, experiment, registered_user, others, cs_job
):
    """잘못된 커서보다 권한 오류가 먼저다: 비평가자는 평가 작업 403, 타인 C/S 작업 404."""
    run = asyncio.run(
        accept_run(settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4())
    ).data
    with session_client(settings, others["sales"][0]) as stranger:
        stranger.app.state.evaluation_access = access
        bad = {"Last-Event-ID": "not-a-cursor"}
        assert (
            stranger.get(f"/api/v1/platform/jobs/{run.job_id}/events", headers=bad).status_code
            == 403
        )
        assert (
            stranger.get(f"/api/v1/platform/jobs/{cs_job}/events", headers=bad).status_code == 404
        )
    with session_client(settings, registered_user[0]) as requester:
        response = requester.get(f"/api/v1/platform/jobs/{cs_job}/events", headers=bad)
        assert (response.status_code, response.json()["error"]["code"]) == (
            400,
            "INVALID_EVENT_CURSOR",
        )
