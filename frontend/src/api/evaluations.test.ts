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

// 공개 요약 소비자도 실제 fetch 경계에서 검증한다.
import { getEvaluationSummary } from './evaluation-summary.ts';
import { summaryFixture, syntheticQualityFixture } from '../test/evaluation-summary.ts';

test('집계는 현재 세션으로 조회하고 원문·추가 필드는 투영하지 않는다', async t => {
  const data = summaryFixture();
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    assert.equal(url, `/api/v1/platform/evaluations/runs/${data.run_id}/summary`);
    assert.equal(init.credentials, 'same-origin'); assert.equal(init.cache, 'no-store');
    return Response.json({ data: { ...data, raw_output: 'secret', first: { ...data.first, gold: 'secret' } } });
  });
  assert.deepEqual(await getEvaluationSummary(data.run_id, new AbortController().signal), data);
});

for (const change of ['run', 'origin', 'version', 'count', 'null', 'rate', 'date']) {
  test(`집계의 잘못된 ${change} 응답을 거부한다`, async t => {
    const data = summaryFixture();
    if (change === 'run') data.run_id = jobId;
    if (change === 'origin') Object.assign(data, { data_origin: 'real' });
    if (change === 'version') Object.assign(data, { report_version: 'unknown' });
    if (change === 'count') data.first.stored_attempt_count = -1;
    if (change === 'null') data.first.tokens.output_tokens.total = 0;
    if (change === 'rate') data.retry.schema_violation_rate = 0;
    if (change === 'date') data.generated_at = 'not-date';
    t.mock.method(globalThis, 'fetch', async () => Response.json({ data }));
    await assert.rejects(getEvaluationSummary(snapshot.run_id, new AbortController().signal), /집계 응답/);
  });
}

for (const status of [401, 403, 404, 409, 503, 502]) {
  test(`집계 ${status} 실패는 합성 성공으로 대체하지 않는다`, async t => {
    t.mock.method(globalThis, 'fetch', async () => new Response('secret', { status }));
    await assert.rejects(getEvaluationSummary(snapshot.run_id, new AbortController().signal), error => {
      assert.ok(error instanceof Error); assert.doesNotMatch(error.message, /secret/); return true;
    });
  });
}

test('집계 요청의 취소를 전달한다', async t => {
  const controller = new AbortController();
  t.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => {
    controller.abort(); init.signal!.throwIfAborted(); return Response.json({});
  });
  await assert.rejects(getEvaluationSummary(snapshot.run_id, controller.signal), { name: 'AbortError' });
});

test('비용은 십진 문자열을 그대로 보존하고 구버전의 누락을 미연결로 구별한다', async t => {
  const data = summaryFixture();
  data.first.cost_usd = { reported_count: 2, missing_count: 1, known_sum: '9007199254740993.000001', total: null, pricing_version_count: 2 };
  t.mock.method(globalThis, 'fetch', async () => Response.json({ data }));
  const result = await getEvaluationSummary(data.run_id, new AbortController().signal);
  assert.deepEqual(result.first.cost_usd, data.first.cost_usd);
  delete data.first.cost_usd;
  delete data.retry.cost_usd;
  assert.equal((await getEvaluationSummary(data.run_id, new AbortController().signal)).first.cost_usd, null);
});

for (const badCost of [0, '0', 'NaN', '-1.00', '1e-6', '0.00\n', '0'.repeat(129)]) {
  test(`잘못된 비용 문자열을 거부한다: ${JSON.stringify(badCost)}`, async t => {
    const data = summaryFixture();
    data.first.cost_usd = { reported_count: 1, missing_count: 2, known_sum: '0.000001', total: null, pricing_version_count: 1 };
    if (badCost === '0') Object.assign(data.first, { cost_usd: '0' });
    else Object.assign(data.first.cost_usd, { known_sum: badCost });
    t.mock.method(globalThis, 'fetch', async () => Response.json({ data }));
    await assert.rejects(getEvaluationSummary(data.run_id, new AbortController().signal), /집계 응답/);
  });
}

