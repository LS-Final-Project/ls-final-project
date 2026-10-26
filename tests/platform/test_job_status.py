"""실제 세션·PostgreSQL을 사용하는 평가 작업 상태 조회 회귀."""

import asyncio
from uuid import uuid4

import psycopg
import pytest

from backend.api.platform.auth.callback import MicrosoftSubject
from backend.api.platform.auth.http import SESSION_COOKIE
from backend.api.platform.auth.sessions import issue_session
from backend.api.platform.evaluations.admission import accept_run
from backend.api.platform.evaluations.smoke_worker import dispatch_one
from tests.platform.test_evaluation_access import client_for


@pytest.fixture
def job_client(settings, access, experiment, registered_user):
    """합성 사용자에게 실제 DB 세션과 작업을 발급한다.

    Args:
        settings: 검사 DB 실행 계정 설정.
        access: 지정 평가자와 합성 구성 허용 목록.
        experiment: 고정 합성 실험.
        registered_user: 실제 DB에 등록한 합성 사용자.

    Returns:
        tuple: HTTP 클라이언트와 저장된 접수 결과.
    """
    grant = asyncio.run(issue_session(settings, registered_user[0]))
    admitted = asyncio.run(
        accept_run(settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4())
    ).data
    client = client_for((experiment["actor"],))
    client.app.state.settings = settings
    client.app.state.evaluation_access = access
    client.cookies.set(SESSION_COOKIE, grant.token.get_secret_value())
    with client:
        yield client, admitted


def test_snapshot_tracks_completed_run_without_sensitive_fields(job_client, settings, access):
    """접수와 종료의 판본·이벤트 위치를 노출 허용 필드만으로 반환한다.

    Args:
        job_client: 실제 DB 세션과 합성 접수.
        settings: 검사 환경 설정.
        access: 합성 실행 허용 설정.

    Returns:
        None: 실제 worker 완료와 조회 상태·헤더·필드 집합을 확인한다.
    """
    client, admitted = job_client
    url = f"/api/v1/platform/jobs/{admitted.job_id}"
    queued = client.get(url)
    assert queued.status_code == 200, queued.text
    data = queued.json()["data"]
    assert data == {
        "job_id": str(admitted.job_id),
        "run_id": str(admitted.run_id),
        "owner": "platform",
        "kind": "evaluation.run",
        "status": "QUEUED",
        "run_status": "QUEUED",
        "version": 1,
        "last_event_sequence": 1,
        "needs_review": False,
    }
    assert queued.headers["etag"] == '"1"'
    assert queued.headers["cache-control"] == "no-store"
    asyncio.run(dispatch_one(settings, access))
    completed = client.get(url)
    state = completed.json()["data"]
    assert state["status"] == state["run_status"] == "SUCCEEDED"
    assert state["last_event_sequence"] == 5
    assert state["version"] > data["version"]
    assert set(state) == set(data)
    assert completed.headers["etag"] == f'"{state["version"]}"'
    access.approved_config_hashes = frozenset()
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("revocation", ["session", "expiry", "account", "role", "evaluator"])
def test_revocation_is_checked_on_every_request(
    job_client, evaluation_database, access, revocation
):
    """세션·계정·역할·지정 철회가 다음 조회에서 즉시 거부된다.

    Args:
        job_client: 유효한 DB 세션과 작업.
        evaluation_database: 전용 관리자 연결.
        access: 현재 평가자 설정.
        revocation: 철회할 인증 조건.

    Returns:
        None: 첫 조회 성공과 철회 후401 또는403을 확인한다.
    """
    client, admitted = job_client
    url = f"/api/v1/platform/jobs/{admitted.job_id}"
    assert client.get(url).status_code == 200
    with psycopg.connect(evaluation_database) as conn:
        if revocation == "session":
            conn.execute("DELETE FROM identity.sessions")
        elif revocation == "expiry":
            conn.execute(
                "UPDATE identity.sessions SET created_at=now()-interval '2 hours', "
                "expires_at=now()-interval '1 second'"
            )
        elif revocation == "account":
            conn.execute("UPDATE identity.users SET is_active=false")
        elif revocation == "role":
            conn.execute("DELETE FROM identity.user_roles")
        else:
            access.evaluator_user_ids = frozenset()
    assert client.get(url).status_code == (403 if revocation == "evaluator" else 401)


@pytest.mark.parametrize("boundary", ["requester", "creator", "test", "missing"])
def test_unavailable_jobs_are_hidden(job_client, evaluation_database, boundary):
    """세 소유권 경계와 잠긴 시험·없는 작업을 동일404로 숨긴다.

    Args:
        job_client: 유효한 DB 세션과 작업.
        evaluation_database: 전용 관리자 연결.
        boundary: 변경할 소유권 또는 조회 대상.

    Returns:
        None: 존재나 내부 식별자를 오류에 포함하지 않음을 확인한다.
    """
    client, admitted = job_client
    with psycopg.connect(evaluation_database) as conn:
        if boundary == "requester":
            conn.execute(
                "UPDATE evaluation.runs SET requested_by_user_id=NULL WHERE id=%s",
                (admitted.run_id,),
            )
        elif boundary == "creator":
            conn.execute("UPDATE evaluation.experiments SET created_by_user_id=NULL")
        elif boundary == "test":
            conn.execute("UPDATE evaluation.experiments SET data_split='test'")
    target = uuid4() if boundary == "missing" else admitted.job_id
    response = client.get(f"/api/v1/platform/jobs/{target}")
    assert response.status_code == 404, response.text
    assert str(admitted.run_id) not in response.text


