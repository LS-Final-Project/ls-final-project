"""KAN-194 9차 1/3 cs 작업 내부 접수를 전용 PostgreSQL과 platform_app 계정으로 확인한다. 행은 합성이다."""

from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api.platform.jobs.cs import ROUTE, CsJobSettings, configure_cs_jobs
from backend.api.platform.main import create_app

GOOD = {"Authorization": "Bearer good-caller"}
URL = "/internal/v1/platform/jobs/cs"


class FakeCaller:
    """지정 cs 실행 계정 토큰만 통과시키는 합성 검증기."""

    def verify(self, token):
        """good-caller만 통과시킨다.

        Args:
            token: Bearer 토큰.

        Returns:
            None: 다른 토큰은 401이다.
        """
        if token != "good-caller":
            raise HTTPException(401)


def enabled(**changes):
    """켠 접수 설정.

    Args:
        **changes: 바꿀 값.

    Returns:
        CsJobSettings: 설정.
    """
    values = {"processor_connected": True, **changes}
    return CsJobSettings(
        _env_file=None,
        **values,
        enabled=True,
        service_url="https://platform.example.run.app",
        invoker_email="cs-runtime@proj-test.iam.gserviceaccount.com",
    )


@pytest.fixture
def client(settings, jobs_database):
    """접수를 켠 앱 클라이언트.

    Args:
        settings: platform_app 설정.
        jobs_database: 작업 마이그레이션을 적용한 DB.

    Returns:
        TestClient: 클라이언트.
    """
    app = create_app(settings=settings)
    configure_cs_jobs(app, enabled(), caller=FakeCaller())
    return TestClient(app)


@pytest.fixture
def body(jobs_database, registered_user):
    """합성 접수 본문과 검사 뒤 정리.

    Args:
        jobs_database: 관리자 DSN.
        registered_user: 등록 사용자.

    Returns:
        dict: 접수 본문.
    """
    value = {
        "ticket_id": str(uuid4()),
        "input_version": "a" * 64,
        "idempotency_key": str(uuid4()),
        "requested_by": str(registered_user[1]),
    }
    yield value
    with psycopg.connect(jobs_database) as conn:
        mine = "SELECT id FROM runtime.jobs WHERE actor_id=%s"
        for table in ("job_control", "dispatch_attempts", "dispatch_outbox", "job_events"):
            conn.execute(
                f"DELETE FROM runtime.{table} WHERE job_id IN ({mine})", (value["requested_by"],)
            )
        conn.execute("DELETE FROM runtime.jobs WHERE actor_id=%s", (value["requested_by"],))


def rows(dsn, actor):
    """그 사용자의 작업과 딸린 행 수.

    Args:
        dsn: 관리자 DSN.
        actor: 사용자 ID.

    Returns:
        list[tuple]: (owner, kind, subject_id, business_id=id, status, 이벤트 수, outbox 수, 제어 행 수).
    """
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT j.owner, j.kind, j.subject_id::text, j.business_id = j.id, j.status, "
            "(SELECT count(*) FROM runtime.job_events e WHERE e.job_id=j.id), "
            "(SELECT count(*) FROM runtime.dispatch_outbox o WHERE o.job_id=j.id), "
            "(SELECT count(*) FROM runtime.job_control c WHERE c.job_id=j.id) "
            "FROM runtime.jobs j WHERE j.actor_id=%s ORDER BY j.created_at",
            (actor,),
        ).fetchall()


def test_disabled_route_is_closed(settings, jobs_database, body):
    """기본 비활성이면 404다."""
    response = TestClient(create_app(settings=settings)).post(URL, json=body, headers=GOOD)
    assert response.status_code == 404


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer someone-else"}])
def test_only_the_cs_caller_can_admit(client, jobs_database, body, headers):
    """지정 cs 실행 계정 토큰이 아니면 401이고 저장하지 않는다."""
    assert client.post(URL, json=body, headers=headers).status_code == 401
    assert rows(jobs_database, body["requested_by"]) == []


def test_admission_stores_job_event_dispatch_intent_and_control(client, jobs_database, body):
    """접수는 QUEUED 작업·job.queued·전송 의도·제어 행을 함께 만들고, 같은 요청 재전송은 그 작업이다."""
    sent = []

    async def soon(app):
        sent.append(app)

    client.app.state.jobs_dispatch_soon = soon
    first = client.post(URL, json=body, headers=GOOD)
    assert first.status_code == 201
    assert len(sent) == 1
    data = first.json()["data"]
    assert data["status_url"] == f"/api/v1/platform/jobs/{data['job_id']}"
    assert (data["ticket_id"], data["status"], data["created"]) == (
        body["ticket_id"],
        "QUEUED",
        True,
    )
    assert rows(jobs_database, body["requested_by"]) == [
        ("cs", "cs.analyze", body["ticket_id"], True, "QUEUED", 1, 1, 1)
    ]
    replay = client.post(URL, json=body, headers=GOOD)
    assert (replay.status_code, replay.json()["data"]["job_id"]) == (200, data["job_id"])
    assert replay.json()["data"]["created"] is False
    assert len(rows(jobs_database, body["requested_by"])) == 1


