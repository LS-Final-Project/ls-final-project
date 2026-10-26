import { test } from 'node:test';
import assert from 'node:assert/strict';
import { getEvaluationJob } from './evaluations.ts';

const jobId = '11111111-1111-4111-8111-111111111111';
const snapshot = {
  job_id: jobId, run_id: '22222222-2222-4222-8222-222222222222',
  owner: 'platform', kind: 'evaluation.run', status: 'RUNNING', run_status: 'RUNNING',
  needs_review: false, version: 2, last_event_sequence: 1,
};

test('평가 조회는 실제 세션 요청을 사용하고 제한된 필드만 반환한다', async t => {
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    assert.equal(url, `/api/v1/platform/jobs/${jobId}`);
    assert.equal(init.credentials, 'same-origin');
    assert.equal(init.cache, 'no-store');
    assert.ok(init.signal);
    return Response.json({ data: { ...snapshot, raw_output: '비공개' } });
  });
  assert.deepEqual(await getEvaluationJob(jobId, new AbortController().signal), snapshot);
});

test('잘못된 작업 ID는 서버에 요청하지 않는다', async t => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('호출 금지'); });
  await assert.rejects(getEvaluationJob('../me', new AbortController().signal), /올바른 작업 ID/);
  assert.equal(fetch.mock.callCount(), 0);
});

for (const [status, message] of [[401, '로그인'], [403, '평가자'], [404, '찾지 못'], [503, '비활성'], [500, '확인하지 못']] as const) {
  test(`${status} 응답은 성공으로 대체하지 않고 안전한 안내를 반환한다`, async t => {
    t.mock.method(globalThis, 'fetch', async () => new Response('민감한 내부 오류', { status }));
    await assert.rejects(getEvaluationJob(jobId, new AbortController().signal), error => {
      assert.ok(error instanceof Error);
      assert.match(error.message, new RegExp(message));
      assert.doesNotMatch(error.message, /민감/);
      return true;
    });
  });
}

for (const data of [null, { ...snapshot, job_id: snapshot.run_id }, { ...snapshot, run_status: 'UNKNOWN' }, { ...snapshot, version: 0 }, { ...snapshot, needs_review: 'false' }]) {
  test(`잘못된 스냅샷을 거부한다: ${JSON.stringify(data)}`, async t => {
    t.mock.method(globalThis, 'fetch', async () => Response.json({ data }));
    await assert.rejects(getEvaluationJob(jobId, new AbortController().signal), /상태 응답/);
  });
}

test('JSON이 아닌 응답의 원문은 오류 안내에 노출하지 않는다', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('<html>비공개 원문</html>'));
  await assert.rejects(getEvaluationJob(jobId, new AbortController().signal), /서버의 상태 응답/);
});

test('화면 이동에 따른 취소 신호를 실제 요청까지 전달한다', async t => {
  const controller = new AbortController();
  t.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => {
    controller.abort();
    init.signal!.throwIfAborted();
    return Response.json({ data: snapshot });
  });
  await assert.rejects(getEvaluationJob(jobId, controller.signal), { name: 'AbortError' });
});
