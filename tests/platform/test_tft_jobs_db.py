"""KAN-182 tft 작업 내부 접수를 전용 PostgreSQL과 platform_app 계정으로 확인한다. 행은 합성이다."""

from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api.platform.jobs.tft import TftJobSettings, configure_tft_jobs
from backend.api.platform.main import create_app

GOOD = {"Authorization": "Bearer good-caller"}
URL = "/internal/v1/platform/jobs/tft"


class FakeCaller:
    """지정 tft 실행 계정 토큰만 통과시키는 합성 검증기."""

    def verify(self, token):
        if token != "good-caller":
            raise HTTPException(401)


def enabled():
    return TftJobSettings(
        _env_file=None,
        enabled=True,
        service_url="https://platform.example.run.app",
        invoker_email="tft-runtime@proj-test.iam.gserviceaccount.com",
    )


@pytest.fixture
def client(settings, jobs_database):
    app = create_app(settings=settings)
    configure_tft_jobs(app, enabled(), caller=FakeCaller())
    return TestClient(app)


@pytest.fixture
def body(jobs_database, registered_user):
    value = {
        "actor_id": str(registered_user[1]),
        "kind": "rfp.process",
        "project_id": str(uuid4()),
        "input_version": "a" * 64,
        "idempotency_key": str(uuid4()),
        "request_hash": "b" * 64,
    }
    yield value
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "DELETE FROM runtime.job_events WHERE job_id IN "
            "(SELECT id FROM runtime.jobs WHERE actor_id=%s)",
            (value["actor_id"],),
        )
        conn.execute("DELETE FROM runtime.jobs WHERE actor_id=%s", (value["actor_id"],))


def rows(dsn, actor):
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT j.owner, j.kind, j.subject_id::text, j.status, "
            "(SELECT count(*) FROM runtime.job_events e WHERE e.job_id=j.id), "
            "(SELECT count(*) FROM runtime.dispatch_outbox o WHERE o.job_id=j.id) "
            "FROM runtime.jobs j WHERE j.actor_id=%s ORDER BY j.created_at",
            (actor,),
        ).fetchall()


def test_disabled_route_is_closed(settings, jobs_database, body):
    response = TestClient(create_app(settings=settings)).post(URL, json=body, headers=GOOD)
    assert response.status_code == 404


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer someone-else"}])
def test_only_the_tft_caller_can_admit(client, jobs_database, body, headers):
    assert client.post(URL, json=body, headers=headers).status_code == 401
    assert rows(jobs_database, body["actor_id"]) == []


def test_admission_stores_a_queued_job_without_dispatch(client, jobs_database, body):
    first = client.post(URL, json=body, headers=GOOD)
    assert first.status_code == 201
    data = first.json()["data"]
    assert data["status_url"] == f"/api/v1/platform/jobs/{data['job_id']}"
    assert (data["kind"], data["project_id"], data["created"]) == (
        "rfp.process",
        body["project_id"],
        True,
    )
    # 처리 worker 연결 전이라 평가 전송기가 집어 갈 outbox 행이 없다.
    assert rows(jobs_database, body["actor_id"]) == [
        ("tft", "rfp.process", body["project_id"], "QUEUED", 1, 0)
    ]
    replay = client.post(URL, json=body, headers=GOOD)
    assert (replay.status_code, replay.json()["data"]["job_id"]) == (200, data["job_id"])
    assert len(rows(jobs_database, body["actor_id"])) == 1


@pytest.mark.parametrize(
    "change", [{"request_hash": "c" * 64}, {"input_version": "d" * 64}, {"project_id": None}]
)
def test_same_key_with_different_content_conflicts(client, jobs_database, body, change):
    assert client.post(URL, json=body, headers=GOOD).status_code == 201
    other = {**body, **{k: v or str(uuid4()) for k, v in change.items()}}
    response = client.post(URL, json=other, headers=GOOD)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "IDEMPOTENCY_CONFLICT")


def test_one_running_job_per_project(client, jobs_database, body):
    assert client.post(URL, json=body, headers=GOOD).status_code == 201
    export = {**body, "kind": "rfp.export", "idempotency_key": str(uuid4())}
    response = client.post(URL, json=export, headers=GOOD)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "TFT_JOB_IN_PROGRESS")
    # 끝난 뒤에는 같은 프로젝트에 새 작업을 받는다.
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "UPDATE runtime.jobs SET status='SUCCEEDED' WHERE subject_id=%s", (body["project_id"],)
        )
    assert client.post(URL, json=export, headers=GOOD).status_code == 201


def test_unknown_kind_and_extra_fields_are_refused(client, body):
    assert (
        client.post(URL, json={**body, "kind": "evaluation.run"}, headers=GOOD).status_code == 422
    )
    assert client.post(URL, json={**body, "prompt": "x"}, headers=GOOD).status_code == 422


def test_database_refuses_mixed_owner_and_kind(jobs_database, registered_user):
    with psycopg.connect(jobs_database) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO runtime.jobs (id,owner,kind,business_id,subject_id,input_version,"
                "actor_id,method,route,idempotency_key,request_hash) VALUES (%s,'tft',"
                "'evaluation.run',%s,%s,%s,%s,'POST','/api/v1/platform/evaluations/runs',%s,%s)",
                (uuid4(), uuid4(), uuid4(), "a" * 64, registered_user[1], "key-00001", "b" * 64),
            )


def test_tft_runtime_role_still_has_no_runtime_access(jobs_database):
    with psycopg.connect(jobs_database) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='tft_app'").fetchone():
            pytest.skip("tft_app 역할이 없는 검사 DB")
        assert not conn.execute(
            "SELECT has_table_privilege('tft_app', 'runtime.jobs', 'INSERT')"
        ).fetchone()[0]
