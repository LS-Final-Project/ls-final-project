import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import CsAdmin from './CsAdmin';
import CsCustomers from './CsCustomers';
import { statsQuery } from '../api/cs-stats';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';

// 고객 이력(CS0103)과 업무 통계(CS0901, KAN-233 서버 집계) 화면 시험.
const example = examples.find(x => x.path === '/api/v1/cs/stats/overview')!.body;
const owners = (items: unknown[]) => ({ data: { period: { start: null, end: null }, items } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });
const admin = () => { setApiMode('live'); return renderAt('/cs/admin', '/cs/admin', <CsAdmin />); };
const customers = () => { setApiMode('live'); return renderAt('/cs/customers', '/cs/customers', <CsCustomers />); };
const serve = (extra: (url: string) => unknown = () => undefined) => vi.fn(async (url: RequestInfo | URL) => {
  const path = String(url);
  return jsonResponse(200, extra(path) ?? (path.includes('/owners') ? owners([]) : example));
});

const LOADED = 'SLA 기준 처리 현황';
const OWNER = '11111111-1111-4111-8111-111111111111';
const ownerRow = (extra: Record<string, unknown> = {}) => ({ owner_user_id: OWNER, received: 4, closed: 3, open: 5, by_state: {}, due_today: 2, overdue: 1, sla_unknown: 0, avg_handle_days: 2.5, improvements_open: 4, ...extra });

test('업무 통계는 서버 SLA·부서·개선 집계를 그리고 더미 안내 띠가 없다', async () => {
  const fetch = serve(url => url.includes('/owners') ? owners([ownerRow()]) : url.includes('/platform/users') ? { data: { items: [{ user_id: OWNER, display_name: '시연 담당자' }] } } : undefined);
  vi.stubGlobal('fetch', fetch); admin();
  await screen.findByText(LOADED);
  expect(screen.queryByText('더미 데이터 미리보기')).toBeNull();
  // 예시: 첫 응답 36/40, 해결 31/35, 임박 2, 초과 1, 초과 문의 15시간.
  expect(screen.getByText('90%')).toBeTruthy(); expect(screen.getByText('36 / 40건')).toBeTruthy();
  expect(screen.getByText('31 / 35건')).toBeTruthy();
  const overdue = (await screen.findByRole('link', { name: 'CSV1-000044' })).closest('tr')!;
  expect(overdue.textContent).toContain('Oil leakage at gasket'); expect(overdue.textContent).toContain('15시간');
  expect(screen.getByText(/분류 없어 SLA 미확인 0건/)).toBeTruthy();
  // 부서: 요청 4, 답변 3, 대기 1, 평균 회신 2.4일.
  const dept = screen.getByRole('region', { name: '부서별 검토 요청 상황' });
  expect(dept.textContent).toContain('2.4일');
  // 개선: 회신 기한 초과 1, 재발 신호 0(계산됨).
  expect(screen.getByText('부서 회신 기한 초과 1')).toBeTruthy(); expect(screen.getByText('완료 후 재발 신호 0')).toBeTruthy();
  const owner = (await screen.findByText('시연 담당자')).closest('tr')!;
  expect(owner.textContent).toContain('2.5일');
});

test('서버가 주지 않은 칸은 0이 아니라 미확인으로 보인다', async () => {
  const bare = { data: { ...example.data, sla: null, departments: [{ dept_code: 'TS_TRF', transferred: 1, results_submitted: 0, results_accepted: 0, returned_or_recalled: 0 }], improvements: { by_status: {}, by_type: { FAULT: 0, DOCUMENT: 0 }, open: 0, receiver_unconfirmed: 0 } } };
  vi.stubGlobal('fetch', serve(url => url.includes('/owners') ? owners([{ owner_user_id: null, received: 0, closed: 0, open: 3, by_state: {} }]) : bare)); admin();
  await screen.findByText(LOADED);
  expect(screen.getAllByText('미확인').length).toBeGreaterThan(5);
  expect(screen.getByText('완료 후 재발 신호 미확인')).toBeTruthy();
  const row = (await screen.findByText('미배정')).closest('tr')!; expect(row.textContent).toContain('미확인');
});

