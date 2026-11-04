import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import Tickets from './Tickets';
import TicketDetail from './TicketDetail';
import CsAdmin from './CsAdmin';
import CsImprovements from './CsImprovements';
import { setApiMode } from '../api/client';
import { jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';
import { assignmentText } from '../ls-shell/cs-labels';

// KAN-214 담당자 자동 배정 화면: 문의 조회의 관리자 단추·미리보기→적용, 문의 처리의 배정 근거, 운영 현황의 기준표.
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

const CS_MANAGER = { id: 'm', display_name: '관리자', roles: ['manager'], managed_departments: ['GCS_L1'] };
const ticket = { id: 't1', subject: 'Breaker trips', external_id: 'ID-1', channel: 'EMAIL', product_code: 'MCCB-ABS-103c', received_at: '2026-10-20T12:00:00Z', data_origin: 'synthetic', workflow_state: 'RECEIVED' };
const proposal = (id: string, extra: object) => ({ ticket_id: id, external_id: `ID-${id}`, subject: null, product_code: 'MCCB-ABS-103c', family: 'LV_BREAKER', body_language: 'th', received_at: '2026-10-20T12:00:00Z', priority: null, matched_language: 'th', matched_family: 'LV_BREAKER', candidate_count: 2, chosen_open_count: 0, ...extra });
const PREVIEW = { rule_version: 'cs-assign-v1', items: [proposal('1', { user_id: 'u-th', reason_code: 'LANGUAGE_AND_FAMILY' }), proposal('2', { body_language: 'pl', family: 'PLC', user_id: null, reason_code: 'NO_MATCH', matched_language: null, matched_family: null, candidate_count: 0, chosen_open_count: null })] };

type Call = { url: string; init?: RequestInit };
function serve(me: object) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); calls.push({ url, init });
    if (url.endsWith('/platform/me')) return new Response(JSON.stringify({ data: me }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'synthetic-csrf' } });
    if (url.includes('/improvement-requests')) return jsonResponse(200, { data: { items: [] } });
    if (url.endsWith('/assignments/preview')) return jsonResponse(200, { data: PREVIEW });
    if (url.endsWith('/assignments/apply')) return jsonResponse(200, { data: { rule_version: 'cs-assign-v1', assigned: 1, skipped: 1, items: [{ ticket_id: '1', outcome: 'ASSIGNED', user_id: 'u-th', reason_code: 'LANGUAGE_AND_FAMILY' }, { ticket_id: '2', outcome: 'NO_MATCH', user_id: null, reason_code: 'NO_MATCH' }] } });
    if (url.includes('/platform/users?')) return jsonResponse(200, { data: { items: [{ user_id: 'u-th', display_name: '가정 상담원 3' }] } });
    if (url.includes('/platform/assignees')) return jsonResponse(200, { data: { items: [{ user_id: 'u-th', display_name: '가정 상담원 3' }] } });
    if (url.includes('/assignments/agents')) return jsonResponse(200, { data: { rule_version: 'cs-assign-v1', items: [] } });
    if (url.includes('/cs/stats/')) return jsonResponse(503, { error: { code: 'X', message: '합성' } });
    return jsonResponse(200, { data: { items: [ticket], next_cursor: null } });
  }));
  return calls;
}
const list = () => { setApiMode('live'); renderAt('/cs/tickets', '/cs/tickets', <Tickets />); };

for (const [who, me] of [['C/S 담당자', { id: 'c', roles: ['cs_agent'], managed_departments: [] }], ['다른 부서 관리자', { id: 'm', roles: ['manager'], managed_departments: ['TS_TRF'] }]] as const) {
  test(`자동 배정 단추: ${who}에게는 잠기고 이유를 보인다`, async () => {
    serve(me); list();
    await screen.findByRole('link', { name: 'Breaker trips' });
    const button = screen.getByRole('button', { name: '자동 배정' }) as HTMLButtonElement;
    await waitFor(() => expect(button.getAttribute('title')).toBe('C/S 부서 관리자만 자동 배정할 수 있습니다'));
    expect(button.disabled).toBe(true);
  });
}

