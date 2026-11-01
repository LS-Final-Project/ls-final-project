import { useState } from 'react';
import TicketDetail from '../pages/TicketDetail';
import examples from '../mocks/generated/cs.json';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import CsAnalysisAction from './CsAnalysisAction';
import { getAnalysisJob, startAnalysis } from '../api/cs-analysis';
import { setApiMode } from '../api/client';
import type { Identity } from '../api/platform';
import { jsonResponse, renderAt } from '../test/render';
const A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const T = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const J = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const user = { id: A, roles: ['cs_agent'] } as Identity;
let actor = A;
let roles = ['cs_agent'];
let token: string | null = 'test-token';
const accepted = { data: { job_id: J, status_url: `/api/v1/platform/jobs/${J}`, status: 'QUEUED', created: true } };
function serve(post: () => Response | Promise<Response> = () => jsonResponse(202, accepted), status = 'SUCCEEDED') {
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url).endsWith('/me')) return new Response(JSON.stringify({ data: { id: actor, roles } }), { headers: { 'Content-Type': 'application/json', ...(token ? { 'X-CSRF-Token': token } : {}) } });
    if (init?.method === 'POST') return post();
    return jsonResponse(200, { data: { job_id: J, ticket_id: T, owner: 'cs', kind: 'cs.analyze', status, version: 1, last_event_sequence: 1 } });
  });
  vi.stubGlobal('fetch', fetch); return fetch;
}
const posts = (fetch: ReturnType<typeof serve>) => fetch.mock.calls.filter(([, init]) => init?.method === 'POST');
function setup(extra = {}) {
  setApiMode('live');
  const done = vi.fn();
  const props = { user, ticketId: T, closed: false, hasAnalysis: false, onComplete: done, ...extra };
  const result = renderAt('/', '/', <CsAnalysisAction key={`${props.user.id}:${props.ticketId}`} {...props} />);
  return { ...result, done, props };
}
const confirm = () => fireEvent.click(screen.getByRole('checkbox'));
const start = () => fireEvent.click(screen.getByRole('button', { name: 'AI 분석 요청' }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); actor = A; roles = ['cs_agent']; token = 'test-token'; });

test('명시 비용 확인 뒤 한 번 접수하고 문의별 작업 완료를 읽어 결과를 갱신한다', async () => {
  const fetch = serve(); const { done } = setup();
  expect(screen.getByText(/모델 호출 비용/)).toBeTruthy();
  expect(posts(fetch)).toHaveLength(0);
  expect((screen.getByRole('button', { name: 'AI 분석 요청' }) as HTMLButtonElement).disabled).toBe(true);
  confirm(); const button = screen.getByRole('button', { name: 'AI 분석 요청' }); fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect(done).toHaveBeenCalledTimes(1));
  expect(posts(fetch)).toHaveLength(1);
  expect(posts(fetch)[0][1]?.body).toBeUndefined();
  expect(posts(fetch)[0][1]?.headers).toMatchObject({ 'X-CSRF-Token': 'test-token' });
  expect(screen.getByRole('status').textContent).toContain('분류와 초안');
});

test('결과불명은 동일키로만 명시 재확인하고 새 분석을 자동 접수하지 않는다', async () => {
  let calls = 0;
  const fetch = serve(() => ++calls === 1 ? jsonResponse(503, {}) : jsonResponse(202, accepted));
  setup(); confirm(); start();
  await screen.findByRole('button', { name: '같은 분석 요청 다시 확인' });
  expect(posts(fetch)).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '같은 분석 요청 다시 확인' }));
  await screen.findByRole('status');
  expect(posts(fetch)).toHaveLength(2);
  expect(posts(fetch)[0][1]?.headers).toEqual(posts(fetch)[1][1]?.headers);
});

test('불명 요청 후 계정이 바뀌면 이전 요청 POST를 차단한다', async () => {
  const fetch = serve(() => jsonResponse(503, {})); setup(); confirm(); start();
  await screen.findByRole('button', { name: '같은 분석 요청 다시 확인' }); actor = B;
  fireEvent.click(screen.getByRole('button', { name: '같은 분석 요청 다시 확인' }));
  await screen.findByText(/계정이 변경되었습니다/);
  expect(posts(fetch)).toHaveLength(1);
});

