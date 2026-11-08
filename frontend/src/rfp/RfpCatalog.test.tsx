// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import { conditionsText, nextPartnerCode, parseConditions } from '../api/catalog';
import { errorBody, jsonResponse, renderApp } from '../test/render';

const U = '11111111-1111-4111-8111-111111111111';
const response = (data: unknown) => jsonResponse(200, { data, meta: { request_id: 'test' } });
const OPTIONS = {
  attributes: ['rated_current', 'frame_size'], own_families: ['LV_BREAKER', 'TRANSFORMER'], partner_families: ['몰드 변압기', '저압 배전반'],
  lifecycle_statuses: ['ACTIVE', 'MATURE', 'DISCONTINUED'], scopes: ['SUPPLY', 'TEST'], own_certification_kinds: ['LISTED', 'TEST_REPORT', 'MARK'],
  partner_certification_kinds: ['LISTED', 'TEST_REPORT'], condition_names: ['voltage'],
};
const own = (code: string, extra = {}) => ({
  product_code: code, version: 0, source: 'FILE', family: 'LV_BREAKER', lifecycle_status: 'ACTIVE', standards: ['IEC 60947-2'],
  certifications: [{ standard: 'CE', kind: 'MARK', evidence_id: 'product_master', variant: null }],
  spec_facts: [{ product_code: code, attribute: 'frame_size', value: '100', unit: 'AF', conditions: [{ name: 'voltage', value: '415', unit: 'V' }], evidence_id: 'DS-1', section: '2.1' }],
  edited_at: null, edited_by: null, ...extra,
});
const partner = {
  profile: {
    candidate_code: 'P-A', display_name: '가상 협력사 P-A', manufacturer_name: '가상 제조사', profile_source: 'synthetic', profile_version: 2, data_origin: 'synthetic',
    derivation_kind: 'PROVIDED', known_at: '2026-10-28', product_family: '몰드 변압기', products: [{ product_code: 'SYN-1', model: 'M-1', scope: ['SUPPLY'] }],
    integrator_capable: { assembly: true, test: null, integration: null, warranty: null }, spec_facts: [], certifications: [], similar_tasks: [], supply_regions: ['KR'],
    lead_time: null, warranty: null, confirmation: { status: 'UNCONFIRMED', as_of: '2026-10-28', reviewer: '가상 검토자', follow_up_questions: [] }, notes: null,
  },
  edited_at: null, edited_by: null,
};

function live() {
  setApiMode('live');
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: U, tid: U, oid: U, display_name: '담당자', email: 'test@example.invalid', roles: ['sales'], dept: null } }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'session-test' } });
    if (url.endsWith('/catalog/options')) return response(OPTIONS);
    if (url.endsWith('/catalog/own-products')) return response([own('MCCB-1'), own('ACB-2', { version: 2, source: 'REGISTERED', lifecycle_status: 'DISCONTINUED', edited_at: '2026-11-08T03:00:00Z', edited_by: U })]);
    if (url.endsWith('/catalog/partners')) return response([partner]);
    if (init?.method === 'PUT' && url.includes('/own-products/NEW-1')) return response(own('NEW-1', { version: 1, source: 'REGISTERED' }));
    if (init?.method === 'PUT' && url.includes('/partners/')) return jsonResponse(409, errorBody('TFT_PRODUCT_CODE_TAKEN', 'taken'));
    if (url.includes('/users')) return response({ items: [{ user_id: U, display_name: '담당자' }] });
    return jsonResponse(404, errorBody('NOT_FOUND', '없음'));
  }));
  return calls;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); clearSessionCsrfToken(); setApiMode('mock'); });

test('conditions round-trip as one line and partner codes skip taken ones', () => {
  const conditions = [{ name: 'voltage', value: '415', unit: 'V' }, { name: 'current_type', value: 'AC' }];
  expect(conditionsText(conditions)).toBe('voltage=415 V; current_type=AC');
  expect(parseConditions('voltage=415 V; current_type=AC')).toEqual(conditions);
  expect(parseConditions('  ')).toBeNull();
  expect(nextPartnerCode(['R-A', 'R-B'])).toBe('R-C');
});

test('own products list shows file and edited rows and the detail of a picked product', async () => {
  live();
  renderApp('/tft/catalog');
  const grid = await screen.findByRole('table', { name: '자사 제품' });
  expect(await within(grid).findByText('기본 자료')).toBeTruthy();
  expect(within(grid).getByText('수정 2판')).toBeTruthy();
  expect(within(grid).getByText('단종')).toBeTruthy();
  fireEvent.click(within(grid).getByRole('button', { name: 'MCCB-1' }));
  const detail = await screen.findByRole('table', { name: '사양' });
  expect(within(detail).getByText('100 AF')).toBeTruthy();
  expect(within(detail).getByText('voltage=415 V')).toBeTruthy();
  expect(screen.getAllByRole('link', { name: '제품 목록' }).length).toBeGreaterThan(0);
});

test('registering an own product sends the vocabulary key and a fresh version', async () => {
  const calls = live();
  renderApp('/tft/catalog');
  const register = await screen.findByRole('button', { name: '자사 제품 등록' }) as HTMLButtonElement;
  await waitFor(() => expect(register.disabled).toBe(false));
  fireEvent.click(register);
  const dialog = await screen.findByRole('dialog', { name: '자사 제품 등록' });
  const save = within(dialog).getByRole('button', { name: '등록' }) as HTMLButtonElement;
  expect(save.disabled).toBe(true);
  fireEvent.change(within(dialog).getByLabelText('제품 코드'), { target: { value: 'MCCB-1' } });
  expect(within(dialog).getByRole('alert').textContent).toContain('이미 있는 제품 코드');
  fireEvent.change(within(dialog).getByLabelText('제품 코드'), { target: { value: 'NEW-1' } });
  fireEvent.change(within(dialog).getByLabelText('사양 1 속성'), { target: { value: 'rated_current' } });
  fireEvent.change(within(dialog).getByLabelText('사양 1 값'), { target: { value: '630' } });
  fireEvent.change(within(dialog).getByLabelText('사양 1 단위'), { target: { value: 'A' } });
  expect(save.disabled).toBe(false);
  fireEvent.click(save);
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  const sent = calls.find(c => c.init?.method === 'PUT');
  expect(sent?.url).toContain('/api/v1/tft/catalog/own-products/NEW-1');
  expect((sent?.init?.headers as Record<string, string>)['X-CSRF-Token']).toBe('session-test');
  expect(JSON.parse(String(sent?.init?.body))).toMatchObject({ expected_version: 0, family: 'LV_BREAKER', spec_facts: [{ attribute: 'rated_current', value: '630', unit: 'A', conditions: null, evidence_id: '제품 목록 등록' }] });
});

test('editing a partner shows the server conflict in the dialog', async () => {
  live();
  renderApp('/tft/catalog');
  fireEvent.click(await screen.findByRole('tab', { name: /협력사/ }));
  fireEvent.click(await screen.findByRole('button', { name: '가상 협력사 P-A' }));
  const edit = await screen.findByRole('button', { name: '수정' }) as HTMLButtonElement;
  await waitFor(() => expect(edit.disabled).toBe(false));
  fireEvent.click(edit);
  const dialog = await screen.findByRole('dialog', { name: /협력사 수정/ });
  expect(within(dialog).getByText(/3판이 됩니다/)).toBeTruthy();
  fireEvent.click(within(dialog).getByRole('button', { name: '수정 저장' }));
  expect((await within(dialog).findByRole('alert')).textContent).toContain('다른 협력사가 이미 쓰는 제품 코드');
});
