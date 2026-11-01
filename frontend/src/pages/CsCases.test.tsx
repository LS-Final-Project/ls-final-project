// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import CsCases from './CsCases';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });
const R1 = 'dddddddd-dddd-4ddd-8ddd-ddddddddddd1';
const R2 = 'dddddddd-dddd-4ddd-8ddd-ddddddddddd2';
const T1 = '22222222-2222-4222-8222-222222222221';
const T2 = '22222222-2222-4222-8222-222222222222';
const T3 = '22222222-2222-4222-8222-222222222223';
const summary = (id: string, problem: string, over: Record<string, unknown> = {}) => ({ id, request_type: 'FAULT', product_code: 'TRF-OIL-1000', problem_statement: problem, status: 'DRAFT', version: 3, evidence_count: 2, cs_owner: null, ...over });
const row = (ticket: string, ext: string, over: Record<string, unknown> = {}) => ({ ticket_id: ticket, ticket_external_id: ext, case_id: `c-${ext}`, case_external_id: `RC-${ext}`, customer_external_id: `CUST-${ext}`, product_code: 'TRF-OIL-1000', symptom: 'Oil leakage at gasket', root_cause_type: 'WEAR_PART', root_cause: 'Gasket damage', action_taken: 'Replace gasket', resolved_by_dept: 'FIELD_SVC', known_at: '2026-10-26T00:00:00Z', ...over });
const ev = (id: string, ticket: string, ext: string, over: Record<string, unknown> = {}) => ({ id, ticket_id: ticket, ticket_external_id: ext, case_id: `c-${ext}`, case_external_id: `RC-${ext}`, added_by: '11111111-1111-4111-8111-111111111111', added_at: '2026-10-26T14:10:00Z', removed_by: null, removed_at: null, removal_reason: null, ...over });
type Post = { url: string; body: Record<string, unknown> };

function server(opts: { items?: unknown[]; comparison?: unknown[]; hidden?: number; evidence?: unknown[]; post?: () => Response; listCalls?: string[] } = {}) {
  const posts: Post[] = [];
  const items = opts.items ?? [summary(R1, '가스켓 누유'), summary(R2, '인버터 알람 반복', { product_code: 'INV-LSLV0075-S100' })];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u1', roles: ['cs_agent'] } }), { status: 200, headers: { 'X-CSRF-Token': 'tok' } });
    if (init?.method === 'POST') { posts.push({ url: path, body: JSON.parse(String(init.body)) }); return (opts.post ?? (() => jsonResponse(200, { data: {} })))(); }
    if (path.includes('/case-comparison')) return jsonResponse(200, { data: { request_id: path.includes(R2) ? R2 : R1, items: path.includes(R2) ? [] : opts.comparison ?? [row(T1, 'CS25-0044'), row(T2, 'CS25-0120', { case_id: null, case_external_id: null, symptom: null, root_cause_type: null, root_cause: null, action_taken: null, resolved_by_dept: null })], hidden_case_count: opts.hidden ?? 0 } });
    if (/\/improvement-requests\/[0-9a-f-]{36}$/.test(path)) return jsonResponse(200, { data: { request: summary(path.includes(R2) ? R2 : R1, '요청', { completed_at: null }), departments: [], evidence: opts.evidence ?? [ev('e1', T1, 'CS25-0044'), ev('e2', T2, 'CS25-0120'), ev('e3', T3, 'CS26-1036', { removed_at: '2026-10-27T09:40:00Z', removed_by: '11111111-1111-4111-8111-111111111111', removal_reason: '단자 표기 문의로 누유와 무관' })], next_owners: [], replies: [], actions: [], rounds: [] } });
    opts.listCalls?.push(path);
    return jsonResponse(200, { data: { items } });
  }));
  return posts;
}
const view = (live = true) => { setApiMode(live ? 'live' : 'mock'); renderAt('/cs/cases', '/cs/cases', <CsCases />); };

test('근거마다 해결 기록을 한 표에 보이고, 해결 기록이 없으면 미기록으로 보이며 건수를 센다', async () => {
  server();
  view();
  const table = await screen.findByRole('table', { name: '반복 사례 비교' });
  const rows = within(table).getAllByRole('row').slice(1);
  expect(rows).toHaveLength(2);
  expect(rows[0].textContent).toContain('CS25-0044');
  expect(rows[0].textContent).toContain('RC-CS25-0044');
  expect(rows[0].textContent).toContain('Gasket damage');
  expect(rows[0].textContent).toContain('현장 서비스');
  expect(rows[1].textContent).toContain('없음');
  expect(rows[1].textContent).toContain('미기록');
  expect(screen.getByText(/해결 기록 없는 문의/).textContent).toContain('1');
  expect(screen.getByText(/근거 문의 2건/)).toBeTruthy();
});