test.each(['CS_JOB_IN_PROGRESS', 'IDEMPOTENCY_CONFLICT'])('%s는 새 키 자동 재접수 없이 차단한다', async code => {
  const fetch = serve(() => jsonResponse(409, { error: { code, message: 'conflict' } })); setup(); confirm(); start();
  await screen.findByText(/자동으로 재접수하지 않습니다/);
  expect(posts(fetch)).toHaveLength(1);
  expect((screen.getByRole('button', { name: 'AI 분석 요청' }) as HTMLButtonElement).disabled).toBe(true);
});

test.each(['FAILED', 'CANCELLED', 'PARTIAL'])('작업 %s도 결과 조회를 갱신하며 성공으로 표현하지 않는다', async status => {
  serve(undefined, status); const { done } = setup(); confirm(); start();
  await waitFor(() => expect(done).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('status').textContent).not.toContain('작업 완료');
});

test.each(['closed', 'manager', 'mock'])('%s에서는 분석을 요청하지 않는다', mode => {
  const fetch = serve(); setup(mode === 'closed' ? { closed: true } : mode === 'manager' ? { user: { ...user, roles: ['manager'] } } : {});
  if (mode === 'mock') { cleanup(); setApiMode('mock'); renderAt('/', '/', <CsAnalysisAction user={user} ticketId={T} closed={false} hasAnalysis={false} onComplete={() => undefined} />); }
  expect((screen.getByRole('button', { name: 'AI 분석 요청' }) as HTMLButtonElement).disabled).toBe(true);
  expect(posts(fetch)).toHaveLength(0);
});

test('현재 세션에 CSRF 또는 cs_agent가 없으면 POST하지 않는다', async () => {
  const fetch = serve(); setApiMode('live'); token = null;
  await expect(startAnalysis({ actorId: A, ticketId: T, key: 'test-key-1' }, new AbortController().signal)).rejects.toThrow('세션 보안');
  token = 'token'; roles = ['manager'];
  await expect(startAnalysis({ actorId: A, ticketId: T, key: 'test-key-1' }, new AbortController().signal)).rejects.toThrow('C/S 담당자');
  expect(posts(fetch)).toHaveLength(0);
});

test('다른 문의의 job 응답은 사용하지 않는다', async () => {
  serve();
  await expect(getAnalysisJob(A, B, J, new AbortController().signal)).rejects.toThrow('요청한 문의');
});

test('페이지 이탈은 진행중 접수 응답과 결과 갱신을 취소한다', async () => {
  let finish!: (value: Response) => void;
  const fetch = serve(() => new Promise(resolve => { finish = resolve; }));
  const { unmount, done } = setup(); confirm(); start();
  await waitFor(() => expect(posts(fetch)).toHaveLength(1));
  const signal = posts(fetch)[0][1]!.signal!; unmount();
  expect(signal.aborted).toBe(true); finish(jsonResponse(202, accepted));
  await Promise.resolve(); expect(done).not.toHaveBeenCalled();
});

test('폴링 권한 만료는 새 접수와 자동 재조회를 막는다', async () => {
  const fetch = serve();
  fetch.mockImplementation(async (url, init) => {
    if (String(url).endsWith('/me')) return new Response(JSON.stringify({ data: { id: A, roles } }), { headers: { 'X-CSRF-Token': 'token' } });
    if (init?.method === 'POST') return jsonResponse(202, accepted);
    return jsonResponse(401, { error: { code: 'AUTH_REQUIRED', message: '세션 만료' } });
  });
  setup(); confirm(); start();
  await screen.findByRole('alert');
  expect(screen.queryByRole('button', { name: '분석 상태 다시 조회' })).toBeNull();
  expect((screen.getByRole('button', { name: 'AI 분석 요청' }) as HTMLButtonElement).disabled).toBe(true);
  expect(posts(fetch)).toHaveLength(1);
});

