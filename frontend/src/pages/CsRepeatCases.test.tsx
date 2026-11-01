import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import CsRepeatCases from './CsRepeatCases';
import { setApiMode } from '../api/client';
import { addEvidence } from '../api/cs-repeat-cases';
import type { ImprovementDetail } from '../api/cs-improvements';
import type { useBusinessAccess } from '../components/BusinessAccess';
import { errorBody, jsonResponse, renderApp, renderAt } from '../test/render';

// 운영 빌드의 권한 확인을 대신한다. null이면 실제 훅(개발 모드에서는 바로 열림)을 쓴다.
let gate: ReturnType<typeof useBusinessAccess> | null = null;
vi.mock('../components/BusinessAccess', async original => {
  const actual = await original<typeof import('../components/BusinessAccess')>();
  return { ...actual, useBusinessAccess: (business: 'cs' | 'tft') => gate ?? actual.useBusinessAccess(business) };
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); gate = null; sessionStorage.clear(); });

const ID = '11111111-1111-4111-8111-111111111111';
const ME = '99999999-9999-4999-8999-999999999999';
const T1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1';
const T2 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2';
const T3 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3';
const T4 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4';
const T5 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5';
const C3 = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc3';
const E1 = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1';
const E2 = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee2';
const E4 = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee4';

const summary = { id: ID, request_type: 'FAULT', origin: 'SYSTEM_CANDIDATE', product_code: 'SYN-INV-1', fault_id: 'FLT-001', doc_signal: null, status: 'DRAFT', version: 3, cs_owner: null, problem_statement: '합성 반복 과전류 정지', evidence_count: 2, created_at: '2026-10-30T01:00:00Z' };
type Evidence = ImprovementDetail['data']['evidence'][number];
const evidence = (id: string, ticket: string, no: string, caseNo: string, extra: Partial<Evidence> = {}): Evidence => ({ id, ticket_id: ticket, ticket_external_id: no, case_id: `bbbbbbbb-bbbb-4bbb-8bbb-${ticket.slice(-12)}`, case_external_id: caseNo, ticket_subject: `제목 ${no}`, ticket_received_at: '2026-10-28T00:00:00Z', customer_external_id: `CUST-${no}`, added_by: null, added_at: '2026-10-29T01:00:00Z', removed_by: null, removed_at: null, removal_reason: null, ...extra });
function detailBody(status = 'DRAFT', version = 3) {
  return { data: {
    request: { id: ID, request_type: 'FAULT', status, version },
    evidence: [
      evidence(E1, T1, 'SYN-T-1', 'SYN-RC-1'),
      evidence(E2, T2, 'SYN-T-2', 'SYN-RC-2', { added_by: ME, added_at: '2026-10-29T02:00:00Z' }),
      evidence(E4, T4, 'SYN-T-4', 'SYN-RC-4', { removed_by: ME, removed_at: '2026-10-29T03:00:00Z', removal_reason: '합성 조건 불일치' }),
    ],
    departments: [], replies: [], actions: [], next_owners: [],
  } };
}
const caseRow = (ticket: string, no: string, extra = {}) => ({ row_kind: 'EVIDENCE', ticket_id: ticket, ticket_external_id: no, ticket_subject: `제목 ${no}`, ticket_received_at: '2026-10-28T00:00:00Z', customer_external_id: `CUST-${no}`, product_code: 'SYN-INV-1', case_id: `bbbbbbbb-bbbb-4bbb-8bbb-${ticket.slice(-12)}`, case_external_id: `SYN-RC-${no.slice(-1)}`, symptom: `증상 ${no}`, root_cause_type: 'FIRMWARE', root_cause: `원인 ${no}`, action_taken: `조치 ${no}`, resolved_by_dept: 'RND_FW', known_at: '2026-10-28T00:00:00Z', ...extra });
const comparisonBody = { data: { request_id: ID, items: [caseRow(T1, 'SYN-T-1'), caseRow(T2, 'SYN-T-2', { root_cause: '다른 원인', root_cause_type: 'SYN_RAW_CODE', action_taken: null }), caseRow(T3, 'SYN-T-3', { row_kind: 'CANDIDATE', case_id: C3, case_external_id: 'SYN-RC-3', customer_external_id: 'CUST-3', root_cause: '후보 원인' })], hidden_case_count: 0 } };
const poolBody = { data: { as_of: '2026-10-31T00:00:00Z', rules_version: 'synthetic', items: [
  { request_id: ID, evidence: [
    { ticket_id: T1, case_id: 'x', customer_id: 'CUST-SYN-T-1', known_at: '2026-10-28T00:00:00Z' },
    { ticket_id: T3, ticket_external_id: 'SYN-T-3', case_id: C3, case_external_id: 'SYN-RC-3', ticket_subject: '제목 SYN-T-3', ticket_received_at: '2026-10-28T00:00:00Z', customer_id: 'CUST-3', known_at: '2026-10-28T00:00:00Z' },
    { ticket_id: T5, ticket_external_id: 'SYN-T-5', case_id: null, case_external_id: null, ticket_subject: null, ticket_received_at: '2026-10-28T00:00:00Z', customer_id: null, known_at: '2026-10-28T00:00:00Z' },
  ] },
  { request_id: '22222222-2222-4222-8222-222222222222', evidence: [{ ticket_id: 'other-ticket', case_id: 'y', customer_id: 'OTHER', known_at: '2026-10-28T00:00:00Z' }] },
] } };