@pytest.mark.parametrize("field", ["ticket_id", "input_version"])
def test_same_key_with_different_content_conflicts(client, jobs_database, body, field):
    """같은 멱등 키로 문의·입력 판본이 다르면 409 IDEMPOTENCY_CONFLICT다."""
    assert client.post(URL, json=body, headers=GOOD).status_code == 201
    other = {**body, field: str(uuid4()) if field == "ticket_id" else "d" * 64}
    response = client.post(URL, json=other, headers=GOOD)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "IDEMPOTENCY_CONFLICT")


def test_one_running_job_per_ticket(client, jobs_database, body):
    """같은 문의에 진행 중 작업이 있으면 409 CS_JOB_IN_PROGRESS이고, 끝난 뒤에는 새 작업을 받는다."""
    assert client.post(URL, json=body, headers=GOOD).status_code == 201
    again = {**body, "idempotency_key": str(uuid4())}
    response = client.post(URL, json=again, headers=GOOD)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "CS_JOB_IN_PROGRESS")
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "UPDATE runtime.jobs SET status='SUCCEEDED' WHERE subject_id=%s", (body["ticket_id"],)
        )
    assert client.post(URL, json=again, headers=GOOD).status_code == 201


def test_extra_fields_and_bad_values_are_refused(client, body):
    """원문 등 모르는 필드와 형식이 틀린 값은 422다."""
    assert client.post(URL, json={**body, "body": "x"}, headers=GOOD).status_code == 422
    assert client.post(URL, json={**body, "input_version": "x"}, headers=GOOD).status_code == 422


def test_database_refuses_cs_rows_outside_the_contract(jobs_database, registered_user):
    """cs 행은 정해진 경로·문의·business_id=id가 아니면 DB가 거부한다."""
    job_id = uuid4()
    for business_id, route, subject in (
        (uuid4(), ROUTE, uuid4()),
        (job_id, "/api/v1/cs/other", uuid4()),
        (job_id, ROUTE, None),
    ):
        with psycopg.connect(jobs_database) as conn, pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO runtime.jobs (id,owner,kind,business_id,subject_id,input_version,"
                "actor_id,method,route,idempotency_key,request_hash) VALUES (%s,'cs','cs.analyze',"
                "%s,%s,%s,%s,'POST',%s,%s,%s)",
                (job_id, business_id, subject, "a" * 64, registered_user[1], route, "key-00001")
                + ("b" * 64,),
            )


def test_worker_role_has_only_runtime_column_privileges(jobs_database):
    """cs_worker는 제어·이벤트·상태 열만 쓰고 작업 생성·전송 장부 권한이 없으며, cs_app은 runtime 권한이 없다."""
    with psycopg.connect(jobs_database) as conn:

        def has(role, privilege, table, column=None):
            if column is None:
                query, args = "SELECT has_table_privilege(%s,%s,%s)", (role, table, privilege)
            else:
                query = "SELECT has_column_privilege(%s,%s,%s,%s)"
                args = (role, table, column, privilege)
            return conn.execute(query, args).fetchone()[0]

        assert has("cs_worker", "UPDATE", "runtime.job_control", "lease_expires_at")
        assert has("cs_worker", "UPDATE", "runtime.jobs", "status")
        assert has("cs_worker", "INSERT", "runtime.job_events", "payload")
        assert not has("cs_worker", "INSERT", "runtime.jobs")
        assert not has("cs_worker", "UPDATE", "runtime.jobs", "actor_id")
        assert not has("cs_worker", "SELECT", "runtime.dispatch_outbox")
        assert not has("cs_worker", "UPDATE", "runtime.job_control", "cancel_requested_at")
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname='cs_app'").fetchone():
            assert not has("cs_app", "SELECT", "runtime.jobs")


def test_unregistered_actor_is_a_client_error(client, jobs_database, body):
    """등록되지 않은 사용자는 422 JOB_ACTOR_UNKNOWN이다."""
    response = client.post(URL, json={**body, "requested_by": str(uuid4())}, headers=GOOD)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "JOB_ACTOR_UNKNOWN")


def test_nothing_is_stored_before_the_processor_is_connected(settings, jobs_database, body):
    """처리기 연결 전이면 503이고 저장하지 않는다(문의가 409로 잠기지 않게)."""
    app = create_app(settings=settings)
    configure_cs_jobs(app, enabled(processor_connected=False), caller=FakeCaller())
    response = TestClient(app).post(URL, json=body, headers=GOOD)
    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "CS_PROCESSOR_NOT_CONNECTED",
    )
    assert rows(jobs_database, body["requested_by"]) == []


def test_caller_is_checked_before_the_body(settings, client):
    """본문 검사보다 호출자 검사가 먼저다(비활성 404, 토큰 없음 401)."""
    assert client.post(URL, json={"ticket_id": "x"}).status_code == 401
    closed = TestClient(create_app(settings=settings))
    assert closed.post(URL, json={"ticket_id": "x"}, headers=GOOD).status_code == 404


def test_settings_require_service_url_and_caller_when_enabled():
    """켰을 때 서비스 주소·호출 계정 형식이 틀리면 설정이 거부된다."""
    with pytest.raises(ValueError):
        CsJobSettings(_env_file=None, enabled=True, service_url="http://x", invoker_email="a@b")
    assert CsJobSettings(_env_file=None).enabled is False
