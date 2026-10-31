"""실제 PostgreSQL과 HTTP 스트림에서 평가 이벤트 복구를 검증한다."""

import asyncio
import json
import socket
import threading
import time
from uuid import uuid4

import httpx
import psycopg
import pytest
import uvicorn

from backend.api.platform.auth.http import SESSION_COOKIE
from backend.api.platform.evaluations.controls import control_run
from backend.api.platform.evaluations.smoke_worker import dispatch_one
from tests.platform.test_job_status import job_client as job_client


def read_frame(lines):
    """HTTP 스트림에서 빈 줄로 끝나는 한 프레임을 읽는다.

    Args:
        lines: httpx의 줄 반복자.

    Returns:
        str: 프레임 본문. 스트림이 끝나면 빈 문자열.
    """
    frame = []
    for line in lines:
        if not line:
            return "\n".join(frame)
        frame.append(line)
    return "\n".join(frame)


@pytest.fixture
def live_job(job_client, monkeypatch):
    """작업 API를 임시 loopback HTTP 서버에서 실행한다.

    Args:
        job_client: 실제 DB 세션·작업을 가진 TestClient.
        monkeypatch: 검사에서만 poll/heartbeat 시간을 단축한다.

    Returns:
        tuple: 네트워크 HTTP 클라이언트와 접수 결과.
    """
    client, admitted = job_client
    monkeypatch.setattr("backend.api.platform.jobs.events.POLL_SECONDS", 0.02)
    monkeypatch.setattr("backend.api.platform.jobs.events.HEARTBEAT_SECONDS", 0.08)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(client.app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(
            base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",
            headers={"Cookie": f"{SESSION_COOKIE}={client.cookies.get(SESSION_COOKIE)}"},
            timeout=3,
        ) as http:
            yield http, admitted
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("cursor", ["", "-1", "+1", "1.0", "１", "9223372036854775808", "9"])
def test_invalid_cursor_before_stream_headers(job_client, cursor):
    """형식 오류·범위 초과·미래 커서는 스트림 시작 전에400을 반환한다.

    Args:
        job_client: 실제 접수와 세션.
        cursor: 잘못된 헤더 커서.

    Returns:
        None: 공통 JSON 오류와 안전한 코드를 확인한다.
    """
    client, admitted = job_client
    response = client.get(
        f"/api/v1/platform/jobs/{admitted.job_id}/events",
        headers={"Last-Event-ID": cursor} if cursor.isascii() else {},
        params={} if cursor.isascii() else {"after": cursor},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_EVENT_CURSOR"
    assert response.headers["content-type"].startswith("application/json")


def test_replay_precedence_and_terminal_reconnect(job_client, settings, access):
    """종료 이벤트까지 재생하고 헤더 우선·이후 위치·종료204를 지킨다.

    Args:
        job_client: 합성 실행과 실제 세션.
        settings: 실행 DB 설정.
        access: 현재 평가 허용 설정.

    Returns:
        None: 실제 worker의 저장 순서와 재접속 동작을 확인한다.
    """
    client, admitted = job_client
    asyncio.run(dispatch_one(settings, access))
    url = f"/api/v1/platform/jobs/{admitted.job_id}/events"
    all_events = client.get(url)
    assert all_events.status_code == 200
    assert all_events.headers["content-type"].startswith("text/event-stream")
    assert all_events.headers["x-accel-buffering"] == "no"
    assert "no-store" in all_events.headers["cache-control"]
    assert [line for line in all_events.text.splitlines() if line.startswith("id:")] == [
        f"id: {i}" for i in range(1, 6)
    ]
    assert "generation" not in all_events.text
    assert '"state":"SUCCEEDED"' in all_events.text
    replay = client.get(url + "?after=invalid", headers={"Last-Event-ID": "3"})
    assert [line for line in replay.text.splitlines() if line.startswith("id:")] == [
        "id: 4",
        "id: 5",
    ]
    assert client.get(url + "?after=5").status_code == 204
    assert client.get(url, headers={"Last-Event-ID": "5"}).content == b""


def test_live_stream_disconnect_and_cancel_recovery(live_job, settings, access, experiment):
    """연결을 끊은 뒤 저장된 취소를 재접속으로 한 번 재생한다.

    Args:
        live_job: 실제 loopback HTTP 스트림.
        settings: 실행 설정.
        access: 평가 허용 목록.
        experiment: 현재 소유자.

    Returns:
        None: 첫 이벤트·heartbeat·접속 종료 후 취소 재생과204를 확인한다.
    """
    http, admitted = live_job
    url = f"/api/v1/platform/jobs/{admitted.job_id}/events"
    with http.stream("GET", url) as response:
        assert response.status_code == 200
        lines = response.iter_lines()
        assert read_frame(lines).startswith("id: 1\nevent: job.queued")
        assert read_frame(lines) == ": heartbeat"
    asyncio.run(
        control_run(
            settings,
            access,
            experiment["actor"],
            admitted.run_id,
            "cancel",
            str(uuid4()),
            '"1"',
            uuid4(),
        )
    )
    resumed = http.get(url, headers={"Last-Event-ID": "1"})
    assert resumed.status_code == 200
    assert resumed.text.startswith("id: 2\nevent: job.completed")
    assert '"state":"CANCELLED"' in resumed.text
    assert http.get(url + "?after=2").status_code == 204


@pytest.mark.parametrize(
    "revoke", ["session", "expiry", "account", "role", "evaluator", "owner", "database"]
)
def test_open_stream_stops_after_access_loss(live_job, evaluation_database, access, revoke):
    """연결 중 권한 회수·DB 장애 후 신규 업무 이벤트를 보내지 않는다.

    Args:
        live_job: 실제 HTTP 스트림.
        evaluation_database: 격리 관리자 연결.
        access: 앱의 현재 평가자 설정.
        revoke: 연결 도중 회수할 조건.

    Returns:
        None: 실패 시 스트림 종료 및 새 연결의 오류 응답을 확인한다.
    """
    http, admitted = live_job
    url = f"/api/v1/platform/jobs/{admitted.job_id}/events"
    try:
        with http.stream("GET", url) as response:
            lines = response.iter_lines()
            assert read_frame(lines).startswith("id: 1")
            with psycopg.connect(evaluation_database) as conn:
                if revoke == "session":
                    conn.execute("DELETE FROM identity.sessions")
                elif revoke == "expiry":
                    conn.execute(
                        "UPDATE identity.sessions SET created_at=now()-interval '2 hours',expires_at=now()-interval '1 second'"
                    )
                elif revoke == "account":
                    conn.execute("UPDATE identity.users SET is_active=false")
                elif revoke == "role":
                    conn.execute("DELETE FROM identity.user_roles")
                elif revoke == "evaluator":
                    access.evaluator_user_ids = frozenset()
                elif revoke == "owner":
                    conn.execute(
                        "UPDATE evaluation.runs SET requested_by_user_id=NULL WHERE id=%s",
                        (admitted.run_id,),
                    )
                else:
                    conn.execute("REVOKE SELECT ON runtime.job_events FROM platform_app")
                conn.execute(
                    "INSERT INTO runtime.job_events(job_id,sequence,type) VALUES (%s,2,'job.progress')",
                    (admitted.job_id,),
                )
            remaining = list(lines)
            assert all(not line.startswith(("id:", "event:", "data:")) for line in remaining)
        assert (
            http.get(url).status_code
            == {
                "session": 401,
                "expiry": 401,
                "account": 401,
                "role": 401,
                "evaluator": 403,
                "owner": 404,
                "database": 503,
            }[revoke]
        )
    finally:
        if revoke == "database":
            with psycopg.connect(evaluation_database) as conn:
                conn.execute("GRANT SELECT ON runtime.job_events TO platform_app")


def test_live_stream_delivers_new_commits(live_job, settings, access):
    """이미 열린 스트림이 worker가 새로 commit한 종료까지 순서대로 받는다.

    Args:
        live_job: 실제 HTTP 스트림.
        settings: worker 연결 설정.
        access: 허용된 합성 설정.

    Returns:
        None: 중복·누락 없이1부터5까지 전송됨을 확인한다.
    """
    http, admitted = live_job
    with http.stream("GET", f"/api/v1/platform/jobs/{admitted.job_id}/events") as response:
        lines = response.iter_lines()
        first = read_frame(lines)
        assert first.startswith("id: 1")
        asyncio.run(dispatch_one(settings, access))
        rest = list(lines)
    assert [line for line in rest if line.startswith("id:")] == [f"id: {i}" for i in range(2, 6)]


def test_bounded_replay_payload_filter_and_retention(job_client, evaluation_database):
    """100건 경계에서도 순서를 유지하고 민감 필드를 제거하며 지난 커서를 거부한다.

    Args:
        job_client: 실제 세션과 접수.
        evaluation_database: 대량 합성 이벤트를 기록할 전용 DB.

    Returns:
        None: 배치 경계·허용 payload·만료410 및 스냅샷 복구를 확인한다.
    """
    client, admitted = job_client
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            'INSERT INTO runtime.job_events(job_id,sequence,type,payload) SELECT %s,n,\'job.progress\',\'{"step":"smoke","raw_output":"synthetic-private","generation":5}\' FROM generate_series(2,105) n',
            (admitted.job_id,),
        )
        conn.execute(
            "INSERT INTO runtime.job_events(job_id,sequence,type,payload) VALUES (%s,106,'job.completed','{\"state\":\"SUCCEEDED\"}')",
            (admitted.job_id,),
        )
        conn.execute(
            "UPDATE evaluation.runs SET status='SUCCEEDED',finished_at=now() WHERE id=%s",
            (admitted.run_id,),
        )
    url = f"/api/v1/platform/jobs/{admitted.job_id}/events"
    response = client.get(url)
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert [e["sequence"] for e in events] == list(range(1, 107))
    assert events[1]["payload"] == {"step": "smoke"}
    assert "synthetic-private" not in response.text
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "DELETE FROM runtime.job_events WHERE job_id=%s AND sequence<100", (admitted.job_id,)
        )
    expired = client.get(url + "?after=1")
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "EVENT_CURSOR_EXPIRED"
    snapshot = client.get(f"/api/v1/platform/jobs/{admitted.job_id}").json()["data"]
    assert client.get(url + f"?after={snapshot['last_event_sequence']}").status_code == 204


