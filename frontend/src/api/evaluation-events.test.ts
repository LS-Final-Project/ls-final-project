import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nextEvaluationChange, watchEvaluation, type EvaluationView } from './evaluation-events.ts';

const job = '11111111-1111-4111-8111-111111111111';
const snapshot = { job_id: job, run_id: '22222222-2222-4222-8222-222222222222', owner: 'platform', kind: 'evaluation.run', status: 'RUNNING', run_status: 'RUNNING', needs_review: false, version: 2, last_event_sequence: 2 };
const encoder = new TextEncoder();

/** 합성 프레임을 만든다. Args: 순번, 선택 작업. Returns: SSE 프레임. */
function frame(sequence: number, jobId = job): string {
  return `id: ${sequence}\nevent: job.progress\ndata: ${JSON.stringify({ job_id: jobId, sequence, type: 'job.progress', payload: { state: 'FAILED', raw_output: '비공개' } })}\n\n`;
}

/** 분할 SSE 응답을 만든다. Args: 수신 조각. Returns: 스트림 응답. */
function stream(chunks: string[]): Response {
  return new Response(new ReadableStream({ start(controller) {
    for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
    controller.close();
  } }), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } });
}

test('heartbeat·분할 CRLF·중복을 처리하고 현재 작업 커서와 세션만 요청한다', async t => {
  const payload = ': heartbeat\r\n\r\n' + frame(2).replaceAll('\n', '\r\n') + frame(4);
  let connected = 0;
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    assert.equal(url, `/api/v1/platform/jobs/${job}/events?after=2`);
    assert.equal(init.credentials, 'same-origin');
    assert.deepEqual(init.headers, { Accept: 'text/event-stream' });
    return stream([...payload]);
  });
  await nextEvaluationChange(job, 2, new AbortController().signal, () => connected++);
  assert.equal(connected, 1);
});

test('이벤트 payload 대신 최신 스냅샷으로 완료를 확정하고 더 구독하지 않는다', async t => {
  const urls: string[] = [];
  let reads = 0;
  const views: EvaluationView[] = [];
  t.mock.method(globalThis, 'fetch', async (url: string) => {
    urls.push(url);
    if (url.includes('/events')) return stream([frame(8)]);
    reads++;
    return Response.json({ data: reads === 1 ? snapshot : { ...snapshot, status: 'SUCCEEDED', run_status: 'SUCCEEDED', last_event_sequence: 9 } });
  });
  await watchEvaluation(job, new AbortController().signal, view => views.push(view));
  assert.equal(urls.length, 3);
  assert.equal(views.at(-1)?.connection, 'ended');
  assert.equal(views.at(-1)?.data?.run_status, 'SUCCEEDED');
  assert.ok(!JSON.stringify(views).includes('비공개'));
});

for (const status of [400, 401, 403, 404, 503]) {
  test(`SSE ${status}은 재시도 없이 상세를 지우고 중단한다`, async t => {
    let calls = 0;
    const views: EvaluationView[] = [];
    t.mock.method(globalThis, 'fetch', async (url: string) => {
      calls++;
      return url.includes('/events') ? new Response('비공개 내부 오류', { status }) : Response.json({ data: snapshot });
    });
    await watchEvaluation(job, new AbortController().signal, view => views.push(view));
    assert.equal(calls, 2);
    assert.equal(views.at(-1)?.connection, 'error');
    assert.equal(views.at(-1)?.data, undefined);
    assert.ok(!JSON.stringify(views).includes('비공개'));
  });
}

for (const response of [() => stream([frame(3, snapshot.run_id)]), () => stream(['data: 비공개\n\n']), () => stream(['x'.repeat(65537)]), () => new Response('<html>비공개</html>')]) {
  test('다른 작업·잘못된 형식·큰 프레임·HTML은 실패로 유지한다', async t => {
    t.mock.method(globalThis, 'fetch', async () => response());
    await assert.rejects(nextEvaluationChange(job, 2, new AbortController().signal, () => {}), /실시간 응답/);
  });
}

for (const status of [204, 410, 200]) {
  test(`종료/보관 만료/EOF(${status}) 뒤 상태를 다시 조회해 종료를 복구한다`, async t => {
    let reads = 0;
    const views: EvaluationView[] = [];
    t.mock.method(globalThis, 'fetch', async (url: string) => {
      if (url.includes('/events')) return status === 200 ? stream([': heartbeat\n\n']) : new Response(null, { status });
      reads++;
      return Response.json({ data: reads === 1 ? snapshot : { ...snapshot, status: 'CANCELLED', run_status: 'CANCELLED' } });
    });
    await watchEvaluation(job, new AbortController().signal, view => views.push(view));
    assert.equal(reads, 2);
    assert.ok(views.some(view => view.connection === 'reconnecting' && !view.data));
    assert.equal(views.at(-1)?.data?.run_status, 'CANCELLED');
  });
}

