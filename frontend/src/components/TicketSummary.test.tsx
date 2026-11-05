import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import TicketSummary from './TicketSummary';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const record = (over: Record<string, unknown> = {}) => ({ id: 's1', summary_ko: '포장 라인 HMI 터치가 반응하지 않아 라인이 멈춘 상태입니다.', created_at: '2026-10-28T00:02:00Z', requested_model: 'm', model_version: 'm1', prompt_version: 'v1', input_version: 'cs-summary-input-v1', stale: false, ...over });
const view = (summary: unknown, attempt: unknown = null) => ({ data: { ticket_id: 't1', provenance: 'ai_draft', summary, latest_attempt: attempt } });
const cs = { data: { id: '00000000-0000-4000-8000-000000000001', display_name: '김지원', dept: null, roles: ['cs_agent'] } };
const show = (canWrite = true) => { setApiMode('live'); return renderAt('/t', '/t', <TicketSummary ticketId="t1" canWrite={canWrite} />); };
async function withSession() { const { request } = await import('../api/client'); await request('/api/v1/platform/me'); }
type Post = (init: RequestInit, path: string) => Response;
const serve = (read: () => Response, post: Post = () => jsonResponse(201, view(record()))) => vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
  const path = String(url);
  if (path.endsWith('/platform/me')) return new Response(JSON.stringify(cs), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' } });
  return init?.method === 'POST' ? post(init, path) : read();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); clearSessionCsrfToken(); });

test('저장된 요약을 AI 작성·판정 아님 표시와 함께 보이고, 오래된 요약은 다시 만들 수 있다', async () => {
  vi.stubGlobal('fetch', serve(() => jsonResponse(200, view(record()))));
  show();
  expect(await screen.findByText(/포장 라인 HMI 터치가 반응하지 않아/)).toBeTruthy();
  expect(screen.getByText(/판정이 아님/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /요약/ })).toBeNull();
  cleanup();
  vi.stubGlobal('fetch', serve(() => jsonResponse(200, view(record({ stale: true })))));
  show();
  expect(await screen.findByText(/오래됨/)).toBeTruthy();
  expect(screen.getByRole('button', { name: '요약 다시 만들기' })).toBeTruthy();
});

test('요약이 없으면 직전 실패 이유를 보이고 누르면 한 번만 만든 뒤 최신 요약을 읽는다', async () => {
  let created = false;
  const posts: string[] = [];
  const fetch = serve(() => jsonResponse(200, created ? view(record()) : view(null, { id: 'a', outcome: 'INVALID_OUTPUT', error_code: 'CS_SUMMARY_UNGROUNDED', created_at: '2026-10-28T00:00:00Z' })),
    (init, path) => { posts.push(`${init.method} ${path}`); created = true; return jsonResponse(201, view(record())); });
  vi.stubGlobal('fetch', fetch);
  setApiMode('live');
  await withSession();
  show();
  expect(await screen.findByText(/직전 시도 실패: 원문에 없는 코드·숫자가 들어감/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '요약 만들기' }));
  expect(await screen.findByText('요약을 만들었습니다.')).toBeTruthy();
  await screen.findByText(/포장 라인 HMI 터치가 반응하지 않아/);
  expect(posts).toEqual(['POST /api/v1/cs/tickets/t1/summary']);
});

test('모델 실패(502)는 이유를 보이고, 알 수 없는 오류는 다시 보내지 않고 재조회한다', async () => {
  let reply: Response = jsonResponse(502, { error: { code: 'CS_SUMMARY_FAILED', message: '실패', retryable: false }, meta: { request_id: 'r' } });
  const posts: number[] = [];
  const fetch = serve(() => jsonResponse(200, view(null)), () => { posts.push(1); return reply.clone(); });
  vi.stubGlobal('fetch', fetch);
  setApiMode('live');
  await withSession();
  show();
  fireEvent.click(await screen.findByRole('button', { name: '요약 만들기' }));
  await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
  reply = jsonResponse(503, errorBody('UNAVAILABLE', '장애'));
  fireEvent.click(screen.getByRole('button', { name: '요약 만들기' }));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('같은 요청을 다시 보내지 않고'));
  expect(posts).toHaveLength(2);
});

test('쓰기가 막힌 상태에는 단추가 없고, 기능이 꺼진 배포(404)와 읽기 오류는 안내만 한다', async () => {
  vi.stubGlobal('fetch', serve(() => jsonResponse(200, view(null))));
  show(false);
  expect(await screen.findByText('아직 만들지 않았습니다')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '요약 만들기' })).toBeNull();
  cleanup();
  vi.stubGlobal('fetch', serve(() => jsonResponse(404, errorBody('NOT_FOUND', '없음'))));
  show();
  expect(await screen.findByText('요약 기능이 이 배포에서 꺼져 있습니다')).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', serve(() => jsonResponse(500, errorBody('INTERNAL', '오류'))));
  show();
  expect(await screen.findByText('요약을 불러오지 못했습니다')).toBeTruthy();
});