def test_unsafe_known_payload_is_not_streamed(job_client, evaluation_database):
    """허용 키에 임의 문자열을 넣어도 전송 전에503으로 거부한다.

    Args:
        job_client: 실제 세션과 접수.
        evaluation_database: 변조된 합성 이벤트를 기록할 전용 DB.

    Returns:
        None: 민감 문자열이 오류 본문에도 노출되지 않음을 확인한다.
    """
    client, admitted = job_client
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            'UPDATE runtime.job_events SET payload=\'{"state":"synthetic-private"}\' WHERE job_id=%s',
            (admitted.job_id,),
        )
    response = client.get(f"/api/v1/platform/jobs/{admitted.job_id}/events")
    assert response.status_code == 503
    assert "synthetic-private" not in response.text


@pytest.mark.parametrize("boundary", ["test", "production", "disabled"])
def test_stream_preserves_execution_locks(
    job_client, evaluation_database, settings, access, boundary
):
    """SSE도 최종 시험과 운영 실행 잠금을 우회하지 못한다.

    Args:
        job_client: 실제 세션과 접수.
        evaluation_database: 검사 관리자 연결.
        settings: 현재 실행 환경.
        access: 현재 평가 설정.
        boundary: 차단할 조건.

    Returns:
        None: 시험404 또는 실행 잠금503이 헤더 전에 반환됨을 확인한다.
    """
    client, admitted = job_client
    if boundary == "test":
        with psycopg.connect(evaluation_database) as conn:
            conn.execute("UPDATE evaluation.experiments SET data_split='test'")
    elif boundary == "production":
        client.app.state.settings = settings.model_copy(update={"environment": "production"})
    else:
        access.local_admission_enabled = False
    response = client.get(f"/api/v1/platform/jobs/{admitted.job_id}/events")
    assert response.status_code == (404 if boundary == "test" else 503)


