import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import CsAdmin from './CsAdmin';
import CsCustomers from './CsCustomers';
import { statsQuery } from '../api/cs-stats';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';

// 이전 CsStats 화면의 시험을 고객 이력(CS0103)과 운영 현황(CS0901)으로 나눠 옮긴 것이다.
const example = examples.find(x => x.path === '/api/v1/cs/stats/overview')!.body;
const owners = (items: unknown[]) => ({ data: { period: { start: null, end: null }, items } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });
const admin = () => { setApiMode('live'); return renderAt('/cs/admin', '/cs/admin', <CsAdmin />); };
const customers = () => { setApiMode('live'); return renderAt('/cs/customers', '/cs/customers', <CsCustomers />); };
const serve = (extra: (url: string) => unknown = () => undefined) => vi.fn(async (url: RequestInfo | URL) => {
  const path = String(url);
  return jsonResponse(200, extra(path) ?? (path.includes('/owners') ? owners([]) : example));
});

test('운영 현황은 저장된 집계와 미확인 지표를 구분하고 기간을 현지 시각 기준 ISO로 보낸다', async () => {
  const fetch = serve(); vi.stubGlobal('fetch', fetch); admin();
  await screen.findByText('판단이 필요한 것');
  expect(screen.getByText(/SLA 지연 아님/)).toBeTruthy(); expect(screen.getByText(/미확인 지표:/)).toBeTruthy();
  const before = fetch.mock.calls.length;
  fireEvent.change(screen.getByLabelText('시작일'), { target: { value: '2026-10-01' } });
  fireEvent.change(screen.getByLabelText('종료일 (포함)'), { target: { value: '2026-09-01' } });
  fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await screen.findByText('종료일은 시작일보다 빠를 수 없습니다.'); expect(fetch).toHaveBeenCalledTimes(before);
  fireEvent.change(screen.getByLabelText('종료일 (포함)'), { target: { value: '2026-10-31' } });
  fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await waitFor(() => expect(fetch.mock.calls.length).toBeGreaterThan(before));
  const urls = fetch.mock.calls.map(([url]) => String(url));
  // 종료일은 포함이므로 다음 날 0시(현지)를 미포함 끝으로 보낸다.
  expect(urls.some(url => url.includes(`start=${encodeURIComponent(new Date('2026-10-01T00:00').toISOString())}`) && url.includes(`end=${encodeURIComponent(new Date('2026-11-01T00:00').toISOString())}`))).toBe(true);
});

test.each([401, 403, 503])('운영 현황 갱신 %i는 이전 집계를 숨기고 다시 조회로 복구한다', async status => {
  let fail = false;
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => fail ? jsonResponse(status, errorBody('FAIL', '합성 실패')) : jsonResponse(200, String(url).includes('/owners') ? owners([]) : example)));
  admin(); await screen.findByText('판단이 필요한 것');
  fail = true; fireEvent.click(screen.getByRole('button', { name: '조회' })); await screen.findByRole('alert');
  expect(screen.queryByText('판단이 필요한 것')).toBeNull();
  fail = false; // 401·403·404는 재시도해도 같아서 '다시 시도' 단추 없이 안내만 한다. 조회 단추로 복구한다.
  fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await screen.findByText('판단이 필요한 것');
});

test('담당자 미배정과 기간 밖 진행 부담을 구분하고 담당자 집계에는 고객 조건을 보내지 않는다', async () => {
  const fetch = serve(url => url.includes('/owners') ? owners([{ owner_user_id: null, received: 0, closed: 0, open: 3 }]) : undefined);
  vi.stubGlobal('fetch', fetch); admin();
  await screen.findByText('판단이 필요한 것'); await screen.findByText('미배정');
  expect(screen.getByText('담당자별 진행')).toBeTruthy();
  expect(screen.getByText(/진행은 전체 기간/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('고객 ID'), { target: { value: 'CUST-1' } });
  fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).includes('/overview?') && String(url).includes('customer=CUST-1'))).toBe(true));
  expect(fetch.mock.calls.filter(([url]) => String(url).includes('/owners')).every(([url]) => !String(url).includes('customer'))).toBe(true);
  // "담당자 없음" 건수는 담당자 미배정 진행 건에서 온다.
  const row = (await screen.findByText('담당자 없음')).closest('tr')!; expect(row.textContent).toContain('3');
});