test('기간·부서 조건을 조회에 반영하고 거꾸로 된 기간은 보내지 않으며 조건 지우기는 전체로 되돌린다', async () => {
  const fetch = serve(); vi.stubGlobal('fetch', fetch); admin();
  await screen.findByText(LOADED);
  const before = fetch.mock.calls.length;
  fireEvent.change(screen.getByLabelText('시작일'), { target: { value: '2026-10-01' } });
  fireEvent.change(screen.getByLabelText('종료일 (포함)'), { target: { value: '2026-09-01' } });
  fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await screen.findByText('종료일은 시작일보다 빠를 수 없습니다.'); expect(fetch).toHaveBeenCalledTimes(before);
  fireEvent.change(screen.getByLabelText('종료일 (포함)'), { target: { value: '2026-10-31' } });
  fireEvent.change(screen.getByLabelText('이관 부서'), { target: { value: 'TS_TRF' } });
  fireEvent.keyDown(window, { key: 'F8' });
  await waitFor(() => expect(fetch.mock.calls.length).toBeGreaterThan(before));
  const urls = fetch.mock.calls.map(([url]) => String(url));
  // 종료일은 포함이므로 다음 날 0시(현지)를 미포함 끝으로 보낸다. 담당자 집계에도 같은 조건을 보낸다.
  const range = (url: string) => url.includes(`start=${encodeURIComponent(new Date('2026-10-01T00:00').toISOString())}`) && url.includes(`end=${encodeURIComponent(new Date('2026-11-01T00:00').toISOString())}`) && url.includes('dept=TS_TRF');
  expect(urls.some(url => url.includes('/overview?') && range(url))).toBe(true);
  expect(urls.some(url => url.includes('/owners?') && range(url))).toBe(true);
  await screen.findByText(LOADED);
  fireEvent.click(screen.getByRole('button', { name: /조건 지우기/ }));
  await waitFor(() => expect(String(fetch.mock.calls.at(-1)?.[0])).toMatch(/\/stats\/(overview|owners)$/));
  expect((screen.getByLabelText('시작일') as HTMLInputElement).value).toBe('');
});

test.each([401, 403, 503])('업무 통계 갱신 %i는 이전 집계를 숨기고 다시 조회로 복구한다', async status => {
  let fail = false;
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => fail ? jsonResponse(status, errorBody('FAIL', '합성 실패')) : jsonResponse(200, String(url).includes('/owners') ? owners([]) : example)));
  admin(); await screen.findByText(LOADED);
  fail = true; fireEvent.click(screen.getByRole('button', { name: '조회' })); await screen.findByRole('alert');
  expect(screen.queryByText(LOADED)).toBeNull();
  fail = false; // 401·403·404는 재시도해도 같아서 '다시 시도' 단추 없이 안내만 한다. 조회 단추로 복구한다.
  fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await screen.findByText(LOADED);
});

test('담당자별 집계가 관리자 범위 밖이면 그 구역만 안내하고 나머지 집계는 보인다', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).includes('/owners') ? jsonResponse(403, errorBody('FORBIDDEN', '관리자만')) : jsonResponse(200, example)));
  admin(); await screen.findByText(LOADED);
  await screen.findByText('담당자별 현황은 관리자 범위에서만 제공됩니다.');
  expect(screen.getByText('부서별 검토 요청 상황')).toBeTruthy();
});

test('업무 통계는 조회 중 반복 클릭을 막고 이탈하면 두 요청을 취소하며 재진입은 다시 조회한다', async () => {
  const signals: AbortSignal[] = []; let calls = 0;
  vi.stubGlobal('fetch', vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url).endsWith('/platform/me')) return Promise.resolve(jsonResponse(200, { data: { id: 'c', roles: ['cs_agent'], managed_departments: [] } }));
    calls++;
    if (calls > 2) return Promise.resolve(jsonResponse(200, String(url).includes('/owners') ? owners([]) : example));
    signals.push(init?.signal as AbortSignal);
    return new Promise<Response>((_r, reject) => (init?.signal as AbortSignal).addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
  }));
  const page = admin(); await waitFor(() => expect(calls).toBe(2));
  const button = screen.getByRole('button', { name: '조회' }) as HTMLButtonElement;
  expect(button.disabled).toBe(true); fireEvent.click(button); expect(calls).toBe(2);
  page.unmount(); expect(signals.every(signal => signal.aborted)).toBe(true);
  admin(); await screen.findByText(LOADED);
});

