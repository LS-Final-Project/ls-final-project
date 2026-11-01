import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import CsRepeatCases from './CsRepeatCases';
import { setApiMode } from '../api/client';
import type { useBusinessAccess } from '../components/BusinessAccess';
import { errorBody, jsonResponse, renderAt } from '../test/render';

// 운영 빌드의 권한 확인을 대신한다. null이면 실제 훅(개발 모드에서는 바로 열림)을 쓴다.
let gate: ReturnType<typeof useBusinessAccess> | null = null;
vi.mock('../components/BusinessAccess', async original => {
  const actual = await original<typeof import('../components/BusinessAccess')>();
  return { ...actual, useBusinessAccess: (business: 'cs' | 'tft') => gate ?? actual.useBusinessAccess(business) };
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); gate = null; });

const ID = '11111111-1111-4111-8111-111111111111';
const candidate = { id: ID, request_type: 'FAULT', origin: 'SYSTEM_CANDIDATE', product_code: 'SYN-INV-1', fault_id: 'FLT-001', doc_signal: null, status: 'DRAFT', version: 1, cs_owner: null, problem_statement: '합성 반복 과전류 정지', evidence_count: 7, created_at: '2026-10-30T01:00:00Z' };
const solved = { ticket_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', ticket_external_id: 'SYN-T-1', customer_external_id: 'SYN-C-1', product_code: 'SYN-INV-1', case_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1', case_external_id: 'SYN-CASE-1', symptom: '합성 증상', root_cause_type: '펌웨어', root_cause: '합성 원인', action_taken: '합성 조치', resolved_by_dept: 'RND_FW', known_at: '2026-10-29T02:00:00Z' };
const open = { ...solved, ticket_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2', ticket_external_id: 'SYN-T-2', customer_external_id: 'SYN-C-2', case_id: null, case_external_id: null, symptom: null, root_cause_type: null, root_cause: null, action_taken: null, resolved_by_dept: null, known_at: null };

type Reply = [number, unknown];
function serve(listReply: Reply, compareReply: Reply = [200, { data: { request_id: ID, items: [solved, open], hidden_case_count: 2 } }]) {
  const fetch = vi.fn(async (url: RequestInfo | URL) => {
    const [status, body] = String(url).endsWith('/case-comparison') ? compareReply : listReply;
    return jsonResponse(status, body);
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}
function view(path = '/cs/repeat-cases') {
  setApiMode('live');
  return renderAt(path, 'cs/repeat-cases/:requestId?', <CsRepeatCases />);
}

test('저장된 시스템 후보만 조회하고 서버 건수와 이동 경로를 표시한다', async () => {
  const fetch = serve([200, { data: { items: [candidate] } }]);
  view();
  const grid = await screen.findByRole('table', { name: '반복 사례 후보 목록' });
  expect(String(fetch.mock.calls[0][0])).toBe('/api/v1/cs/improvement-requests?origin=SYSTEM_CANDIDATE');
  expect(within(grid).getByText('7건')).toBeTruthy();
  expect(within(grid).getByText('미배정')).toBeTruthy();
  expect(within(grid).getByRole('link', { name: '합성 반복 과전류 정지' }).getAttribute('href')).toBe(`/cs/repeat-cases/${ID}`);
  expect(within(grid).getByRole('link', { name: '개선요청 상세' }).getAttribute('href')).toBe(`/cs/improvements/${ID}`);
  expect(fetch).toHaveBeenCalledTimes(1);
});

test('비교표는 해결 기록과 없는 기록을 구분하고 문의 처리로 이동한다', async () => {
  const fetch = serve([200, { data: { items: [candidate] } }]);
  view(`/cs/repeat-cases/${ID}`);
  const grid = await screen.findByRole('table', { name: '사례 비교표' });
  expect(fetch.mock.calls.some(([url]) => String(url) === `/api/v1/cs/improvement-requests/${ID}/case-comparison`)).toBe(true);
  expect(within(grid).getByRole('link', { name: 'SYN-T-1' }).getAttribute('href')).toBe(`/cs/tickets/${solved.ticket_id}`);
  expect(within(grid).getByRole('link', { name: 'SYN-T-2' }).getAttribute('href')).toBe(`/cs/tickets/${open.ticket_id}`);
  expect(within(grid).getByText('합성 조치')).toBeTruthy();
  expect(within(grid).getByText('펌웨어 개발')).toBeTruthy();
  expect(within(grid).getByText('해결 기록 없음')).toBeTruthy();
  expect(screen.getByRole('note').textContent).toContain('근거 문의 2건은 표에서 뺐습니다');
  expect(screen.getByText(/사례 비교표 — 합성 반복 과전류 정지/)).toBeTruthy();
});

test('숨긴 문의가 없으면 숨김 안내를 내지 않는다', async () => {
  serve([200, { data: { items: [candidate] } }], [200, { data: { request_id: ID, items: [solved], hidden_case_count: 0 } }]);
  view(`/cs/repeat-cases/${ID}`);
  await screen.findByRole('table', { name: '사례 비교표' });
  expect(screen.queryByRole('note')).toBeNull();
});

test('빈 목록과 빈 비교표를 각각 안내한다', async () => {
  serve([200, { data: { items: [] } }], [200, { data: { request_id: ID, items: [], hidden_case_count: 0 } }]);
  view(`/cs/repeat-cases/${ID}`);
  await screen.findByText('저장된 반복 사례 후보가 없습니다.');
  await screen.findByText('비교할 근거 문의가 없습니다.');
});

test.each([[403, '권한이 없습니다'], [404, '찾을 수 없습니다']])('비교표 오류 %i은 목록을 남기고 안내한다', async (status, title) => {
  serve([200, { data: { items: [candidate] } }], [status, errorBody('FAIL', '합성 오류')]);
  view(`/cs/repeat-cases/${ID}`);
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain(title);
  expect(screen.getByRole('table', { name: '반복 사례 후보 목록' })).toBeTruthy();
  expect(screen.queryByRole('table', { name: '사례 비교표' })).toBeNull();
});

test('목록 통신 실패 뒤 다시 시도로 복구한다', async () => {
  let fail = true;
  vi.stubGlobal('fetch', vi.fn(async () => fail ? jsonResponse(503, errorBody('UNAVAILABLE', '합성 일시 오류')) : jsonResponse(200, { data: { items: [candidate] } })));
  view();
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain('요청을 처리하지 못했습니다');
  fail = false;
  fireEvent.click(within(alert).getByRole('button', { name: '다시 시도' }));
  await screen.findByRole('table', { name: '반복 사례 후보 목록' });
  expect(screen.queryByRole('alert')).toBeNull();
});

test('서버에 닿지 못하면 연결 실패로 안내한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
  view();
  expect((await screen.findByRole('alert')).textContent).toContain('업무 서버에 연결할 수 없습니다');
});

test('운영 빌드에서 권한이 확인되면 열리고, 역할이 없으면 조회하지 않는다', async () => {
  const fetch = serve([200, { data: { items: [candidate] } }]);
  gate = { ready: true, operational: true, enabled: true, user: {} as ReturnType<typeof useBusinessAccess>['user'] };
  view();
  await screen.findByRole('table', { name: '반복 사례 후보 목록' });
  cleanup();
  fetch.mockClear();
  gate = { ready: false, operational: true, enabled: true, user: { isPending: false, isError: false, data: { data: { roles: [] } } } as unknown as ReturnType<typeof useBusinessAccess>['user'] };
  view();
  expect(screen.getByRole('alert').textContent).toContain('권한이 없습니다');
  await waitFor(() => expect(fetch).not.toHaveBeenCalled());
});