test('통신 실패와 손상된 접수 응답은 같은 키 확인 대상으로 보존한다', async () => {
  let count = 0;
  const fetch = serve(() => { if (++count === 1) throw new TypeError('offline'); return jsonResponse(202, { data: { ...accepted.data, status_url: 'https://invalid.example/' } }); });
  setup(); confirm(); start();
  await screen.findByRole('button', { name: '같은 분석 요청 다시 확인' });
  fireEvent.click(screen.getByRole('button', { name: '같은 분석 요청 다시 확인' }));
  await screen.findByRole('button', { name: '같은 분석 요청 다시 확인' });
  expect(posts(fetch)).toHaveLength(2);
  expect(posts(fetch)[0][1]?.headers).toEqual(posts(fetch)[1][1]?.headers);
  expect(fetch.mock.calls.some(([url]) => String(url).includes('invalid.example'))).toBe(false);
});


test('계정·문의 변경은 이전 확인과 늦은 접수 응답을 새 작업에 적용하지 않는다', async () => {
  let finish!: (value: Response) => void;
  const fetch = serve(() => new Promise(resolve => { finish = resolve; }));
  setApiMode('live');
  const done = vi.fn();
  function Session() {
    const [next, setNext] = useState(false);
    return <><button onClick={() => { actor = B; setNext(true); }}>계정과 문의 변경</button><CsAnalysisAction key={next ? B : A} user={{ ...user, id: next ? B : A }} ticketId={next ? B : T} closed={false} hasAnalysis={false} onComplete={done} /></>;
  }
  renderAt('/', '/', <Session />); confirm(); start();
  await waitFor(() => expect(posts(fetch)).toHaveLength(1));
  fireEvent.click(screen.getByRole('button', { name: '계정과 문의 변경' }));
  finish(jsonResponse(202, accepted));
  await Promise.resolve();
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  expect(screen.queryByRole('status')).toBeNull();
  expect(done).not.toHaveBeenCalled();
});

test('문의 처리 화면의 명시 접수 완료는 분석·문의·업무흐름을 다시 읽고 초안 부재를 알린다', async () => {
  const source = examples.find(item => item.path === '/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222')!.body;
  let finished = false;
  const fetch = serve();
  fetch.mockImplementation(async (url, init) => {
    const path = String(url);
    if (path.endsWith('/me')) return new Response(JSON.stringify({ data: { id: A, roles } }), { headers: { 'X-CSRF-Token': 'test-token' } });
    if (init?.method === 'POST') return jsonResponse(202, accepted);
    if (path.includes('/platform/jobs/')) { finished = true; return jsonResponse(200, { data: { job_id: J, ticket_id: T, owner: 'cs', kind: 'cs.analyze', status: 'SUCCEEDED' } }); }
    if (path.endsWith('/analysis')) return jsonResponse(200, { data: { fields: {}, drafts: [], latest_valid_draft_id: null, latest_run: finished ? { outcome: 'VALID', created_at: '2026-11-01T00:00:00Z' } : null } });
    if (path.endsWith('/workflow')) return jsonResponse(200, { data: { version: 1, transfers: [], dept_results: [] } });
    return jsonResponse(200, { ...source, data: { ...(source as { data: object }).data, workflow_state: 'RECEIVED' } });
  });
  setApiMode('live'); renderAt(`/cs/tickets/${T}`, '/cs/tickets/:ticketId', <TicketDetail />);
  await screen.findByRole('button', { name: 'AI 분석 요청' });
  const calls = (suffix: string) => fetch.mock.calls.filter(([url]) => String(url).endsWith(suffix)).length;
  const before = calls('/workflow');
  confirm(); start();
  await screen.findByText(/분류 결과는 있지만 유효한 초안이 없습니다/);
  expect(calls('/workflow')).toBeGreaterThan(before);
  expect(calls('/analysis')).toBeGreaterThan(1);
  expect(calls(`/tickets/${T}`)).toBeGreaterThan(1);
  expect(posts(fetch)).toHaveLength(1);
});