test('담당자별 집계가 관리자 범위 밖이면 그 구역만 안내하고 나머지 집계는 보인다', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => String(url).includes('/owners') ? jsonResponse(403, errorBody('FORBIDDEN', '관리자만')) : jsonResponse(200, example)));
  admin(); await screen.findByText('판단이 필요한 것');
  await screen.findByText('담당자별 현황은 관리자 범위에서만 제공됩니다.');
  expect(screen.getByText('부서별 검토 요청')).toBeTruthy();
});

test('운영 현황은 조회 중 반복 클릭을 막고 이탈하면 두 요청을 취소하며 재진입은 다시 조회한다', async () => {
  const signals: AbortSignal[] = []; let calls = 0;
  vi.stubGlobal('fetch', vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
    calls++;
    if (calls > 2) return Promise.resolve(jsonResponse(200, String(url).includes('/owners') ? owners([]) : example));
    signals.push(init?.signal as AbortSignal);
    return new Promise<Response>((_r, reject) => (init?.signal as AbortSignal).addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
  }));
  const page = admin(); await waitFor(() => expect(calls).toBe(2));
  const button = screen.getByRole('button', { name: '조회' }) as HTMLButtonElement;
  expect(button.disabled).toBe(true); fireEvent.click(button); expect(calls).toBe(2);
  page.unmount(); expect(signals.every(signal => signal.aborted)).toBe(true);
  admin(); await screen.findByText('판단이 필요한 것');
});

test('운영 현황은 재조회 응답을 기다리는 동안 이전 표시를 숨긴다', async () => {
  let held = false;
  vi.stubGlobal('fetch', vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
    if (held) return new Promise<Response>((_r, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
    return Promise.resolve(jsonResponse(200, String(url).includes('/owners') ? owners([]) : example));
  }));
  const page = admin(); await screen.findByText('판단이 필요한 것');
  held = true; fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await waitFor(() => expect(screen.queryByText('판단이 필요한 것')).toBeNull()); page.unmount();
});

test('고객 이력은 고객 ID를 요구하고 부서 조건을 보내지 않으며 가정 장비를 표시한다', async () => {
  const fetch = vi.fn(async (_url: RequestInfo | URL) => jsonResponse(200, { data: { customer_external_id: 'TEST/ONE', received: 0, unconfirmed: [], by_state: {}, by_product: {}, tickets: [], units: [{ id: 'unit', serial_no: 'SYNTHETIC-UNIT', product_code: 'TEST', unit_status: 'ACTIVE', derivation_kind: 'ASSUMED', confirmed_tickets: 0 }], improvement_requests: [] } }));
  vi.stubGlobal('fetch', fetch); customers();
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
  vi.stubGlobal('fetch', vi.fn(async () => fail ? jsonResponse(status, errorBody('FAIL', '합성 실패')) : jsonResponse(200, { data: { customer_external_id: 'C1', received: 2, unconfirmed: [], by_state: { CLOSED: 1, IN_REVIEW: 1 }, by_product: { P1: 2 }, tickets: [], units: [], improvement_requests: [] } })));
  customers(); fireEvent.change(screen.getByLabelText('고객 ID'), { target: { value: 'C1' } }); fireEvent.click(screen.getByRole('button', { name: '조회' }));
  await screen.findByText('기간 내 접수'); expect(screen.getByText('진행 중')).toBeTruthy();
  fail = true; fireEvent.click(screen.getByRole('button', { name: '조회' })); await screen.findByRole('alert');
  expect(screen.queryByText('기간 내 접수')).toBeNull();
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