type Reply = [number, unknown];
type Routes = { list?: Reply; detail?: Reply | (() => Reply); compare?: Reply; pool?: Reply; write?: Reply | (() => Reply) };
function serve(routes: Routes = {}) {
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path === '/api/v1/platform/me') return new Response(JSON.stringify({ data: { id: ME, roles: ['cs_agent'] } }), { headers: { 'X-CSRF-Token': 'synthetic', 'Content-Type': 'application/json' } });
    const pick = (reply: Reply | (() => Reply)) => typeof reply === 'function' ? reply() : reply;
    const [status, body] = init?.method === 'POST' ? pick(routes.write ?? [200, detailBody()])
      : path.endsWith('?origin=SYSTEM_CANDIDATE') ? routes.list ?? [200, { data: { items: [summary] } }]
        : path.endsWith('/case-comparison?include_candidates=true') ? routes.compare ?? [200, comparisonBody]
          : path === '/api/v1/cs/improvement-candidates' ? routes.pool ?? [200, poolBody]
            : pick(routes.detail ?? [200, detailBody()]);
    return jsonResponse(status, body);
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}
function view(path = `/cs/repeat-cases/${ID}`) {
  setApiMode('live');
  return renderAt(path, 'cs/repeat-cases/:requestId?', <CsRepeatCases />);
}
const posts = (fetch: ReturnType<typeof serve>) => fetch.mock.calls.filter(([, init]) => init?.method === 'POST').map(([url, init]) => [String(url), JSON.parse(String(init!.body))]);
const detailGets = (fetch: ReturnType<typeof serve>) => fetch.mock.calls.filter(([url, init]) => String(url) === `/api/v1/cs/improvement-requests/${ID}` && init?.method !== 'POST').length;
const rowsOf = async () => within(await screen.findByRole('table', { name: '근거 문의' })).getAllByRole('row').slice(1);
const pickRow = (n: number) => fireEvent.click(screen.getByRole('checkbox', { name: `${n}행 선택` }));
const compareCells = (label: string) => within(screen.getByRole('table', { name: '비교' })).getByText(label).closest('tr')!.querySelectorAll('td');