test('업무 통계는 재조회 응답을 기다리는 동안 이전 표시를 숨긴다', async () => {
  let held = false;
  vi.stubGlobal('fetch', vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
    if (held) return new Promise<Response>((_r, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
    return Promise.resolve(jsonResponse(200, String(url).includes('/owners') ? owners([]) : example));
  }));
  const page = admin(); await screen.findByText(LOADED);
  held = true; fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await waitFor(() => expect(screen.queryByText(LOADED)).toBeNull()); page.unmount();
});

test('고객 이력은 고객 ID를 요구하고 부서 조건을 보내지 않으며 가정 장비를 표시한다', async () => {
  const fetch = vi.fn(async (_url: RequestInfo | URL) => jsonResponse(200, { data: { customer_external_id: 'TEST/ONE', received: 0, unconfirmed: [], by_state: {}, by_product: {}, tickets: [], units: [{ id: 'unit', serial_no: 'SYNTHETIC-UNIT', product_code: 'TEST', unit_status: 'ACTIVE', derivation_kind: 'ASSUMED', confirmed_tickets: 0 }], improvement_requests: [] } }));
  vi.stubGlobal('fetch', fetch); customers();
  // 입력 칸에는 CUST-가 미리 적혀 있고, 그것만 있으면 입력하지 않은 것으로 본다.
  expect((screen.getByLabelText('고객 ID') as HTMLInputElement).value).toBe('CUST-');
  fireEvent.click(screen.getByRole('button', { name: '조회' })); await screen.findByText('고객 ID를 입력하세요.'); expect(fetch).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('고객 ID'), { target: { value: 'TEST/ONE' } });
  fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await screen.findByText('SYNTHETIC-UNIT'); expect(screen.getByText('가정 자료')).toBeTruthy();
  expect(String(fetch.mock.calls.at(-1)?.[0])).toBe('/api/v1/cs/stats/customers/TEST%2FONE');
});

test('고객 이력의 고객 ID를 입력만 하고 조회하지 않으면 미적용 안내를 보이고 요청하지 않는다', async () => {
  const fetch = vi.fn(async () => jsonResponse(200, example)); vi.stubGlobal('fetch', fetch); customers();
  fireEvent.change(screen.getByLabelText('고객 ID'), { target: { value: 'SYNTHETIC' } });
  expect(screen.getByText(/입력한 고객 ID는 아직 적용되지 않았습니다/)).toBeTruthy(); expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('초기화')); expect(screen.queryByText(/입력한 고객 ID는 아직 적용/)).toBeNull();
});

test.each([401, 403, 503])('고객 이력 갱신 %i는 이전 이력을 숨긴다', async status => {
  let fail = false;
  vi.stubGlobal('fetch', vi.fn(async () => fail ? jsonResponse(status, errorBody('FAIL', '합성 실패')) : jsonResponse(200, { data: { customer_external_id: 'C1', received: 2, unconfirmed: [], by_state: { CLOSED: 1, IN_REVIEW: 1, NEW_STATE: 1 }, by_product: { P1: 2 }, tickets: [], units: [], improvement_requests: [] } })));
  customers(); fireEvent.change(screen.getByLabelText('고객 ID'), { target: { value: 'C1' } }); fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await screen.findByLabelText('상태별 문의'); expect(screen.getByText('검토중')).toBeTruthy(); expect(screen.queryByText('미확인 지표')).toBeNull();
  // 정해진 상태 밖의 값도 카드로 보인다.
  expect(screen.getByText('NEW_STATE')).toBeTruthy();
  fail = true; fireEvent.click(screen.getByRole('button', { name: '조회' })); await screen.findByRole('alert');
  expect(screen.queryByLabelText('상태별 문의')).toBeNull();
});