test('볼 수 없는 문의 수가 있으면 표에서 빠졌다고 알린다', async () => {
  server({ hidden: 3 });
  view();
  expect((await screen.findByRole('note')).textContent).toContain('3건은 표에서 빠졌습니다');
  expect(screen.getByText(/범위 밖 3건/)).toBeTruthy();
});

test('근거 변경 기록에 추가와 제외를 시간순으로 보이고 제외 사유를 적는다', async () => {
  server();
  view();
  const table = await screen.findByRole('table', { name: '근거 변경 기록' });
  const rows = within(table).getAllByRole('row').slice(1);
  expect(rows).toHaveLength(4);
  expect(rows.map(item => item.textContent?.includes('추가') ? '추가' : '뺌')).toEqual(['추가', '추가', '추가', '뺌']);
  expect(rows[3].textContent).toContain('단자 표기 문의로 누유와 무관');
});

test('개선요청을 바꾸면 그 요청의 비교표를 읽고, 출처를 고르면 서버에 그 출처만 요청한다', async () => {
  const listCalls: string[] = [];
  server({ listCalls });
  view();
  await screen.findByRole('table', { name: '반복 사례 비교' });
  fireEvent.change(screen.getByLabelText('개선요청'), { target: { value: R2 } });
  await screen.findByText('비교할 근거가 없습니다.');
  fireEvent.change(screen.getByLabelText('출처'), { target: { value: 'SYSTEM_CANDIDATE' } });
  await waitFor(() => expect(listCalls.some(url => url.endsWith('improvement-requests?origin=SYSTEM_CANDIDATE'))).toBe(true));
});

test('근거에서 빼기는 표에서 고른 근거와 사유, 읽은 판본을 보내고 끝나면 다시 읽는다', async () => {
  const posts = server();
  view();
  await screen.findByRole('table', { name: '반복 사례 비교' });
  const button = screen.getByRole('button', { name: '근거에서 빼기' }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  await waitFor(() => expect(screen.getByLabelText('CS25-0120 선택')).toBeTruthy());
  fireEvent.click(screen.getByLabelText('CS25-0120 선택'));
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  const dialog = within(await screen.findByRole('dialog'));
  fireEvent.change(dialog.getByLabelText('빼는 사유'), { target: { value: '증상이 달라 무관' } });
  fireEvent.click(dialog.getByRole('button', { name: '빼기' }));
  await screen.findByText(/근거에서 뺐습니다/);
  expect(posts).toHaveLength(1);
  expect(posts[0].url).toMatch(/\/improvement-requests\/dddddddd-.*\/evidence\/e2\/remove$/);
  expect(posts[0].body).toEqual({ version: 3, reason: '증상이 달라 무관' });
});

test('판본 충돌(409)과 서버 거부는 이유를 보이고 같은 요청을 다시 보내지 않는다', async () => {
  const posts = server({ post: () => jsonResponse(409, errorBody('CS_REQUEST_VERSION_CONFLICT', '판본이 다릅니다')) });
  view();
  await screen.findByRole('table', { name: '반복 사례 비교' });
  await waitFor(() => expect(screen.getByLabelText('CS25-0044 선택')).toBeTruthy());
  fireEvent.click(screen.getByLabelText('CS25-0044 선택'));
  await waitFor(() => expect((screen.getByRole('button', { name: '근거에서 빼기' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '근거에서 빼기' }));
  fireEvent.change(within(await screen.findByRole('dialog')).getByLabelText('빼는 사유'), { target: { value: '무관' } });
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '빼기' }));
  expect((await screen.findByRole('alert')).textContent).toContain('다른 사용자가 먼저 바꿨습니다');
  expect(posts).toHaveLength(1);
});

test('모의 응답 모드에서는 안내를 보이고 근거 빼기를 열지 않으며, 요청이 없으면 안내한다', async () => {
  server();
  view(false);
  expect(await screen.findByText(/모의 응답 모드/)).toBeTruthy();
  expect((screen.queryByRole('button', { name: '근거에서 빼기' }) as HTMLButtonElement | null)?.disabled ?? true).toBe(true);
  cleanup();
  server({ items: [] });
  view();
  expect(await screen.findByText('비교할 개선요청이 없습니다.')).toBeTruthy();
  expect(screen.queryByRole('table', { name: '반복 사례 비교' })).toBeNull();
});