test('포함·후보·뺌을 구분하고 후보는 현재 요청의 아직 넣지 않은 문의만 보인다', async () => {
  serve();
  view();
  const rows = await rowsOf();
  await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(5));
  const text = (await rowsOf()).map(row => [...row.querySelectorAll('td')].slice(2).map(td => td.textContent));
  expect(text).toEqual([
    ['SYN-T-1', 'SYN-RC-1', 'CUST-SYN-T-1', '2026-10-28', '제목 SYN-T-1', '포함'],
    ['SYN-T-2', 'SYN-RC-2', 'CUST-SYN-T-2', '2026-10-28', '제목 SYN-T-2', '포함'],
    ['SYN-T-3', 'SYN-RC-3', 'CUST-3', '2026-10-28', '제목 SYN-T-3', '후보'],
    ['SYN-T-5', '해결 기록 없음', '-', '2026-10-28', '미기록', '해결 기록 없음'],
    ['SYN-T-4', 'SYN-RC-4', 'CUST-SYN-T-4', '2026-10-28', '제목 SYN-T-4', '뺌'],
  ]);
  expect(rows.length).toBeGreaterThan(0);
  expect(within(rows[0]).getByRole('link', { name: 'SYN-T-1' }).getAttribute('href')).toBe(`/cs/tickets/${T1}`);
  expect(screen.queryByText('other-ticket')).toBeNull();
  expect(screen.getByRole('link', { name: '요청으로 이동' }).getAttribute('href')).toBe(`/cs/improvements/${ID}`);
});

test('근거 변경 기록은 추가와 뺌을 오래된 순으로 처리자·사유와 함께 보인다', async () => {
  serve();
  view();
  const table = await screen.findByRole('table', { name: '근거 변경 기록' });
  await waitFor(() => expect(within(table).getAllByRole('row')).toHaveLength(5));
  const text = within(table).getAllByRole('row').slice(1).map(row => [...row.querySelectorAll('td')].slice(1).map(td => td.textContent));
  expect(table.textContent).not.toContain(ME);
  expect(text).toEqual([
    ['추가', 'SYN-RC-1', 'SYN-T-1', '시스템', '-'],
    ['추가', 'SYN-RC-4', 'SYN-T-4', '시스템', '-'],
    ['추가', 'SYN-RC-2', 'SYN-T-2', '담당자', '-'],
    ['뺌', 'SYN-RC-4', 'SYN-T-4', '담당자', '합성 조건 불일치'],
  ]);
});

test('첫 포함 행을 기준으로 고정해 번호 머리의 2열로 비교하고 원인 유형은 표시 이름, 빈 값은 미기록이다', async () => {
  serve();
  view();
  const table = await screen.findByRole('table', { name: '비교' });
  expect(screen.getByRole('heading', { name: /비교, 2\/5행 SYN-RC-2 \(기준 SYN-T-1\)/ })).toBeTruthy();
  expect([...table.querySelectorAll('th')].map(th => th.textContent)).toEqual(['항목', '기준 SYN-T-1', 'SYN-RC-2']);
  expect([...compareCells('원인')].map(td => td.textContent)).toEqual(['원인', '원인 SYN-T-1', '다른 원인']);
  expect([...compareCells('원인 유형')].map(td => td.textContent)).toEqual(['원인 유형', '펌웨어', '이름 없는 유형']);
  expect([...compareCells('조치')].map(td => td.textContent)).toEqual(['조치', '조치 SYN-T-1', '미기록']);
  expect([...compareCells('해결 부서')].map(td => td.textContent)).toEqual(['해결 부서', '펌웨어 개발', '펌웨어 개발']);
  expect(document.body.textContent).not.toContain('SYN_RAW_CODE');
  expect(screen.queryByRole('combobox', { name: '기준 행' })).toBeNull();
  pickRow(1);
  expect([...table.querySelectorAll('th')].map(th => th.textContent)).toEqual(['항목', '기준 SYN-T-1', 'SYN-RC-1']);
});