@pytest.mark.parametrize("status", ["CANCELLED", "PARTIAL", "FAILED", "TIMED_OUT"])
def test_terminal_state_mapping(job_client, evaluation_database, status):
    """종료 상태를 작업 공통 enum으로 투영하면서 평가 원래 상태를 보존한다.

    Args:
        job_client: 실제 접수와 세션.
        evaluation_database: 검사 관리자 연결.
        status: 평가 종료 상태.

    Returns:
        None: 종료 복구용 상태 조회의 정확한 매핑을 확인한다.
    """
    client, admitted = job_client
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.runs SET status=%s,finished_at=now() WHERE id=%s",
            (status, admitted.run_id),
        )
    data = client.get(f"/api/v1/platform/jobs/{admitted.job_id}").json()["data"]
    assert data["run_status"] == status
    assert data["status"] == ("FAILED" if status == "TIMED_OUT" else status)


def test_environment_lock_and_database_failure(job_client, settings, access, evaluation_database):
    """운영·비활성 잠금과 DB 오류를 합성 성공으로 숨기지 않는다.

    Args:
        job_client: 실제 세션과 작업.
        settings: 실행 설정.
        access: 평가 허용 설정.
        evaluation_database: 전용 DB 관리자 연결.

    Returns:
        None: 잠금과 저장소 실패가503으로 반환됨을 확인한다.
    """
    client, admitted = job_client
    url = f"/api/v1/platform/jobs/{admitted.job_id}"
    client.app.state.settings = settings.model_copy(update={"environment": "production"})
    assert client.get(url).status_code == 503
    client.app.state.settings = settings
    access.local_admission_enabled = False
    assert client.get(url).status_code == 503
    access.local_admission_enabled = True
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("REVOKE SELECT ON runtime.job_events FROM platform_app")
    try:
        response = client.get(url)
        assert response.status_code == 503
        assert "job_events" not in response.text
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute("GRANT SELECT ON runtime.job_events TO platform_app")


def test_other_evaluator_cannot_read_job(job_client, settings, access, evaluation_database):
    """다른 지정 평가자의 유효 세션도 남의 작업을 읽지 못한다.

    Args:
        job_client: 소유자의 세션과 작업.
        settings: 실행 DB 연결.
        access: 지정 평가자 설정.
        evaluation_database: 격리 관리자 연결.

    Returns:
        None: 실제 다른 사용자 세션의404와 익명401을 확인한다.
    """
    client, admitted = job_client
    actor, tid, oid = uuid4(), uuid4(), uuid4()
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name) "
            "VALUES (%s,%s,%s,'other@example.invalid','합성 다른 평가자')",
            (actor, tid, oid),
        )
        conn.execute("INSERT INTO identity.user_roles VALUES (%s,'manager')", (actor,))
    try:
        access.evaluator_user_ids = access.evaluator_user_ids | {actor}
        grant = asyncio.run(issue_session(settings, MicrosoftSubject(tid, oid)))
        client.cookies.set(SESSION_COOKIE, grant.token.get_secret_value())
        url = f"/api/v1/platform/jobs/{admitted.job_id}"
        response = client.get(url)
        assert response.status_code == 404
        assert str(admitted.run_id) not in response.text
        client.cookies.clear()
        assert client.get(url).status_code == 401
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute("DELETE FROM identity.sessions WHERE user_id=%s", (actor,))
            conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (actor,))
            conn.execute("DELETE FROM identity.users WHERE id=%s", (actor,))


def test_state_and_event_position_commit_together(job_client, evaluation_database):
    """미커밋 상태·이벤트는 숨기고 commit된 스냅샷을 함께 반환한다.

    Args:
        job_client: 실제 접수와 세션.
        evaluation_database: 상태와 이벤트를 함께 변경할 검사 DB.

    Returns:
        None: commit 전후 판본·상태·sequence와 payload 비노출을 확인한다.
    """
    client, admitted = job_client
    url = f"/api/v1/platform/jobs/{admitted.job_id}"
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.runs SET status='RUNNING',started_at=now() WHERE id=%s",
            (admitted.run_id,),
        )
        conn.execute(
            "UPDATE evaluation.run_control SET version=version+1 WHERE run_id=%s",
            (admitted.run_id,),
        )
        conn.execute(
            "INSERT INTO runtime.job_events(job_id,sequence,type,payload) "
            "VALUES (%s,2,'job.started','{\"private\":\"synthetic-secret\"}')",
            (admitted.job_id,),
        )
        before = client.get(url).json()["data"]
        assert (before["status"], before["version"], before["last_event_sequence"]) == (
            "QUEUED",
            1,
            1,
        )
    response = client.get(url)
    after = response.json()["data"]
    assert (after["status"], after["version"], after["last_event_sequence"]) == ("RUNNING", 2, 2)
    assert "synthetic-secret" not in response.text
