"""KAN-199 RFP 작업(owner=tft)의 전송 분기·상태·SSE 접근을 전용 PostgreSQL로 확인한다. 행은 합성이다."""

import asyncio
import json
from uuid import uuid4

import httpx
import psycopg
import pytest
from pydantic import SecretStr

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.jobs.cloud_run import (
    CloudRunJobsSettings,
    DeliveryResult,
    TftWorkerJobSettings,
    dispatch_one,
)
from backend.api.platform.jobs.dispatch import DispatchSettings, begin_send, claim_next
from backend.api.platform.jobs.events import EventPayload
from backend.api.platform.jobs.tft import TftJobSettings
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