test('다음 할 일 줄은 조건 줄 위에서 고른 행과 서버 값으로 안내하고 후보 건수를 요약한다', async () => {
  serve();
  const { container } = view();
  await screen.findByRole('table', { name: '비교' });
  await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(5));
  const next = container.querySelector('.kv-next')!;
  expect(next.compareDocumentPosition(container.querySelector('.kv-cond')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(next.textContent).toBe('다음SYN-T-2 뺄지 판단, 비교표 준비됨 / 요청 작성 중, 근거 사례 2건 / 후보 2건');
  expect(screen.getByText('후보 문의 2건')).toBeTruthy();
  expect(screen.getByRole('table', { name: '근거 문의' }).querySelector('thead input')).toBeNull();
  pickRow(3);
  expect(next.textContent).toContain('SYN-T-3 넣을지 판단, 비교표 준비됨');
  pickRow(4);
  expect(next.textContent).toContain('SYN-T-5 해결 기록이 없어 넣을 수 없음');
});

test('열린 화면 탭 이름은 반복 사례다', async () => {
  serve();
  setApiMode('live');
  renderApp(`/cs/repeat-cases/${ID}`);
  await screen.findByRole('tab', { name: '반복 사례', selected: true });
});

test('후보 행도 같은 종류와 해결 기록의 비교표를 보이고 근거 추가는 상세 판본으로 보낸다', async () => {
  const added = detailBody('DRAFT', 4);
  added.data.evidence.push(evidence('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee3', T3, 'SYN-T-3', 'SYN-RC-3', { case_id: C3 }));
  const fetch = serve({ write: [200, added] });
  view();
  await screen.findByRole('table', { name: '비교' });
  await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(5));
  pickRow(3);
  expect([...compareCells('원인')].map(td => td.textContent)).toEqual(['원인', '원인 SYN-T-1', '후보 원인']);
  expect(screen.getByRole('link', { name: '원문 보기' }).getAttribute('href')).toBe(`/cs/tickets/${T3}`);
  const compareBefore = fetch.mock.calls.filter(([url]) => String(url).endsWith('/case-comparison?include_candidates=true')).length;
  fireEvent.click(screen.getByRole('button', { name: '근거 추가' }));
  await screen.findByText('근거에 추가했습니다.');
  expect(document.querySelector('.kv-next')!.textContent).toContain('근거 사례 3건');
  expect((await rowsOf()).filter(row => row.querySelectorAll('td')[7].textContent === '포함')).toHaveLength(3);
  expect(screen.getByText(/판본 4/)).toBeTruthy();
  expect(posts(fetch)).toEqual([[`/api/v1/cs/improvement-requests/${ID}/evidence`, { ticket_id: T3, case_id: C3, version: 3 }]]);
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => String(url).endsWith('/case-comparison?include_candidates=true')).length).toBe(compareBefore + 1));
});

test('해결 기록이 없는 후보와 뺀 행은 근거에 넣을 수 없다', async () => {
  serve();
  view();
  await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(5));
  pickRow(4);
  expect((screen.getByRole('button', { name: '근거 추가' }) as HTMLButtonElement).disabled).toBe(true);
  pickRow(5);
  expect(screen.getByText(/근거에서 뺀 문의는 비교표가 없습니다/)).toBeTruthy();
  expect((screen.getByRole('button', { name: '근거 추가' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByLabelText('빼는 사유') as HTMLTextAreaElement).disabled).toBe(true);
});

test('근거에서 빼기는 사유가 있어야 하고 근거 ID·사유·판본을 보낸다', async () => {
  const removed = detailBody('DRAFT', 4);
  removed.data.evidence[1] = { ...removed.data.evidence[1], removed_by: ME, removed_at: '2026-10-31T01:00:00Z', removal_reason: '합성 조건이 다름' };
  const fetch = serve({ write: [200, removed] });
  view();
  await screen.findByRole('table', { name: '비교' });
  pickRow(2);
  const button = screen.getByRole('button', { name: '근거에서 빼기' }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('빼는 사유'), { target: { value: '   ' } });
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('빼는 사유'), { target: { value: ' 합성 조건이 다름 ' } });
  fireEvent.click(button);
  await screen.findByText('근거에서 뺐습니다.');
  expect(document.querySelector('.kv-next')!.textContent).toContain('근거 사례 1건');
  expect((await rowsOf()).filter(row => row.querySelectorAll('td')[7].textContent === '포함')).toHaveLength(1);
  expect(screen.getByText(/판본 4/)).toBeTruthy();
  expect(posts(fetch)).toEqual([[`/api/v1/cs/improvement-requests/${ID}/evidence/${E2}/remove`, { reason: '합성 조건이 다름', version: 3 }]]);
});

