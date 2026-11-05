import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import Tickets from './Tickets';
import TicketDetail from './TicketDetail';
import CsAssign from './CsAssign';
import CsImprovements from './CsImprovements';
import { setApiMode } from '../api/client';
import { jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';
import { assignmentText } from '../ls-shell/cs-labels';

// KAN-214 담당자 자동 배정 화면: 문의 처리의 배정 근거, 업무 배정의 기준표. 문의 조회의 자동 배정 단추는 KAN-235에서 뺐다.
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
    if (url.includes('/assignments/improvements/unmatched')) return jsonResponse(200, { data: { items: UNMATCHED_REQUESTS, has_more: false } });
    if (url.includes('/assignments/unmatched')) return jsonResponse(200, { data: { items: UNMATCHED_TICKETS, has_more: true } });
    return jsonResponse(200, { data: { items: [ticket], next_cursor: null } });
  }));
  return calls;
}
// 맞는 담당자 없음 목록(KAN-234): 서버가 최신 판단이 NO_MATCH인 것만 사유 재료와 함께 준다.
const unmatchedTicket = (id: string, extra: object) => ({ ticket_id: id, external_id: `MAIL-${id}`, subject: 'A PLC nem indul', subject_ko: null, channel: 'EMAIL', product_code: 'PLC-NX-32', body_language: 'hu', received_at: '2026-11-05T09:00:00Z', reason_code: 'NO_MATCH', language: 'hu', family: null, trigger: 'INTAKE', decided_at: '2026-11-05T09:00:00Z', ...extra });
const UNMATCHED_TICKETS = [unmatchedTicket('u1', {}), unmatchedTicket('u2', { subject: 'Falownik', subject_ko: '인버터 고장', product_code: 'PLC-XGI-CPUU', body_language: 'pl-PL', language: 'pl', family: 'PLC' })];
const UNMATCHED_REQUESTS = [{ request_id: 'r9', request_type: 'FAULT', product_code: 'PLC-XGI-CPUU', fault_id: 'FLT-031', status: 'DRAFT', created_at: '2026-11-05T00:00:00Z', reason_code: 'NO_MATCH', family: 'PLC', trigger: 'CANDIDATE', decided_at: '2026-11-05T00:00:00Z' }];
const list = () => { setApiMode('live'); renderAt('/cs/tickets', '/cs/tickets', <Tickets />); };

