import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import DeptInbox from './DeptInbox';
import { setApiMode } from '../api/client';
import type { TicketItem } from '../api/cs';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const ago = (days: number) => new Date(Date.now() - days * 86_400_000).toISOString();
const ticket = (n: number, round: number, status: NonNullable<TicketItem['transfer']>['status'], days: number): TicketItem => ({
  id: `3333333${n}-3333-4333-8333-333333333333`, external_id: `CS26-100${n}`, subject: `합성 문의 ${n}`, channel: 'EMAIL', product_code: 'INV-TEST', workflow_state: 'TRANSFERRED',
  owner_user_id: null, received_at: ago(days + 2), data_origin: 'synthetic', derivation_kind: 'PROVIDED',
  transfer: { round, dept_code: 'QA_CLAIM', status, transferred_at: ago(days) },
} as TicketItem);
const identity = (over: Record<string, unknown> = {}) => ({ data: { id: '00000000-0000-4000-8000-000000000009', display_name: '최민호', dept: 'QA_CLAIM', roles: ['dept_staff'], ...over } });
const serve = (items: TicketItem[], who = identity(), next: string | null = null) => vi.fn(async (url: RequestInfo | URL) => String(url).endsWith('/platform/me') ? jsonResponse(200, who)
  : jsonResponse(200, { data: { items, next_cursor: next } }));
const show = () => { setApiMode('live'); return renderAt('/dept/inbox', '/dept/inbox', <DeptInbox />); };
const listCalls = (fetch: ReturnType<typeof serve>) => fetch.mock.calls.map(call => String(call[0])).filter(url => url.includes('/cs/tickets'));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

test('소속 부서로 목록을 읽어 차수·요청일시·상태·경과일을 보이고 답변할 요청과 7일 이상을 센다', async () => {
  const fetch = serve([ticket(1, 2, 'OPEN', 0), ticket(2, 1, 'OPEN', 8), ticket(3, 1, 'OPEN', 13)]);
  vi.stubGlobal('fetch', fetch);
  show();
  const table = await screen.findByRole('table', { name: '받은 요청' });
  const rows = within(table).getAllByRole('row').slice(1);
  expect(rows).toHaveLength(3);
  expect(rows[0].textContent).toContain('CS26-1001');
  expect(rows[0].textContent).toContain('진행');
  expect(screen.getByText(/답변할 요청/).textContent).toMatch(/3건, 7일 이상 2/);
  expect(listCalls(fetch)[0]).toBe('/api/v1/cs/tickets?limit=100&dept=QA_CLAIM&transfer_status=OPEN');
  expect((screen.getByLabelText('부서') as HTMLSelectElement).disabled).toBe(true);
});

test('상태를 바꾸면 서버에 그 상태만 요청하고 전체는 상태 조건 없이 요청한다', async () => {
  const fetch = serve([ticket(1, 1, 'RESULT_SUBMITTED', 3)]);
  vi.stubGlobal('fetch', fetch);
  show();
  await screen.findByRole('table', { name: '받은 요청' });
  fireEvent.change(screen.getByLabelText('상태'), { target: { value: 'RESULT_SUBMITTED' } });
  await waitFor(() => expect(listCalls(fetch).some(url => url.endsWith('dept=QA_CLAIM&transfer_status=RESULT_SUBMITTED'))).toBe(true));
  fireEvent.change(screen.getByLabelText('상태'), { target: { value: '' } });
  await waitFor(() => expect(listCalls(fetch).some(url => url.endsWith('limit=100&dept=QA_CLAIM'))).toBe(true));
});

test('차수와 요청일자는 받아 온 목록에서 거르고 범위가 거꾸로면 표를 비운다', async () => {
  vi.stubGlobal('fetch', serve([ticket(1, 2, 'OPEN', 1), ticket(2, 1, 'OPEN', 5)]));
  show();
  await screen.findByRole('table', { name: '받은 요청' });
  fireEvent.change(screen.getByLabelText('차수'), { target: { value: '2+' } });
  expect(within(screen.getByRole('table', { name: '받은 요청' })).getAllByRole('row')).toHaveLength(2);
  expect(screen.getByText('합성 문의 1')).toBeTruthy();
  expect(screen.queryByText('합성 문의 2')).toBeNull();
  fireEvent.change(screen.getByLabelText('시작일'), { target: { value: '2026-12-02' } });
  fireEvent.change(screen.getByLabelText('종료일'), { target: { value: '2026-12-01' } });
  expect(screen.getByRole('alert').textContent).toContain('종료일은 시작일보다 빠를 수 없습니다');
  expect(screen.getByText('조건에 맞는 요청이 없습니다.')).toBeTruthy();
});

test('이관 정보가 없는 항목은 표에 넣지 않고 더 보기는 다음 커서로 이어 붙인다', async () => {
  const fetch = serve([ticket(1, 1, 'OPEN', 1), { ...ticket(2, 1, 'OPEN', 1), transfer: null }], identity(), 'next-1');
  vi.stubGlobal('fetch', fetch);
  show();
  await screen.findByRole('table', { name: '받은 요청' });
  expect(within(screen.getByRole('table', { name: '받은 요청' })).getAllByRole('row')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: '더 보기' }));
  await waitFor(() => expect(listCalls(fetch).some(url => url.includes('cursor=next-1'))).toBe(true));
});

test('소속 부서가 없는 계정은 부서를 고를 때까지 목록을 요청하지 않는다', async () => {
  const fetch = serve([ticket(1, 1, 'OPEN', 1)], identity({ dept: null, roles: ['cs_agent'] }));
  vi.stubGlobal('fetch', fetch);
  show();
  expect(await screen.findByText(/소속 부서가 없는 계정입니다/)).toBeTruthy();
  expect(listCalls(fetch)).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('부서'), { target: { value: 'TS_TRF' } });
  await waitFor(() => expect(listCalls(fetch)[0]).toBe('/api/v1/cs/tickets?limit=100&dept=TS_TRF&transfer_status=OPEN'));
});

test('접근 거절(403)은 오류 화면으로 보이고 로그인 전이면 안내한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).endsWith('/platform/me') ? jsonResponse(200, identity()) : jsonResponse(403, errorBody('FORBIDDEN', '권한이 없습니다.'))));
  show();
  expect(await screen.findByText('권한이 없습니다')).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, errorBody('AUTH_REQUIRED', '만료'))));
  show();
  expect(await screen.findByText(/로그인이 필요합니다/)).toBeTruthy();
});