test('409 판본 충돌은 안내하고 최신 내용을 다시 읽는다', async () => {
  let version = 3;
  const fetch = serve({ detail: () => [200, detailBody('DRAFT', version)], write: () => { version = 4; return [409, { error: { code: 'CS_REQUEST_VERSION_CONFLICT', message: 'stale', retryable: false } }]; } });
  view();
  await screen.findByRole('table', { name: '비교' });
  pickRow(2);
  fireEvent.change(screen.getByLabelText('빼는 사유'), { target: { value: '합성 사유' } });
  const before = detailGets(fetch);
  fireEvent.click(screen.getByRole('button', { name: '근거에서 빼기' }));
  expect((await screen.findByRole('alert')).textContent).toContain('다른 곳에서 요청이 먼저 바뀌었습니다');
  await waitFor(() => expect(detailGets(fetch)).toBe(before + 1));
  await screen.findByText(/판본 4/);
  expect((screen.getByRole('button', { name: '근거에서 빼기' }) as HTMLButtonElement).disabled).toBe(false);
});

test('결과를 알 수 없으면 다시 보내지 못하게 막고 최신 조회 뒤 풀어 준다', async () => {
  const fetch = serve({ write: [503, errorBody('UNAVAILABLE', '합성 일시 오류')] });
  view();
  await screen.findByRole('table', { name: '비교' });
  pickRow(3);
  fireEvent.click(screen.getByRole('button', { name: '근거 추가' }));
  expect((await screen.findByRole('alert')).textContent).toContain('다시 보내지 말고');
  expect((screen.getByRole('button', { name: '근거 추가' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '근거 추가' }));
  expect(posts(fetch)).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '최신 내용 조회' }));
  await waitFor(() => expect((screen.getByRole('button', { name: '근거 추가' }) as HTMLButtonElement).disabled).toBe(false));
});

test('후보 API가 403이면 후보 행만 빼고 화면은 유지한다', async () => {
  serve({ pool: [403, errorBody('FORBIDDEN', '합성 권한 없음')] });
  view();
  await screen.findByText(/후보 행은 빼고 보여 줍니다/);
  expect(screen.getByText('후보 문의 확인 불가')).toBeTruthy();
  expect(document.querySelector('.kv-next')!.textContent).not.toContain('후보');
  const rows = await rowsOf();
  expect(rows.map(row => row.querySelectorAll('td')[7].textContent)).toEqual(['포함', '포함', '뺌']);
  expect(screen.getByRole('table', { name: '비교' })).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
});

test('완료된 요청은 비교만 하고 근거 입력을 막는다', async () => {
  const fetch = serve({ detail: [200, detailBody('COMPLETED')] });
  view();
  await screen.findByRole('table', { name: '비교' });
  expect(screen.getByText(/완료된 요청이라 근거를 바꿀 수 없음/)).toBeTruthy();
  pickRow(3);
  expect((screen.getByRole('button', { name: '근거 추가' }) as HTMLButtonElement).disabled).toBe(true);
  pickRow(1);
  expect((screen.getByLabelText('빼는 사유') as HTMLTextAreaElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: '근거에서 빼기' }) as HTMLButtonElement).disabled).toBe(true);
  expect(posts(fetch)).toHaveLength(0);
});

test('요청번호 선택 상자는 저장된 시스템 후보만 보여 주고 고르기 전에는 안내한다', async () => {
  const fetch = serve();
  view('/cs/repeat-cases');
  const select = await screen.findByRole('combobox', { name: '요청번호' });
  await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(2));
  expect(String(fetch.mock.calls[0][0])).toBe('/api/v1/cs/improvement-requests?origin=SYSTEM_CANDIDATE');
  expect(screen.getByText('요청번호를 골라 근거 문의를 비교하세요.')).toBeTruthy();
  expect(within(select).getByRole('option', { name: /합성 반복 과전류 정지/ })).toBeTruthy();
  expect(fetch.mock.calls.some(([url]) => String(url).includes('/case-comparison'))).toBe(false);
});

