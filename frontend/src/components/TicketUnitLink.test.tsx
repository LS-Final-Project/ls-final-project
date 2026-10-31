import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import TicketUnits from './TicketUnits';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const unit = (id: string, serial: string) => ({ id, serial_no: serial, product_code: 'P1', customer_external_id: 'C1', commissioning_date: null, commissioning_status: 'UNKNOWN', ship_date: null, fw_version_last: null, data_origin: 'synthetic', derivation_kind: 'PROVIDED' });
const link = (over: Record<string, unknown> = {}) => ({ id: 'l1', link_status: 'CANDIDATE', link_basis: 'SERIAL_PROVIDED', repeat_type: null, version: 3, decided_by: null, decided_at: null, ...over });
const cs = { data: { id: '00000000-0000-4000-8000-000000000001', display_name: '김지원', dept: null, roles: ['cs_agent'] } };

type Put = (init: RequestInit, path: string) => Response;
function serve(items: unknown[], put: Put = () => jsonResponse(200, { data: {} })) {
  return vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify(cs), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' } });
    if (init?.method === 'PUT') return put(init, path);
    return jsonResponse(200, { data: path.endsWith('/units') ? { items } : { status: 'NO_CONFIRMED_UNIT', unit: null, items: [] } });
  });
}
async function open(canWrite = true) {
  setApiMode('live');
  renderAt('/', '/', <TicketUnits ticketId="ticket-1" canWrite={canWrite} />);
  fireEvent.click(screen.getByRole('button', { name: '장비 정보 조회' }));
  await screen.findByRole('table', { name: '장비 후보' });
}
async function withSession() { const { request } = await import('../api/client'); await request('/api/v1/platform/me'); }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); clearSessionCsrfToken(); });

test('새 연결은 version null로, 기존 연결은 읽은 판본으로 고른 값을 저장한다', async () => {
  const puts: { path: string; body: unknown; token: string }[] = [];
  vi.stubGlobal('fetch', serve([{ unit: unit('u1', 'SIM-001'), link: null, matches_ticket: true }, { unit: unit('u2', 'SIM-002'), link: link(), matches_ticket: true }],
    (init, path) => { puts.push({ path, body: JSON.parse(String(init.body)), token: (init.headers as Record<string, string>)['X-CSRF-Token'] }); return jsonResponse(200, { data: {} }); }));
  await withSession();
  await open();
  const form = screen.getByRole('form', { name: '장비 연결' });
  fireEvent.change(within(form).getByLabelText('이 장비인가'), { target: { value: 'CONFIRMED' } });
  fireEvent.change(within(form).getByLabelText('이전 문의와 관계'), { target: { value: 'SAME_UNIT' } });
  fireEvent.click(within(form).getByRole('button', { name: '저장' }));
  expect(await screen.findByText('장비 연결을 저장했습니다.')).toBeTruthy();
  expect(puts[0]).toEqual({ path: '/api/v1/cs/tickets/ticket-1/units/u1', token: 'tok', body: { link_status: 'CONFIRMED', link_basis: 'SERIAL_PROVIDED', repeat_type: 'SAME_UNIT', version: null } });
  fireEvent.click(screen.getByLabelText('SIM-002 선택'));
  const second = screen.getByRole('form', { name: '장비 연결' });
  fireEvent.change(within(second).getByLabelText('확인 방법'), { target: { value: 'CUSTOMER_CONFIRMED' } });
  fireEvent.click(within(second).getByRole('button', { name: '저장' }));
  await waitFor(() => expect(puts).toHaveLength(2));
  expect(puts[1].body).toEqual({ link_status: 'CANDIDATE', link_basis: 'CUSTOMER_CONFIRMED', repeat_type: null, version: 3 });
});

test('확정한 장비의 관계를 다른 장비로 둘 수 없고, 고객·제품이 다른 장비는 아님만 기록할 수 있다', async () => {
  const fetch = serve([{ unit: unit('u1', 'SIM-001'), link: null, matches_ticket: false }]);
  vi.stubGlobal('fetch', fetch);
  await withSession();
  await open();
  const form = screen.getByRole('form', { name: '장비 연결' });
  expect(screen.getByText(/"아님"만 기록할 수 있습니다/)).toBeTruthy();
  expect((within(form).getByRole('option', { name: '맞음' }) as HTMLOptionElement).disabled).toBe(true);
  expect((within(form).getByRole('button', { name: '저장' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(within(form).getByLabelText('이 장비인가'), { target: { value: 'REJECTED' } });
  fireEvent.change(within(form).getByLabelText('이전 문의와 관계'), { target: { value: 'DIFFERENT_UNIT' } });
  expect((within(form).getByRole('button', { name: '저장' }) as HTMLButtonElement).disabled).toBe(false);
  cleanup();
  vi.stubGlobal('fetch', serve([{ unit: unit('u1', 'SIM-001'), link: null, matches_ticket: true }]));
  await open();
  const ok = screen.getByRole('form', { name: '장비 연결' });
  fireEvent.change(within(ok).getByLabelText('이 장비인가'), { target: { value: 'CONFIRMED' } });
  fireEvent.change(within(ok).getByLabelText('이전 문의와 관계'), { target: { value: 'DIFFERENT_UNIT' } });
  expect(screen.getByRole('alert').textContent).toContain('"다른 장비"로 둘 수 없습니다');
  expect((within(ok).getByRole('button', { name: '저장' }) as HTMLButtonElement).disabled).toBe(true);
});

test('판본 충돌·이미 확정·고객 불일치는 이유를 보이고, 알 수 없는 오류는 다시 보내지 않고 재조회한다', async () => {
  let reply: Response = jsonResponse(409, errorBody('CS_UNIT_LINK_VERSION_CONFLICT', '충돌'));
  const puts: number[] = [];
  const fetch = serve([{ unit: unit('u1', 'SIM-001'), link: null, matches_ticket: true }], () => { puts.push(1); return reply.clone(); });
  vi.stubGlobal('fetch', fetch);
  await withSession();
  await open();
  const save = () => fireEvent.click(within(screen.getByRole('form', { name: '장비 연결' })).getByRole('button', { name: '저장' }));
  save();
  expect((await screen.findByRole('alert')).textContent).toContain('다른 사용자가 먼저 이 장비 연결을 바꿨습니다');
  reply = jsonResponse(409, errorBody('CS_TICKET_UNIT_ALREADY_CONFIRMED', '이미'));
  save();
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('이미 다른 장비가 확정돼 있습니다'));
  reply = jsonResponse(422, errorBody('CS_UNIT_MISMATCH', '불일치'));
  save();
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('고객 또는 제품이 문의와 다른 장비'));
  reply = jsonResponse(503, errorBody('UNAVAILABLE', '장애'));
  save();
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('같은 요청을 다시 보내지 않고'));
  expect(puts).toHaveLength(4);
  const reads = fetch.mock.calls.filter(call => String(call[0]).endsWith('/units') && !(call[1] as RequestInit | undefined)?.method).length;
  expect(reads).toBeGreaterThanOrEqual(5);
});

test('쓰기가 막힌 상태(canWrite 아님)에서는 양식 없이 안내만 보인다', async () => {
  vi.stubGlobal('fetch', serve([{ unit: unit('u1', 'SIM-001'), link: null, matches_ticket: true }]));
  await open(false);
  expect(screen.queryByRole('form', { name: '장비 연결' })).toBeNull();
  expect(screen.getByText(/실제 API 개발 모드에서만 할 수 있습니다/)).toBeTruthy();
});