test('URLSearchParams.size가 없는 환경에서도 필터를 보존한다', () => {
  const descriptor = Object.getOwnPropertyDescriptor(URLSearchParams.prototype, 'size');
  try {
    Object.defineProperty(URLSearchParams.prototype, 'size', { configurable: true, get: () => undefined });
    expect(statsQuery({ start: '', end: '', dept: 'TS_LV', customer: 'TEST/ONE' }, 'overview')).toBe('?dept=TS_LV&customer=TEST%2FONE');
    expect(statsQuery({ start: '', end: '', dept: '', customer: '' }, 'overview')).toBe('');
  } finally { if (descriptor) Object.defineProperty(URLSearchParams.prototype, 'size', descriptor); else Reflect.deleteProperty(URLSearchParams.prototype, 'size'); }
});

test('고객 이력은 라벨 표에 없는 문의 상태를 빈 칸이 아니라 서버 값 그대로 보인다', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url: RequestInfo | URL) => jsonResponse(200, { data: { customer_external_id: 'C1', received: 1, unconfirmed: [], by_state: { NEW_STATE: 1 }, by_product: {}, tickets: [{ id: 't', external_id: 'T-1', received_at: '2026-10-01T00:00:00Z', subject: 's', channel: 'EMAIL', product_code: 'P', workflow_state: 'NEW_STATE', data_origin: 'synthetic' }], units: [], improvement_requests: [] } })));
  customers(); fireEvent.change(screen.getByLabelText('고객 ID'), { target: { value: 'C1' } }); fireEvent.click(screen.getByRole('button', { name: '조회' }));
  const row = (await screen.findByRole('link', { name: 'T-1' })).closest('tr')!;
  expect(row.textContent).toContain('NEW_STATE');
});

test('고객 이력은 주소의 customer로 들어오면 바로 그 고객을 조회한다', async () => {
  const fetch = vi.fn(async (_url: RequestInfo | URL) => jsonResponse(200, { data: { customer_external_id: 'CUST-9', received: 1, unconfirmed: [], by_state: {}, by_product: { P: 1 }, tickets: [], units: [], improvement_requests: [] } }));
  vi.stubGlobal('fetch', fetch); setApiMode('live');
  renderAt('/cs/customers?customer=CUST-9', '/cs/customers', <CsCustomers />);
  await screen.findByLabelText('상태별 문의');
  expect(String(fetch.mock.calls.at(-1)?.[0])).toBe('/api/v1/cs/stats/customers/CUST-9');
  expect((screen.getByLabelText('고객 ID') as HTMLInputElement).value).toBe('CUST-9');
});

test('고객 이력은 고객 ID 칸에서 Enter로 조회한다', async () => {
  const fetch = vi.fn(async (_url: RequestInfo | URL) => jsonResponse(200, { data: { customer_external_id: 'C2', received: 0, unconfirmed: [], by_state: {}, by_product: {}, tickets: [], units: [], improvement_requests: [] } }));
  vi.stubGlobal('fetch', fetch); customers();
  const input = screen.getByLabelText('고객 ID');
  fireEvent.change(input, { target: { value: 'C2' } });
  fireEvent.submit(input.closest('form')!);
  await screen.findByLabelText('상태별 문의');
  expect(String(fetch.mock.calls.at(-1)?.[0])).toBe('/api/v1/cs/stats/customers/C2');
});

test('고객 이력 주소의 기간이 거꾸로면 조회하지 않고 오류를 보인다', async () => {
  const fetch = vi.fn(async () => jsonResponse(200, example)); vi.stubGlobal('fetch', fetch); setApiMode('live');
  renderAt('/cs/customers?customer=C3&start=2026-05-02&end=2026-05-01', '/cs/customers', <CsCustomers />);
  expect((await screen.findByRole('alert')).textContent).toContain('종료일은 시작일보다 빠를 수 없습니다.');
  expect(fetch).not.toHaveBeenCalled();
});