test('연결 종료 후 세션 회수는 GET401로 확인하고 재시도를 중단한다', async t => {
  let calls = 0;
  const views: EvaluationView[] = [];
  t.mock.method(globalThis, 'fetch', async () => {
    calls++;
    if (calls === 1) return Response.json({ data: snapshot });
    if (calls === 2) return stream([]);
    return new Response(null, { status: 401 });
  });
  await watchEvaluation(job, new AbortController().signal, view => views.push(view));
  assert.equal(calls, 3);
  assert.match(views.at(-1)?.message || '', /로그인/);
  assert.equal(views.at(-1)?.data, undefined);
});

test('끊어진 스트림은 스냅샷이 성공해도 최대3회만 재접속한다', async t => {
  let subscriptions = 0;
  const views: EvaluationView[] = [];
  t.mock.method(globalThis, 'fetch', async (url: string) => {
    if (!url.includes('/events')) return Response.json({ data: snapshot });
    subscriptions++;
    return stream([]);
  });
  await watchEvaluation(job, new AbortController().signal, view => views.push(view));
  assert.equal(subscriptions, 4);
  assert.equal(views.filter(view => view.connection === 'reconnecting').length, 3);
  assert.equal(views.at(-1)?.connection, 'error');
});

test('재접속 대기 중 화면 이탈은 후속 요청과 갱신을 취소한다', async t => {
  const controller = new AbortController();
  let calls = 0;
  const views: EvaluationView[] = [];
  t.mock.method(globalThis, 'fetch', async () => { calls++; throw new TypeError('network'); });
  await watchEvaluation(job, controller.signal, view => {
    views.push(view);
    if (view.connection === 'reconnecting') controller.abort();
  });
  assert.equal(calls, 1);
  assert.equal(views.at(-1)?.connection, 'reconnecting');
});

test('헤더15초와 유휴45초 제한은 요청을 실제 취소한다', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let headerAborted = false;
  t.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal!.addEventListener('abort', () => { headerAborted = true; reject(init.signal!.reason); }, { once: true });
  }));
  const header = nextEvaluationChange(job, 2, new AbortController().signal, () => {});
  t.mock.timers.tick(15000);
  await assert.rejects(header, /연결이 끊겼/);
  assert.ok(headerAborted);

  let idleAborted = false;
  let connected!: () => void;
  const ready = new Promise<void>(resolve => { connected = resolve; });
  t.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => new Response(new ReadableStream({ start(controller) {
    init.signal!.addEventListener('abort', () => { idleAborted = true; controller.error(init.signal!.reason); }, { once: true });
  } }), { headers: { 'Content-Type': 'text/event-stream' } }));
  const idle = nextEvaluationChange(job, 2, new AbortController().signal, connected);
  await ready;
  t.mock.timers.tick(45000);
  await assert.rejects(idle, /연결이 끊겼/);
  assert.ok(idleAborted);
});

test('보관 만료 뒤에는 새 스냅샷의 커서로 구독한다', async t => {
  const positions: string[] = [];
  let reads = 0;
  t.mock.method(globalThis, 'fetch', async (url: string) => {
    if (url.includes('/events')) {
      positions.push(url.split('after=')[1]);
      return positions.length === 1 ? new Response(null, { status: 410 }) : stream([frame(7)]);
    }
    reads++;
    return Response.json({ data: { ...snapshot, last_event_sequence: reads === 1 ? 2 : 6,
      status: reads === 3 ? 'SUCCEEDED' : 'RUNNING', run_status: reads === 3 ? 'SUCCEEDED' : 'RUNNING' } });
  });
  await watchEvaluation(job, new AbortController().signal, () => {});
  assert.deepEqual(positions, ['2', '6']);
});

test('깨진 UTF-8은 네트워크 장애로 재시도하지 않고 즉시 중단한다', async t => {
  let calls = 0;
  const views: EvaluationView[] = [];
  t.mock.method(globalThis, 'fetch', async (url: string) => {
    calls++;
    return url.includes('/events')
      ? new Response(new Uint8Array([0xff]), { headers: { 'Content-Type': 'text/event-stream' } })
      : Response.json({ data: snapshot });
  });
  await watchEvaluation(job, new AbortController().signal, view => views.push(view));
  assert.equal(calls, 2);
  assert.equal(views.at(-1)?.connection, 'error');
  assert.equal(views.at(-1)?.data, undefined);
  assert.match(views.at(-1)?.message || '', /실시간 응답/);
});