test('빈 목록을 안내한다', async () => {
  serve({ list: [200, { data: { items: [] } }] });
  view('/cs/repeat-cases');
  await screen.findByText('저장된 반복 사례 후보가 없습니다.');
});

test.each([[403, '권한이 없습니다'], [404, '찾을 수 없습니다']])('요청 상세 오류 %i을 안내한다', async (status, title) => {
  serve({ detail: [status, errorBody('FAIL', '합성 오류')] });
  view();
  expect((await screen.findByRole('alert')).textContent).toContain(title);
  expect(screen.queryByRole('table', { name: '근거 문의' })).toBeNull();
});

test('목록 통신 실패 뒤 다시 시도로 복구한다', async () => {
  let fail = true;
  vi.stubGlobal('fetch', vi.fn(async () => fail ? jsonResponse(503, errorBody('UNAVAILABLE', '합성 일시 오류')) : jsonResponse(200, { data: { items: [summary] } })));
  view('/cs/repeat-cases');
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain('요청을 처리하지 못했습니다');
  fail = false;
  fireEvent.click(within(alert).getByRole('button', { name: '다시 시도' }));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
});

test('목업 모드에서는 근거를 보내지 않는다', async () => {
  const fetch = serve();
  setApiMode('mock');
  await expect(addEvidence(ID, { ticket_id: T3, case_id: C3, version: 3 }, new AbortController().signal)).rejects.toMatchObject({ status: 403 });
  expect(fetch).not.toHaveBeenCalled();
});

test('운영 빌드에서 권한이 확인되면 열리고, 역할이 없으면 조회하지 않는다', async () => {
  const fetch = serve();
  gate = { ready: true, operational: true, enabled: true, user: {} as ReturnType<typeof useBusinessAccess>['user'] };
  view();
  await screen.findByRole('table', { name: '근거 문의' });
  cleanup();
  fetch.mockClear();
  gate = { ready: false, operational: true, enabled: true, user: { isPending: false, isError: false, data: { data: { roles: [] } } } as unknown as ReturnType<typeof useBusinessAccess>['user'] };
  view();
  expect(screen.getByRole('alert').textContent).toContain('권한이 없습니다');
  await waitFor(() => expect(fetch).not.toHaveBeenCalled());
});