def test_commit_between_state_and_events_uses_one_snapshot(
    job_client, evaluation_database, monkeypatch
):
    """상태 조회 직후의 동시 commit이 현재 배치에 섞이지 않는다.

    Args:
        job_client: 실제 세션과 접수.
        evaluation_database: 동시 변경을 commit할 별도 연결.
        monkeypatch: 상태 SELECT 직후에만 합성 변경을 삽입한다.

    Returns:
        None: 기존 배치는 이전 상태/이벤트, 다음 배치는 종료까지 반환한다.
    """
    from fastapi import Request

    from backend.api.platform.jobs import events, status

    client, admitted = job_client
    # 상태 읽기는 owner별 검사기(status.read_platform_job → read_evaluation_job)를 거친다.
    original = status.read_evaluation_job

    async def commit_after_read(conn, actor, job_id):
        """스냅샷을 만든 다음 별도 연결에서 종료와 이벤트를 commit한다.

        Args:
            conn: 조회 트랜잭션 연결.
            actor: 현재 사용자.
            job_id: 작업 UUID.

        Returns:
            EvaluationJobStatus: 동시 commit 이전 스냅샷.
        """
        snapshot = await original(conn, actor, job_id)
        with psycopg.connect(evaluation_database) as writer:
            writer.execute(
                "UPDATE evaluation.runs SET status='CANCELLED',finished_at=now() WHERE id=%s",
                (admitted.run_id,),
            )
            writer.execute(
                "UPDATE evaluation.run_control SET version=version+1 WHERE run_id=%s",
                (admitted.run_id,),
            )
            writer.execute(
                "INSERT INTO runtime.job_events(job_id,sequence,type,payload) VALUES (%s,2,'job.completed','{\"state\":\"CANCELLED\"}')",
                (admitted.job_id,),
            )
        return snapshot

    request = Request(
        {
            "type": "http",
            "app": client.app,
            "headers": [
                (b"cookie", f"{SESSION_COOKIE}={client.cookies.get(SESSION_COOKIE)}".encode())
            ],
        }
    )
    monkeypatch.setattr(status, "read_evaluation_job", commit_after_read)
    first = asyncio.run(events.read_event_batch(request, admitted.job_id, 0))
    assert not first.terminal and first.last_sequence == 1
    assert [e.sequence for e in first.events] == [1]
    monkeypatch.setattr(status, "read_evaluation_job", original)
    second = asyncio.run(events.read_event_batch(request, admitted.job_id, 1))
    assert second.terminal and second.last_sequence == 2
    assert [e.sequence for e in second.events] == [2]
