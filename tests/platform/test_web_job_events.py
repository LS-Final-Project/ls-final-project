"""실제 Node 웹 중계·플랫폼·PostgreSQL을 연결하는 합성 소비자 회귀."""

import asyncio
import os
import selectors
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest

from backend.api.platform.evaluations.controls import control_run
from tests.platform.test_job_events import live_job as live_job
from tests.platform.test_job_events import read_frame
from tests.platform.test_job_status import job_client as job_client


@pytest.fixture
def web_job(live_job):
    """검사용 IAM 토큰 공급자만 대체하고 실제 웹 handler를 Node에서 실행한다.

    Args:
        live_job: 실제 세션과 loopback 플랫폼 HTTP 서버.

    Returns:
        tuple: 웹 중계 HTTP 클라이언트와 접수 결과.
    """
    platform, admitted = live_job
    module = Path("frontend/server.mjs").resolve().as_uri()
    script = """
import { createServer } from 'node:http';
const { handler } = await import(process.env.TEST_WEB_MODULE);
const server = createServer(handler({
  directory: '/tmp', upstream: process.env.TEST_PLATFORM_URL,
  tokenProvider: async () => 'synthetic-iam-token', evaluationReadRelayEnabled: true,
}));
server.listen(0, '127.0.0.1', () => console.log(server.address().port));
"""
    process = subprocess.Popen(
        ["node", "--input-type=module", "-e", script],
        env={
            **os.environ,
            "NODE_ENV": "test",
            "TEST_WEB_MODULE": module,
            "TEST_PLATFORM_URL": str(platform.base_url),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=5), "검사 웹 서버가 준비되지 않았습니다."
        port = process.stdout.readline().strip()
        assert port.isdigit(), "검사 웹 서버의 포트를 확인할 수 없습니다."
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Cookie": platform.headers["Cookie"]},
            timeout=5,
        ) as client:
            yield client, admitted
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def test_web_consumer_recovers_after_disconnect(web_job, settings, access, experiment):
    """웹 상태 snapshot 뒤 연결 단절·취소를 SSE와 최종 GET으로 복구한다.

    Args:
        web_job: 실제 웹 중계와 접수.
        settings: 실행 DB 설정.
        access: 허용된 합성 실행 설정.
        experiment: 현재 소유자.

    Returns:
        None: GET→SSE→단절→취소→재접속→종료 GET/204를 확인한다.
    """
    client, admitted = web_job
    url = f"/api/v1/platform/jobs/{admitted.job_id}"
    snapshot = client.get(url)
    assert snapshot.status_code == 200
    assert snapshot.headers["etag"] == '"1"'
    sequence = snapshot.json()["data"]["last_event_sequence"]
    with client.stream("GET", url + f"/events?after={sequence}") as stream:
        assert stream.status_code == 200
        assert stream.headers["x-accel-buffering"] == "no"
        assert read_frame(stream.iter_lines()) == ": heartbeat"
    asyncio.run(
        control_run(
            settings,
            access,
            experiment["actor"],
            admitted.run_id,
            "cancel",
            str(uuid4()),
            snapshot.headers["etag"],
            uuid4(),
        )
    )
    resumed = client.get(url + "/events?after=0", headers={"Last-Event-ID": str(sequence)})
    assert resumed.status_code == 200
    assert resumed.text.startswith("id: 2\nevent: job.completed")
    assert '"state":"CANCELLED"' in resumed.text
    assert "id: 1" not in resumed.text
    terminal = client.get(url).json()["data"]
    assert terminal["status"] == "CANCELLED"
    assert client.get(url + f"/events?after={terminal['last_event_sequence']}").status_code == 204


def test_web_stream_closes_on_session_revocation(web_job, evaluation_database):
    """실제 플랫폼 세션 철회가 열린 웹 SSE와 다음 GET에 반영된다.

    Args:
        web_job: 실제 웹 중계·플랫폼·접수.
        evaluation_database: 격리 DB의 세션 회수 연결.

    Returns:
        None: 스트림 종료와 재조회401이 그대로 보존됨을 확인한다.
    """
    client, admitted = web_job
    url = f"/api/v1/platform/jobs/{admitted.job_id}"
    with client.stream("GET", url + "/events") as stream:
        lines = stream.iter_lines()
        assert read_frame(lines).startswith("id: 1")
        with psycopg.connect(evaluation_database) as conn:
            conn.execute("DELETE FROM identity.sessions")
        assert all(not line.startswith(("id:", "data:", "event:")) for line in lines)
    assert client.get(url).status_code == 401
    assert client.get(url + "/events").status_code == 401