test('C/S 관리자는 미리보기를 보고 배정 적용하면 결과 건수를 보고 목록을 다시 읽는다', async () => {
  const calls = serve(CS_MANAGER); list();
  await screen.findByRole('link', { name: 'Breaker trips' });
  const button = screen.getByRole('button', { name: '자동 배정' }) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  const table = within(await screen.findByRole('table', { name: '자동 배정 미리보기' }));
  expect(await table.findByText('가정 상담원 3')).toBeTruthy();
  expect(table.getByText('태국어')).toBeTruthy(); expect(table.getByText('저압 차단기')).toBeTruthy();
  expect(table.getByText(/언어·제품군 담당 · 2명 중 진행 0건/)).toBeTruthy();
  expect(table.getByText('폴란드어')).toBeTruthy(); expect(table.getByText('맞는 담당자 없음')).toBeTruthy();
  expect(screen.getByText(/배정 대상 1건, 맞는 담당자 없음 1건/)).toBeTruthy();
  // 미리보기는 쓰지 않는다. 적용 전까지 apply 호출이 없다.
  expect(calls.some(c => c.url.endsWith('/assignments/apply'))).toBe(false);
  const listCalls = calls.filter(c => c.url.includes('/api/v1/cs/tickets')).length;
  fireEvent.click(screen.getByRole('button', { name: '배정 적용' }));
  expect(await screen.findByText(/끝남: 배정 1건, 미배정 1건 \(맞는 담당자 없음 1\)/)).toBeTruthy();
  const apply = calls.find(c => c.url.endsWith('/assignments/apply'))!;
  expect(apply.init?.headers).toMatchObject({ 'X-CSRF-Token': 'synthetic-csrf' });
  expect(JSON.parse(String(apply.init?.body))).toEqual({ rule_version: 'cs-assign-v1', items: [{ ticket_id: '1', user_id: 'u-th' }, { ticket_id: '2', user_id: null }] });
  await waitFor(() => expect(calls.filter(c => c.url.includes('/api/v1/cs/tickets')).length).toBeGreaterThan(listCalls));
  expect(screen.queryByRole('button', { name: '배정 적용' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '닫기' }));
  expect(screen.queryByRole('table', { name: '자동 배정 미리보기' })).toBeNull();
});

test('배정 근거 문구는 사유·후보 수·진행 건수를 그대로 옮긴다', () => {
  expect(assignmentText({ reason_code: 'LANGUAGE_AND_FAMILY', matched_language: 'th', matched_family: 'LV_BREAKER', candidate_count: 3, chosen_open_count: 2 })).toBe('자동 배정: 태국어·저압 차단기 담당, 3명 중 진행 2건으로 가장 적음');
  expect(assignmentText({ reason_code: 'LANGUAGE_ONLY', matched_language: 'th', matched_family: null, candidate_count: 1, chosen_open_count: 4 })).toBe('자동 배정: 태국어 담당 (제품군 담당 없음), 진행 4건');
  expect(assignmentText({ reason_code: 'FAMILY_ENGLISH_FALLBACK', matched_language: 'en', matched_family: 'TRANSFORMER', candidate_count: 2, chosen_open_count: 0 })).toBe('자동 배정: 변압기·영어 담당 (문의 언어 담당 없음), 2명 중 진행 0건으로 가장 적음');
});

test('문의 처리는 지금 담당이 규칙으로 정해졌으면 담당 옆에 근거를 한 줄로 보인다', async () => {
  const detail = examples.find(x => x.path === '/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222')!.body;
  const workflow = examples.find(x => x.path.endsWith('/workflow'))!.body as { data: Record<string, unknown> };
  // 담당자 이름 목록(/platform/…)은 비어 있다고 답한다. 이름은 ID 앞부분으로 보인다.
  const answer = (input: RequestInfo | URL, flow: object) => { const url = String(input); return jsonResponse(200, url.includes('/platform/') ? { data: { items: [] } } : url.endsWith('/workflow') ? flow : detail); };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => answer(input, workflow)));
  setApiMode('live'); renderAt('/cs/tickets/x', '/cs/tickets/:ticketId', <TicketDetail />);
  expect(await screen.findByText('자동 배정: 튀르키예어·변압기 담당, 2명 중 진행 3건으로 가장 적음')).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => answer(input, { data: { ...workflow.data, assignment: null } })));
  renderAt('/cs/tickets/x', '/cs/tickets/:ticketId', <TicketDetail />);
  await screen.findByText('문의번호');
  await waitFor(() => expect(document.querySelector('.assign-why')).toBeNull());
});

test('운영 현황은 담당자 배정 기준을 한국어 이름으로 읽기 전용으로 보이고 가정 자료라고 적는다', async () => {
  const overview = examples.find(x => x.path === '/api/v1/cs/stats/overview')!.body;
  const agents = examples.find(x => x.path === '/api/v1/cs/assignments/agents')!.body as { data: { items: { user_id: string }[] } };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/assignments/agents')) return jsonResponse(200, agents);
    if (url.includes('/platform/users?')) return jsonResponse(200, { data: { items: [{ user_id: agents.data.items[0].user_id, display_name: '시연 C/S 담당자' }] } });
    return jsonResponse(200, url.includes('/owners') ? { data: { period: { start: null, end: null }, items: [] } } : overview);
  }));
  setApiMode('live'); renderAt('/cs/admin', '/cs/admin', <CsAdmin />);
  const section = within(await screen.findByRole('region', { name: '담당자 배정 기준' }));
  expect(await section.findByText('시연 C/S 담당자')).toBeTruthy();
  expect(section.getByText('영어·튀르키예어·러시아어')).toBeTruthy();
  expect(section.getByText('변압기·중압 차단기·배전반')).toBeTruthy();
  expect(section.getByText(/가정 자료/, { selector: 'small' })).toBeTruthy();
  // 아직 이름을 못 찾은 사람은 ID 앞부분으로 보인다. 고치는 단추는 없다.
  expect(section.getByText(/^사용자 88888888/)).toBeTruthy();
  expect(section.queryByRole('button')).toBeNull();
});