// KAN-235: 배정은 접수 때 시스템이 하고, 관리자는 업무 배정 화면에서 맞는 담당자 없는 건만 지정한다. 문의 조회에는 자동 배정 단추가 없다.
test('문의 조회에는 C/S 관리자에게도 자동 배정 단추가 없고 배정 API를 부르지 않는다', async () => {
  const calls = serve(CS_MANAGER); list();
  await screen.findByRole('link', { name: 'Breaker trips' });
  expect(screen.queryByRole('button', { name: '자동 배정' })).toBeNull();
  expect(calls.some(c => c.url.includes('/assignments/'))).toBe(false);
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

test('업무 배정은 담당자 배정 기준을 한국어 이름으로 읽기 전용으로 보이고 가정 자료라고 적는다', async () => {
  const overview = examples.find(x => x.path === '/api/v1/cs/stats/overview')!.body;
  const agents = examples.find(x => x.path === '/api/v1/cs/assignments/agents')!.body as { data: { items: { user_id: string }[] } };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/assignments/agents')) return jsonResponse(200, agents);
    if (url.includes('/platform/users?')) return jsonResponse(200, { data: { items: [{ user_id: agents.data.items[0].user_id, display_name: '시연 C/S 담당자' }] } });
    return jsonResponse(200, url.includes('/owners') ? { data: { period: { start: null, end: null }, items: [] } } : overview);
  }));
  setApiMode('live'); renderAt('/cs/admin/assign', '/cs/admin/assign', <CsAssign />);
  const section = within(await screen.findByRole('region', { name: '담당자 배정 기준' }));
  expect(await section.findByText('시연 C/S 담당자')).toBeTruthy();
  expect(section.getByText('영어·튀르키예어·러시아어')).toBeTruthy();
  expect(section.getByText('변압기·중압 차단기·배전반')).toBeTruthy();
  expect(section.getByText(/가정 자료 · 규칙/, { selector: '.meta' })).toBeTruthy();
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


// 관리자 메뉴는 통계/보고서뿐이라 배정 기능을 업무 배정(CS0902)에 모았다.
const admin = () => { setApiMode('live'); renderAt('/cs/admin/assign', '/cs/admin/assign', <CsAssign />); };
test('업무 배정: 자동 배정 단추 없이 맞는 담당자 없는 문의를 서버 사유로 보이고, 선택 담당 변경을 한다', async () => {
  const calls = serve(CS_MANAGER); admin();
  const section = within(await screen.findByRole('region', { name: '담당 배정' }));
  expect(await section.findByText('Breaker trips')).toBeTruthy();
  // 맞는 담당자 없는 문의는 서버 목록 그대로다. 진행 문의(Breaker trips)는 판단 기록이 없어 들어오지 않는다.
  const unmatched = within(screen.getByRole('region', { name: '맞는 담당자 없는 문의' }));
  expect(await unmatched.findByText('헝가리어 가능자 없음 · 미등록 제품 PLC-NX-32')).toBeTruthy();
  expect(unmatched.getByText('폴란드어 가능자 없음 · PLC·영어 담당 없음')).toBeTruthy();
  expect(unmatched.getByText('인버터 고장')).toBeTruthy();
  expect(unmatched.getByText('헝가리어')).toBeTruthy();
  expect(unmatched.queryByText('Breaker trips')).toBeNull();
  // 상한보다 많으면 건수에 +를 붙인다. 건별 상세 조회는 하지 않는다.
  expect(screen.getByRole('tab', { name: /문의\s*2\+/ })).toBeTruthy();
  expect(calls.some(c => /\/cs\/tickets\/u\d/.test(c.url))).toBe(false);
  expect(calls.find(c => c.url.includes('/assignments/unmatched'))!.url).toContain('limit=100');
  fireEvent.click(unmatched.getAllByRole('button', { name: '담당 지정' })[0]);
  expect(await screen.findByText(/MAIL-u1/)).toBeTruthy();
  const change = section.getByRole('button', { name: /선택 담당 변경/ }) as HTMLButtonElement;
  expect(change.disabled).toBe(true);
  fireEvent.click(section.getByRole('checkbox', { name: 'ID-1 선택' }));
  expect((section.getByRole('button', { name: /선택 담당 변경 \(1\)/ }) as HTMLButtonElement).disabled).toBe(false);
  // 새 문의는 접수 때 규칙으로 배정되므로 관리자 일괄 자동 배정 단추는 두지 않는다.
  expect(screen.queryByRole('button', { name: '미배정 문의 자동 배정' })).toBeNull();
});
test('업무 배정의 담당 배정은 C/S 부서 관리자가 아니면 보이지 않는다', async () => {
  serve({ id: 'm', roles: ['manager'], managed_departments: ['TS_TRF'] }); admin();
  await screen.findByRole('heading', { name: /업무 배정/ });
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u).endsWith('/platform/me'))).toBe(true));
  expect(screen.queryByRole('region', { name: '담당 배정' })).toBeNull();
});
test('업무 배정: 맞는 담당자 없는 개선요청은 서버 목록과 제품군 사유를 보인다', async () => {
  serve(CS_MANAGER); setApiMode('live'); renderAt('/cs/admin/assign?tab=requests', '/cs/admin/assign', <CsAssign />);
  const unmatched = within(await screen.findByRole('region', { name: '맞는 담당자 없는 개선요청' }));
  expect(await unmatched.findByText('PLC 제품군 담당자 없음')).toBeTruthy();
  expect(unmatched.getByRole('link', { name: 'PLC-XGI-CPUU · FLT-031' }).getAttribute('href')).toBe('/cs/improvements/r9');
});