test('선택 행을 바꾸면 사유가 다른 행에 남지 않고 다시 고른 원래 행도 비어 있다', async () => {
  const fetch = serve();
  view();
  await screen.findByRole('table', { name: '비교' });
  pickRow(2);
  fireEvent.change(screen.getByLabelText('빼는 사유'), { target: { value: '둘째 행 전용 사유' } });
  pickRow(1);
  expect((screen.getByLabelText('빼는 사유') as HTMLTextAreaElement).value).toBe('');
  expect((screen.getByRole('button', { name: '근거에서 빼기' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('빼는 사유'), { target: { value: '첫째 행 사유' } });
  pickRow(3);
  expect((screen.getByLabelText('빼는 사유') as HTMLTextAreaElement).value).toBe('');
  pickRow(2);
  expect((screen.getByLabelText('빼는 사유') as HTMLTextAreaElement).value).toBe('');
  fireEvent.change(screen.getByLabelText('빼는 사유'), { target: { value: '확인한 둘째 행 사유' } });
  fireEvent.click(screen.getByRole('button', { name: '근거에서 빼기' }));
  await screen.findByText('근거에서 뺐습니다.');
  expect(posts(fetch)).toEqual([[`/api/v1/cs/improvement-requests/${ID}/evidence/${E2}/remove`, { reason: '확인한 둘째 행 사유', version: 3 }]]);
});

test.each(['row_kind', 'case_id'])('후보와 %s가 다른 비교값은 대신 쓰지 않는다', async key => {
  const items = comparisonBody.data.items.map(row => row.ticket_id === T3 ? { ...row, [key]: key === 'row_kind' ? 'EVIDENCE' : 'dddddddd-dddd-4ddd-8ddd-dddddddddddd' } : row);
  serve({ compare: [200, { data: { ...comparisonBody.data, items } }] });
  view();
  await screen.findByRole('table', { name: '비교' });
  await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(5));
  pickRow(3);
  expect(screen.getByText('이 후보의 비교표가 없습니다. 원문 보기로 문의를 확인하세요.')).toBeTruthy();
  expect(document.body.textContent).not.toContain('후보 원인');
});


test('근거와 후보가 없을 때 입력 사유 없이 단일 빈 표 안내를 보인다', async () => {
  const body = detailBody();
  body.data.evidence = [];
  serve({ detail: [200, body], pool: [200, { data: { items: [] } }], compare: [200, { data: { request_id: ID, items: [], hidden_case_count: 0 } }] });
  view();
  await screen.findByText('근거 문의와 후보가 없습니다.');
  expect((screen.getByLabelText('빼는 사유') as HTMLTextAreaElement).value).toBe('');
  expect((screen.getByRole('button', { name: '근거에서 빼기' }) as HTMLButtonElement).disabled).toBe(true);
});


test('자동 재조회로 선택 근거가 빠지면 다른 포함 행에 이전 사유를 보내지 않는다', async () => {
  const E3 = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee3';
  let body = detailBody();
  body.data.evidence.push(evidence(E3, T3, 'SYN-T-3', 'SYN-RC-3'));
  const items = [...comparisonBody.data.items.filter(row => row.ticket_id !== T3), caseRow(T3, 'SYN-T-3')];
  const fetch = serve({ detail: () => [200, body], compare: [200, { data: { ...comparisonBody.data, items } }], write: () => [200, body] });
  view();
  await screen.findByRole('table', { name: '비교' });
  fireEvent.change(screen.getByLabelText('빼는 사유'), { target: { value: '둘째 행에만 해당하는 사유' } });
  body = { ...body, data: { ...body.data, evidence: body.data.evidence.map(row => row.id === E2 ? { ...row, removed_at: '2026-11-01T00:00:00Z', removal_reason: '다른 곳에서 뺌' } : row) } };
  fireEvent.click(screen.getByRole('button', { name: /조회/ }));
  await screen.findByText('다른 곳에서 뺌');
  expect((screen.getByLabelText('빼는 사유') as HTMLTextAreaElement).value).toBe('');
  expect((screen.getByRole('button', { name: '근거에서 빼기' }) as HTMLButtonElement).disabled).toBe(true);
  expect(posts(fetch)).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('빼는 사유'), { target: { value: '셋째 문의를 확인한 새 사유' } });
  fireEvent.click(screen.getByRole('button', { name: '근거에서 빼기' }));
  await screen.findByText('근거에서 뺐습니다.');
  expect(posts(fetch)).toEqual([[`/api/v1/cs/improvement-requests/${ID}/evidence/${E3}/remove`, { reason: '셋째 문의를 확인한 새 사유', version: 3 }]]);
});

test('요청 전환은 requestId key로 초기화되어 기존 행과 사유를 인계하지 않는다', async () => {
  const otherId = '22222222-2222-4222-8222-222222222222';
  let other = false;
  serve({ list: [200, { data: { items: [summary, { ...summary, id: otherId, problem_statement: '두 번째 개선요청' }] } }], detail: () => {
    const body = detailBody();
    return [200, { ...body, data: { ...body.data, request: { ...body.data.request, id: other ? otherId : ID } } }];
  } });
  view();
  await screen.findByRole('table', { name: '비교' });
  fireEvent.change(screen.getByLabelText('빼는 사유'), { target: { value: '첫 요청에서만 쓰는 사유' } });
  other = true;
  fireEvent.change(screen.getByRole('combobox', { name: '요청번호' }), { target: { value: otherId } });
  await waitFor(() => expect((screen.getByLabelText('빼는 사유') as HTMLTextAreaElement).value).toBe(''));
  expect((screen.getByRole('button', { name: '근거에서 빼기' }) as HTMLButtonElement).disabled).toBe(true);
});
