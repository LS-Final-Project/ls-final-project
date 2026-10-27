"""실제 Node 웹 중계·플랫폼·PostgreSQL을 연결하는 합성 소비자 회귀."""

import asyncio
import json
import os
import selectors
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest

from backend.api.platform.evaluations.controls import control_run
from tests.platform.test_cs_smoke_storage import cs_experiment as cs_experiment
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
  evaluationControlRelayEnabled: true,
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


@pytest.mark.parametrize("action", ["cancel", "revoke"])
def test_browser_consumer_observes_real_stream(
    web_job, settings, access, experiment, evaluation_database, action
):
    """실제 화면 소비자가 웹·플랫폼·DB의 취소와 권한 회수를 복구한다.

    Args:
        web_job: 실제 웹 중계와 접수 결과.
        settings: 실행 DB 설정.
        access: 합성 실행 허용 설정.
        experiment: 실행 소유자.
        evaluation_database: 격리 검사 DB.
        action: 취소 또는 세션 회수.

    Returns:
        None: 소비자의 종료/오류와 상세 제거를 확인한다.
    """
    client, admitted = web_job
    script = """
const originalFetch = globalThis.fetch;
globalThis.fetch = (path, init) => originalFetch(new URL(path, process.env.TEST_WEB_URL), {
  ...init, headers: { ...init.headers, Cookie: process.env.TEST_SESSION_COOKIE },
});
const { watchEvaluation } = await import(process.env.TEST_CONSUMER_MODULE);
await watchEvaluation(process.env.TEST_JOB_ID, new AbortController().signal, view => {
  console.log(JSON.stringify({ connection: view.connection, status: view.data?.run_status,
    hasData: Boolean(view.data), message: view.message }));
});
"""
    process = subprocess.Popen(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        env={
            **os.environ,
            "TEST_WEB_URL": str(client.base_url),
            "TEST_SESSION_COOKIE": client.headers["Cookie"],
            "TEST_JOB_ID": str(admitted.job_id),
            "TEST_CONSUMER_MODULE": Path("frontend/src/api/evaluation-events.ts")
            .resolve()
            .as_uri(),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            for _ in range(3):
                assert selector.select(timeout=5), "평가 화면 소비자가 연결되지 않았습니다."
                state = json.loads(process.stdout.readline())
                if state["connection"] == "connected":
                    break
            assert state["connection"] == "connected"
        if action == "cancel":
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
        else:
            with psycopg.connect(evaluation_database) as conn:
                conn.execute("DELETE FROM identity.sessions")
        output, errors = process.communicate(timeout=10)
        assert process.returncode == 0, errors.decode()
        final = json.loads(output.splitlines()[-1])
        if action == "cancel":
            assert final["connection"] == "ended"
            assert final["status"] == "CANCELLED"
        else:
            assert final["connection"] == "error"
            assert not final["hasData"]
            assert "로그인" in final["message"]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("action", ["cancel", "resume"])
def test_web_control_consumer_preserves_idempotent_command(web_job, evaluation_database, action):
    """실제 제어 소비자의 CSRF·판본·유실 응답 재전송을 검사한다.

    Args:
        web_job: 실제 세션과 웹 중계.
        evaluation_database: 격리 DB 연결.
        action: 취소 또는 검토 보류 해제.

    Returns:
        None: 상태 변경과 같은 키 재전송의 판본 보존을 확인한다.
    """
    client, admitted = web_job
    if action == "resume":
        with psycopg.connect(evaluation_database) as conn:
            conn.execute(
                "UPDATE evaluation.runs SET needs_review=true WHERE id=%s", (admitted.run_id,)
            )
    script = """
import assert from 'node:assert/strict';
const originalFetch = globalThis.fetch;
const keys = [];
globalThis.fetch = async (path, init = {}) => {
  const result = await originalFetch(new URL(path, process.env.TEST_WEB_URL), {
    ...init, headers: { ...init.headers, Cookie: process.env.TEST_SESSION_COOKIE,
      ...(init.method === 'POST' ? {Origin: 'https://app.example.invalid'} : {}) },
  });
  if (init.method === 'POST') {
    keys.push(init.headers['Idempotency-Key']);
    if (keys.length === 1) { await result.text(); throw new TypeError('lost response'); }
  }
  return result;
};
const { prepareRunCommand, sendRunCommand, RunControlError } = await import(process.env.TEST_MODULE);
const path = '/api/v1/platform/jobs/' + process.env.TEST_JOB_ID;
const snapshot = (await (await fetch(path)).json()).data;
const command = await prepareRunCommand(snapshot, process.env.TEST_ACTION, new AbortController().signal);
await assert.rejects(sendRunCommand(command, new AbortController().signal),
  error => error instanceof RunControlError && error.uncertain);
const first = (await (await fetch(path)).json()).data;
await sendRunCommand(command, new AbortController().signal);
const replayed = (await (await fetch(path)).json()).data;
assert.equal(first.version, snapshot.version + 1);
assert.equal(replayed.version, first.version);
assert.deepEqual(keys, [command.key, command.key]);
assert.equal(replayed.needs_review, false);
assert.equal(replayed.run_status, process.env.TEST_ACTION === 'cancel' ? 'CANCELLED' : 'QUEUED');
console.log('control replay verified');
"""
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        env={
            **os.environ,
            "TEST_WEB_URL": str(client.base_url),
            "TEST_SESSION_COOKIE": client.headers["Cookie"],
            "TEST_JOB_ID": str(admitted.job_id),
            "TEST_ACTION": action,
            "TEST_MODULE": Path("frontend/src/api/evaluation-controls.ts").resolve().as_uri(),
        },
        capture_output=True,
        timeout=15,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "control replay verified" in result.stdout


def test_web_control_rejects_csrf_origin_and_stale_version(web_job):
    """웹을 통과한 변경 요청도 실제 서버의 보호 조건을 충족해야 한다.

    Args:
        web_job: 실제 세션과 웹 중계.

    Returns:
        None: CSRF·Origin·If-Match 거부와 미변경 상태를 확인한다.
    """
    client, admitted = web_job
    token = client.get("/api/v1/platform/me").headers["X-CSRF-Token"]
    headers = {
        "Origin": "https://app.example.invalid",
        "X-CSRF-Token": token,
        "If-Match": '"1"',
        "Idempotency-Key": str(uuid4()),
    }
    url = f"/api/v1/platform/evaluations/runs/{admitted.run_id}/cancel"
    for changes, status in [
        ({"X-CSRF-Token": "wrong"}, 403),
        ({"Origin": "https://wrong.invalid"}, 403),
        ({"If-Match": '"2"'}, 412),
    ]:
        response = client.post(url, headers={**headers, **changes})
        assert response.status_code == status
    state = client.get(f"/api/v1/platform/jobs/{admitted.job_id}").json()["data"]
    assert state["run_status"] == "QUEUED" and state["version"] == 1


@pytest.fixture
def cs_web_job(cs_experiment, access, request):
    """기존 실제 웹 fixture에 고정 C/S 프로필을 접수한다.

    Args:
        cs_experiment: 접수 전 고정 합성 프로필.
        access: 웹과 worker가 공유하는 승인 설정.
        request: 프로필 준비 뒤 기존 웹 fixture를 실행한다.

    Returns:
        tuple: 실제 웹 HTTP 클라이언트와 접수 결과.
    """
    access.approved_config_hashes = frozenset({cs_experiment["config_hash"]})
    return request.getfixturevalue("web_job")


def test_summary_consumer_reads_actual_web_database_and_revocation(
    cs_web_job, settings, access, evaluation_database, registered_user
):
    """실제 TS→웹→플랫폼→DB에서 합성 집계와 승인·세션 철회를 검증한다.

    Args:
        cs_web_job: 실제 세션과 고정 C/S 실행을 가진 웹 중계.
        settings: worker DB 설정.
        access: 현재 승인 설정.
        evaluation_database: 검사 관리자 연결.
        registered_user: 철회할 합성 세션 사용자.

    Returns:
        None: 미보고·실행 후 집계·권한 오류·원문 비노출을 확인한다.
    """
    from backend.api.platform.evaluations.smoke_worker import dispatch_one

    client, admitted = cs_web_job
    script = """
const original = globalThis.fetch;
globalThis.fetch = (path, init) => original(new URL(path, process.env.TEST_WEB_URL), {
  ...init, headers: {...init.headers, Cookie: process.env.TEST_SESSION_COOKIE},
});
const {getEvaluationSummary} = await import(process.env.TEST_MODULE);
try { console.log(JSON.stringify(await getEvaluationSummary(process.env.TEST_RUN_ID, new AbortController().signal))); }
catch (error) { console.log(JSON.stringify({error: error.message})); }
"""

    def read_summary():
        """실제 TypeScript 소비자의 결과만 읽는다.

        Args:
            없음.

        Returns:
            dict: 집계 또는 소비자의 안전한 오류.
        """
        result = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
            env={
                **os.environ,
                "TEST_WEB_URL": str(client.base_url),
                "TEST_SESSION_COOKIE": client.headers["Cookie"],
                "TEST_RUN_ID": str(admitted.run_id),
                "TEST_MODULE": Path("frontend/src/api/evaluation-summary.ts").resolve().as_uri(),
            },
            capture_output=True,
            timeout=20,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    queued = read_summary()
    assert queued["targets_without_first_attempt"] == 3
    assert queued["first"]["tokens"]["input_tokens"]["total"] is None
    assert asyncio.run(dispatch_one(settings, access)) == admitted.run_id
    completed = read_summary()
    assert completed["status"] == "PARTIAL"
    assert completed["first"]["stored_attempt_count"] == 3
    assert completed["first"]["tokens"]["input_tokens"]["total"] == 21
    assert completed["first"]["schema_violation_rate"] == 0.5
    assert completed["first"]["tokens"]["output_tokens"]["total"] is None
    assert "raw_output" not in json.dumps(completed) and "target_ref" not in json.dumps(completed)
    access.approved_config_hashes = frozenset()
    assert "구성 승인" in read_summary()["error"]
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("DELETE FROM identity.sessions WHERE user_id=%s", (registered_user[1],))
    assert "로그인" in read_summary()["error"]