// 개선요청 목록의 미배정 자동 배정: 자동 배정 전에 만들어져 C/S 담당이 빈 요청을 관리자가 채운다.
const request = (id: string, extra: object) => ({ id, origin: 'SYSTEM_CANDIDATE', problem_statement: `합성 요청 ${id}`, request_type: 'FAULT', product_code: 'MCCB-ABS-103c', fault_id: 'F-01', doc_signal: null, status: 'DRAFT', version: 1, evidence_count: 1, cs_owner: null, created_at: '2026-10-20T00:00:00Z', ...extra });
const REQUESTS = [request('r1', {}), request('r2', { status: 'COMPLETED' }), request('r3', { cs_owner: 'u-lv' }), request('r4', { product_code: 'PLC-XGI-CPUU', fault_id: null }), request('d1', { request_type: 'DOCUMENT' })];
const requestProposal = (id: string, extra: object) => ({ request_id: id, request_type: 'FAULT', product_code: 'MCCB-ABS-103c', fault_id: 'F-01', family: 'LV_BREAKER', status: 'DRAFT', created_at: '2026-10-20T00:00:00Z', matched_family: 'LV_BREAKER', candidate_count: 2, chosen_open_count: 1, ...extra });
const REQUEST_PREVIEW = { rule_version: 'cs-assign-request-v1', items: [requestProposal('r1', { user_id: 'u-lv', reason_code: 'FAMILY' }), requestProposal('r4', { product_code: 'PLC-XGI-CPUU', fault_id: null, family: 'PLC', user_id: null, reason_code: 'NO_MATCH', matched_family: null, candidate_count: 0, chosen_open_count: null })] };
function serveRequests(me: object) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); calls.push({ url, init });
    if (url.endsWith('/platform/me')) return new Response(JSON.stringify({ data: me }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'synthetic-csrf' } });
    if (url.endsWith('/assignments/improvements/preview')) return jsonResponse(200, { data: REQUEST_PREVIEW });
    if (url.endsWith('/assignments/improvements/apply')) return jsonResponse(200, { data: { rule_version: 'cs-assign-request-v1', assigned: 1, skipped: 1, items: [{ request_id: 'r1', outcome: 'ASSIGNED', user_id: 'u-lv', reason_code: 'FAMILY' }, { request_id: 'r4', outcome: 'NO_MATCH', user_id: null, reason_code: 'NO_MATCH' }] } });
    if (url.includes('/platform/users?')) return jsonResponse(200, { data: { items: [{ user_id: 'u-lv', display_name: '가정 상담원 1' }] } });
    if (url.includes('/cs/faults')) return jsonResponse(200, { data: { items: [] } });
    return jsonResponse(200, { data: { items: REQUESTS } });
  }));
  return calls;
}
const requests = () => { setApiMode('live'); renderAt('/cs/improvements', '/cs/improvements', <CsImprovements />); };

test('개선요청 목록은 누구에게나 미배정 건수(고장 요청, 담당 없음, 완료 제외)를 보인다', async () => {
  serveRequests({ id: 'c', roles: ['cs_agent'], managed_departments: [] }); requests();
  await screen.findByRole('table', { name: '개선요청 목록' });
  // r1·r4만 센다: r2는 완료, r3은 담당 있음, d1은 문서 요청(목록 밖).
  expect(screen.getByText('미배정 2건')).toBeTruthy();
  // 목록에서 직접 배정하는 버튼은 두지 않는다(새 요청은 만들 때 규칙으로 배정된다).
  expect(screen.queryByRole('button', { name: '미배정 자동 배정' })).toBeNull();
});


// 관리자 메뉴는 통계/보고서뿐이라 배정 기능을 운영 현황에 모았다.
const admin = () => { setApiMode('live'); renderAt('/cs/admin', '/cs/admin', <CsAdmin />); };
test('운영 현황의 담당 배정: C/S 부서 관리자는 미배정 자동 배정과 선택 담당 변경을 한다', async () => {
  serve(CS_MANAGER); admin();
  const section = within(await screen.findByRole('region', { name: '담당 배정' }));
  expect(await section.findByText('Breaker trips')).toBeTruthy();
  expect(section.getByText(/미배정 1건/)).toBeTruthy();
  const change = section.getByRole('button', { name: /선택 담당 변경/ }) as HTMLButtonElement;
  expect(change.disabled).toBe(true);
  fireEvent.click(section.getByRole('checkbox', { name: 'ID-1 선택' }));
  expect((section.getByRole('button', { name: /선택 담당 변경 \(1\)/ }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(section.getByRole('button', { name: '미배정 문의 자동 배정' }));
  expect(await screen.findByRole('table', { name: '자동 배정 미리보기' })).toBeTruthy();
});
test('운영 현황의 담당 배정은 C/S 부서 관리자가 아니면 보이지 않는다', async () => {
  serve({ id: 'm', roles: ['manager'], managed_departments: ['TS_TRF'] }); admin();
  await screen.findByRole('heading', { name: /운영 현황/ });
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u).endsWith('/platform/me'))).toBe(true));
  expect(screen.queryByRole('region', { name: '담당 배정' })).toBeNull();
});