for (const change of [{ total: '0.000001' }, { pricing_version_count: 0 }, { pricing_version_count: 2 }, { missing_count: 0 }]) {
  test(`모순된 비용 보고 범위를 거부한다: ${JSON.stringify(change)}`, async t => {
    const data = summaryFixture();
    data.first.cost_usd = { reported_count: 1, missing_count: 2, known_sum: '0.000001', total: null, pricing_version_count: 1, ...change };
    t.mock.method(globalThis, 'fetch', async () => Response.json({ data }));
    await assert.rejects(getEvaluationSummary(data.run_id, new AbortController().signal), /집계 응답/);
  });
}

test('합성 품질은 누락·null을 미저장으로 통과시키고 저장값은 허용 필드만 투영한다', async t => {
  const data = summaryFixture();
  t.mock.method(globalThis, 'fetch', async () => Response.json({ data }));
  assert.equal((await getEvaluationSummary(data.run_id, new AbortController().signal)).synthetic_quality, null);
  delete data.synthetic_quality;
  assert.equal((await getEvaluationSummary(data.run_id, new AbortController().signal)).synthetic_quality, null);
  const quality = syntheticQualityFixture();
  data.synthetic_quality = { ...quality, cases: ['secret'], per_class: { ...quality.per_class, DEFECT: { ...quality.per_class.DEFECT, gold: 'secret' } } } as typeof quality;
  assert.deepEqual((await getEvaluationSummary(data.run_id, new AbortController().signal)).synthetic_quality, quality);
});

const qualityChanges: Record<string, (q: Record<string, unknown>) => void> = {
  metric: q => { q.metric = 'category'; },
  gate: q => { q.quality_gate_evaluated = true; },
  origin: q => { q.data_origin = 'real'; },
  sha: q => { q.gold_sha256 = 'A'.repeat(64); },
  status: q => { q.run_status_at_scoring = 'DONE'; },
  scored: q => { q.scored_target_count = 4; },
  correct: q => { q.correct = 3; },
  ratio: q => { q.accuracy = 1.5; },
  string_number: q => { q.macro_f1 = '0.5'; },
  outcome_key: q => { q.outcomes = { ...(q.outcomes as object), EXTRA: 0 }; },
  outcome_sum: q => { q.outcomes = { ...(q.outcomes as object), VALID: 1 }; },
  class_key: q => { q.per_class = { DEFECT: (q.per_class as Record<string, unknown>).DEFECT }; },
  support_sum: q => { const c = q.per_class as Record<string, Record<string, unknown>>; c.DEFECT.support = 2; },
  class_ratio: q => { const c = q.per_class as Record<string, Record<string, unknown>>; c.NON_DEFECT.f1 = -0.1; },
  array: q => { q.per_class = []; },
};
for (const [name, change] of Object.entries(qualityChanges)) {
  test(`잘못된 합성 품질 ${name}을 집계 오류로 거부한다`, async t => {
    const data = summaryFixture();
    const quality = syntheticQualityFixture() as unknown as Record<string, unknown>;
    change(quality);
    Object.assign(data, { synthetic_quality: quality });
    t.mock.method(globalThis, 'fetch', async () => Response.json({ data }));
    await assert.rejects(getEvaluationSummary(data.run_id, new AbortController().signal), /집계 응답/);
  });
}

test('비교 키는 누락·null을 미제공으로 통과시키고 형식이 다르면 거부한다', async t => {
  const data = summaryFixture();
  t.mock.method(globalThis, 'fetch', async () => Response.json({ data }));
  assert.equal((await getEvaluationSummary(data.run_id, new AbortController().signal)).comparison_key, 'c'.repeat(64));
  data.comparison_key = null;
  assert.equal((await getEvaluationSummary(data.run_id, new AbortController().signal)).comparison_key, null);
  delete data.comparison_key;
  assert.equal((await getEvaluationSummary(data.run_id, new AbortController().signal)).comparison_key, null);
  for (const bad of ['C'.repeat(64), 'c'.repeat(63), 1]) {
    Object.assign(data, { comparison_key: bad });
    await assert.rejects(getEvaluationSummary(data.run_id, new AbortController().signal), /집계 응답/);
  }
});
