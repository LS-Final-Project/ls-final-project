import { test } from 'node:test';
import assert from 'node:assert/strict';
import { prepareRunCommand, sendRunCommand, loadRunCommand, saveRunCommand, RunControlError } from './evaluation-controls.ts';
import type { JobStatus } from './evaluations.ts';

const snapshot: JobStatus = { job_id: '11111111-1111-4111-8111-111111111111', run_id: '22222222-2222-4222-8222-222222222222', owner: 'platform', kind: 'evaluation.run', status: 'RUNNING', run_status: 'RUNNING', needs_review: true, version: 2, last_event_sequence: 2 };
const command = { runId: snapshot.run_id, jobId: snapshot.job_id, action: 'resume' as const, etag: '"2"', key: '33333333-3333-4333-8333-333333333333' };
/** 실행 응답을 만든다. Args: 상태 필드 덮어쓰기. Returns: 합성 응답. */
function runResult(extra = {}) { return Response.json({ data: { ...snapshot, status: 'RUNNING', ...extra } }, { headers: { ETag: '"2"' } }); }

test('최신 실행 판본을 조회한 뒤 고정 멱등 명령만 준비한다', async t => {
  const calls: string[] = [];
  t.mock.method(globalThis, 'fetch', async (url: string) => { calls.push(url); return runResult(); });
  const result = await prepareRunCommand(snapshot, 'resume', new AbortController().signal);
  assert.equal(result.etag, '"2"'); assert.equal(result.action, 'resume'); assert.ok(result.key);
  assert.deepEqual(calls, [`/api/v1/platform/evaluations/runs/${snapshot.run_id}`]);
});

for (const extra of [{ version: 3 }, { job_id: snapshot.run_id }, { status: 'CANCELLED' }, { needs_review: false }]) {
  test('다른 판본·대상·종료·보류 아닌 실행의 재개 준비를 거부한다', async t => {
    t.mock.method(globalThis, 'fetch', async () => runResult(extra));
    await assert.rejects(prepareRunCommand(snapshot, 'resume', new AbortController().signal));
  });
}

test('신선한 세션 CSRF와 동일 키·판본으로 본문 없이 전송한다', async t => {
  const requests: RequestInit[] = [];
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    if (url.endsWith('/me')) return new Response('{}', { headers: { 'X-CSRF-Token': 'current-session' } });
    requests.push(init); return runResult();
  });
  await sendRunCommand(command, new AbortController().signal);
  await sendRunCommand(command, new AbortController().signal);
  assert.deepEqual(requests[0].headers, { 'X-CSRF-Token': 'current-session', 'If-Match': '"2"', 'Idempotency-Key': command.key });
  assert.deepEqual(requests[0].headers, requests[1].headers);
  assert.equal(requests[0].body, undefined); assert.equal(requests[0].credentials, 'same-origin');
});

test('세션 정보 확인에 실패하면 변경 요청을 보내지 않는다', async t => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response(null, { status: 401 }); });
  await assert.rejects(sendRunCommand(command, new AbortController().signal), /로그인/);
  assert.equal(calls, 1);
});

for (const status of [401, 403, 409, 412]) {
  test(`HTTP${status}는 성공이나 자동 재시도로 숨기지 않는다`, async t => {
    t.mock.method(globalThis, 'fetch', async (url: string) => url.endsWith('/me')
      ? new Response('{}', { headers: { 'X-CSRF-Token': 'current' } }) : new Response('비공개', { status }));
    await assert.rejects(sendRunCommand(command, new AbortController().signal), error => {
      assert.ok(error instanceof Error); assert.ok(!(error instanceof RunControlError && error.uncertain));
      assert.ok(!error.message.includes('비공개')); return true;
    });
  });
}

for (const failure of ['network', '502', 'invalid'] as const) {
  test(`${failure} 결과는 불명확 상태로 보존한다`, async t => {
    t.mock.method(globalThis, 'fetch', async (url: string) => {
      if (url.endsWith('/me')) return new Response('{}', { headers: { 'X-CSRF-Token': 'current' } });
      if (failure === 'network') throw new TypeError('private network data');
      if (failure === '502') return new Response('비공개', { status: 502 });
      return Response.json({ data: { raw_output: '비공개' } });
    });
    await assert.rejects(sendRunCommand(command, new AbortController().signal), error => error instanceof RunControlError && error.uncertain && !error.message.includes('비공개'));
  });
}

test('재확인 명령은 탭 저장소에서 같은 키·판본으로 복원되고 CSRF는 저장하지 않는다', () => {
  const values = new Map<string, string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value) };
  saveRunCommand({ ...command, csrf: 'private-token' } as typeof command, storage);
  assert.deepEqual(loadRunCommand(command.jobId, storage), command);
  assert.equal(loadRunCommand(snapshot.run_id, storage), undefined);
  assert.ok(!/csrf|private-token/i.test(JSON.stringify([...values])));
  values.set(`evaluation-command:${command.jobId}`, '{"action":"delete"}');
  assert.throws(() => loadRunCommand(command.jobId, storage));
});
