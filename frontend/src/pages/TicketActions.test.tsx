import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { useNavigate } from 'react-router-dom';
import TicketDetail from './TicketDetail';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';

const source = examples.find(x => x.path === '/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222')!;
const base = (source.body as { data: Record<string, unknown> }).data;
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

const flow = (state: string, version: number, extra: Record<string, unknown> = {}) => ({ data: { ticket_id: 'x', state, version, owner_user_id: null, closed_at: null, closed_by: null, customer_sent_at: null, dept_results: [], transfers: [], ...extra } });
type Post = { url: string; init: RequestInit };
const assigneesDefault = [{ user_id: 'u1', display_name: '김지원' }, { user_id: 'u2', display_name: '박민수' }];

// 문의 상세·업무 흐름 조회와 쓰기 호출을 한 곳에서 받는 가짜 서버. post가 Promise를 돌려주면 응답을 늦출 수 있다.
function server(state: string, version: number, post: (call: Post) => Response | Promise<Response> = () => jsonResponse(200, flow('IN_REVIEW', version + 1)), analysis?: unknown, detailExtra: Record<string, unknown> = {}, flowExtra: Record<string, unknown> = {}, roles: string[] = ['cs_agent'], assignees: Response | undefined = undefined) {
  const posts: Post[] = [];
  const get = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u1', roles } }), { status: 200, headers: { 'X-CSRF-Token': 'synthetic-csrf' } });
    if (path.includes('/platform/assignees')) return assignees ?? jsonResponse(200, { data: { items: assigneesDefault } });
    if (['POST', 'PUT'].includes(init?.method ?? 'GET')) { const call = { url: path, init: init! }; posts.push(call); return post(call); }
    if (path.endsWith('/workflow')) { get(path); return jsonResponse(200, flow(state, version, flowExtra)); }
    if (path.endsWith('/analysis')) return analysis ? jsonResponse(200, analysis) : jsonResponse(404, errorBody('CS_ANALYSIS_MISSING', '분석 없음'));
    return jsonResponse(200, { data: { ...base, workflow_state: state, ...detailExtra } });
  }));
  return { posts, get };
}
function view() { setApiMode('live'); return renderAt('/cs/tickets/x', '/cs/tickets/:ticketId', <TicketDetail />); }
const openMore = () => fireEvent.click(screen.getByText(/^기타 \d/));
const menu = (name: string) => screen.getByRole('menuitem', { name: new RegExp(`^${name}`) }) as HTMLButtonElement;
const body = (call: Post) => JSON.parse(call.init.body as string);

test('검토 시작은 읽은 판본과 보호 토큰을 보내고 끝나면 최신 상태를 다시 읽는다', async () => {
  const api = server('RECEIVED', 3);
  view(); await screen.findByText('접수', { selector: 'strong' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  await waitFor(() => expect(menu('검토 시작').disabled).toBe(false));
  openMore(); fireEvent.click(menu('검토 시작'));
  await screen.findByText('검토를 시작했습니다.');
  expect(api.posts).toHaveLength(1);
  expect(api.posts[0].url).toMatch(/\/workflow\/start-review$/);
  expect(body(api.posts[0])).toEqual({ version: 3 });
  expect(api.posts[0].init.headers).toMatchObject({ 'X-CSRF-Token': 'synthetic-csrf' });
  await waitFor(() => expect(api.get.mock.calls.length).toBeGreaterThanOrEqual(2));
});

test('부서 검토 요청은 부서와 사유를 받아 판본과 함께 보낸다', async () => {
  const api = server('IN_REVIEW', 5);
  view(); await screen.findByText('검토 중', { selector: 'strong' });
  await waitFor(() => expect((screen.getByRole('button', { name: '부서 검토 요청' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '부서 검토 요청' }));
  const dialog = await screen.findByRole('dialog');
  // 부서나 사유가 비면 보내지 않는다.
  fireEvent.submit(dialog.querySelector('form')!);
  expect(api.posts).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('요청 부서'), { target: { value: 'TS_AUTO' } });
  fireEvent.change(screen.getByLabelText('요청 사유'), { target: { value: '  터치 무반응 확인  ' } });
  fireEvent.submit(dialog.querySelector('form')!);
  await screen.findByText('부서 검토를 요청했습니다.');
  expect(api.posts[0].url).toMatch(/\/workflow\/transfer$/);
  expect(body(api.posts[0])).toEqual({ dept_code: 'TS_AUTO', reason: '터치 무반응 확인', version: 5 });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('요청 취소와 종결은 각자 맞는 상태에서만 열리고 판본을 보낸다', async () => {
  const recall = server('TRANSFERRED', 8);
  const first = view(); await screen.findByText('부서 이관', { selector: 'strong' });
  await waitFor(() => expect(recall.get).toHaveBeenCalled());
  openMore();
  await waitFor(() => expect(menu('요청 취소').disabled).toBe(false));
  expect(menu('종결').disabled).toBe(true);
  fireEvent.click(menu('요청 취소'));
  fireEvent.change(await screen.findByLabelText('취소 사유'), { target: { value: '부서 선택 오류' } });
  fireEvent.submit(screen.getByRole('dialog').querySelector('form')!);
  await screen.findByText('부서 검토 요청을 취소했습니다.');
  expect(recall.posts[0].url).toMatch(/\/workflow\/recall$/);
  expect(body(recall.posts[0])).toEqual({ reason: '부서 선택 오류', version: 8 });
  first.unmount(); vi.unstubAllGlobals();

  const close = server('ANSWERED', 11);
  view(); await screen.findByText('회신 완료', { selector: 'strong' });
  await waitFor(() => expect(close.get).toHaveBeenCalled());
  openMore();
  await waitFor(() => expect(menu('종결').disabled).toBe(false));
  expect(menu('요청 취소').disabled).toBe(true);
  fireEvent.click(menu('종결'));
  fireEvent.click(await screen.findByRole('button', { name: '종결' }));
  await screen.findByText('문의를 종결했습니다.');
  expect(close.posts[0].url).toMatch(/\/workflow\/close$/);
  expect(body(close.posts[0])).toEqual({ version: 11 });
});

test('판본 충돌 409는 같은 요청을 다시 보내지 않고 최신 상태를 읽어 안내한다', async () => {
  const api = server('RECEIVED', 3, () => jsonResponse(409, errorBody('CS_WORKFLOW_VERSION_CONFLICT', 'conflict')));
  view(); await screen.findByText('접수', { selector: 'strong' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  await waitFor(() => expect(menu('검토 시작').disabled).toBe(false));
  openMore(); fireEvent.click(menu('검토 시작'));
  expect((await screen.findByRole('alert')).textContent).toMatch(/다른 사용자가 먼저 상태를 바꿨습니다/);
  expect(api.posts).toHaveLength(1);
  await waitFor(() => expect(api.get.mock.calls.length).toBeGreaterThanOrEqual(2));
});

test('결과를 알 수 없는 오류(5xx)도 다시 보내지 않고 확인을 안내한다', async () => {
  const api = server('RECEIVED', 3, () => jsonResponse(503, errorBody('UNAVAILABLE', '일시 장애')));
  view(); await screen.findByText('접수', { selector: 'strong' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  await waitFor(() => expect(menu('검토 시작').disabled).toBe(false));
  openMore(); fireEvent.click(menu('검토 시작'));
  expect((await screen.findByRole('alert')).textContent).toMatch(/결과를 확인하지 못했습니다/);
  expect(api.posts).toHaveLength(1);
});

test('실행 중에는 같은 동작을 한 번만 보낸다', async () => {
  let release: (response: Response) => void = () => {};
  const api = server('RECEIVED', 3, () => new Promise<Response>(resolve => { release = resolve; }));
  view(); await screen.findByText('접수', { selector: 'strong' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  await waitFor(() => expect(menu('검토 시작').disabled).toBe(false));
  openMore(); const item = menu('검토 시작');
  fireEvent.click(item); fireEvent.click(item);
  await waitFor(() => expect(api.posts).toHaveLength(1));
  expect(menu('검토 시작').disabled).toBe(true);
  release(jsonResponse(200, flow('IN_REVIEW', 4)));
  await screen.findByText('검토를 시작했습니다.');
  expect(api.posts).toHaveLength(1);
});

test('모의 응답 모드에서는 상태를 바꾸는 동작을 열지 않는다', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  setApiMode('mock');
  renderAt('/cs/tickets/22222222-2222-4222-8222-222222222222', '/cs/tickets/:ticketId', <TicketDetail />);
  await screen.findByRole('heading', { name: '문의 처리' });
  await waitFor(() => expect(screen.getByRole('button', { name: '부서 검토 요청' }).getAttribute('title')).toBe('실제 API 개발 모드에서만 실행할 수 있습니다'));
  expect((screen.getByRole('button', { name: '부서 검토 요청' }) as HTMLButtonElement).disabled).toBe(true);
  expect(fetch.mock.calls.every(([, init]) => (init as RequestInit | undefined)?.method !== 'POST')).toBe(true);
});

const field = (value: unknown) => ({ ai_value: value, corrected: false, corrected_value: null, effective_value: value, source: 'ai_draft' });
const returnAnalysis = { data: { ticket_id: 'x', version: 1, corrections: [], drafts: [], runs: [], latest_valid_draft_id: null, latest_valid_run_id: null, latest_run: null, missing_info: { ai: null, effective: null, error: null }, return_subtype_stated: 'NON_DEFECT', rules: { effective: null, first_response_due: {}, resolution_due: {} },
  fields: { category_code: field('RETURN'), secondary_category: field(null), urgency_flag: field(false), provided_info: field([]) } } };

test('반품 확정은 읽은 판본과 보호 토큰을 보내고 끝나면 최신 상태를 다시 읽는다', async () => {
  const api = server('IN_REVIEW', 6, () => jsonResponse(200, flow('IN_REVIEW', 7)), returnAnalysis);
  view(); await screen.findByRole('form', { name: '반품 확정' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText('책임 구분'), { target: { value: 'MANUFACTURING' } });
  fireEvent.change(screen.getByLabelText('대응 내용'), { target: { value: '교체 발송' } });
  await waitFor(() => expect((screen.getByRole('button', { name: '반품 확정' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '반품 확정' }));
  await screen.findByText('반품 사유를 확정했습니다.');
  // 확정에 성공하면 입력 칸을 비운다(다음 확정에 앞 내용이 남지 않게).
  await waitFor(() => expect((screen.getByLabelText('대응 내용') as HTMLTextAreaElement).value).toBe(''));
  expect((screen.getByLabelText('책임 구분') as HTMLSelectElement).value).toBe('');
  expect(api.posts).toHaveLength(1);
  expect(api.posts[0].url).toMatch(/\/workflow\/return-subtype$/);
  expect(body(api.posts[0])).toEqual({ responsibility: 'MANUFACTURING', cause_type: null, non_manufacturing_reason: null, response_note: '교체 발송', version: 6 });
  expect(api.posts[0].init.headers).toMatchObject({ 'X-CSRF-Token': 'synthetic-csrf' });
  await waitFor(() => expect(api.get.mock.calls.length).toBeGreaterThanOrEqual(2));
});

test('반품 확정의 판본 충돌 409는 다시 보내지 않고 최신 상태를 읽어 안내한다', async () => {
  const api = server('IN_REVIEW', 6, () => jsonResponse(409, errorBody('CS_WORKFLOW_VERSION_CONFLICT', 'conflict')), returnAnalysis);
  view(); await screen.findByRole('form', { name: '반품 확정' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText('책임 구분'), { target: { value: 'UNDETERMINED' } });
  fireEvent.change(screen.getByLabelText('대응 내용'), { target: { value: '재검사' } });
  await waitFor(() => expect((screen.getByRole('button', { name: '반품 확정' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '반품 확정' }));
  expect((await screen.findByRole('alert')).textContent).toMatch(/다른 사용자가 먼저 상태를 바꿨습니다/);
  expect(api.posts).toHaveLength(1);
});

test('다른 문의로 옮기면 반품 확정 입력을 비운다(앞 문의 내용이 실려 가지 않는다)', async () => {
  server('IN_REVIEW', 6, undefined, returnAnalysis);
  const Go = () => { const go = useNavigate(); return <button onClick={() => go('/cs/tickets/other')}>다른 문의</button>; };
  setApiMode('live'); renderAt('/cs/tickets/first', '/cs/tickets/:ticketId', <><TicketDetail /><Go /></>);
  await screen.findByRole('form', { name: '반품 확정' });
  fireEvent.change(screen.getByLabelText('책임 구분'), { target: { value: 'MANUFACTURING' } });
  fireEvent.change(screen.getByLabelText('대응 내용'), { target: { value: '앞 문의 내용' } });
  expect((screen.getByLabelText('대응 내용') as HTMLTextAreaElement).value).toBe('앞 문의 내용');
  fireEvent.click(screen.getByRole('button', { name: '다른 문의' }));
  await waitFor(() => expect((screen.getByLabelText('대응 내용') as HTMLTextAreaElement).value).toBe(''));
  expect((screen.getByLabelText('책임 구분') as HTMLSelectElement).value).toBe('');
  expect((screen.getByRole('button', { name: '반품 확정' }) as HTMLButtonElement).disabled).toBe(true);
});

const fillResolution = () => {
  const set = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
  set('고장 코드', 'FLT-012'); set('증상', '터치 무반응'); set('원인 유형', 'FIRMWARE'); set('원인', '정전기'); set('조치', 'FW 적용'); set('해결 부서', 'TS_AUTO'); set('현장 방문', 'no');
};

test('해결 기록은 종결된 문의에 한 건 등록하고 읽은 판본과 보호 토큰을 보낸다', async () => {
  const api = server('CLOSED', 9, () => jsonResponse(200, flow('CLOSED', 9)), undefined, { resolution_case: null });
  view(); await screen.findByText('종결', { selector: 'strong' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  openMore(); await waitFor(() => expect(menu('해결 기록 남기기').disabled).toBe(false));
  fireEvent.click(menu('해결 기록 남기기'));
  await screen.findByRole('form', { name: '해결 기록' });
  fillResolution();
  fireEvent.click(screen.getByRole('button', { name: '해결 기록 저장' }));
  await screen.findByText('해결 기록을 남겼습니다.');
  expect(api.posts).toHaveLength(1);
  expect(api.posts[0].url).toMatch(/\/resolution-case$/);
  expect(body(api.posts[0])).toMatchObject({ fault_id: 'FLT-012', root_cause_type: 'FIRMWARE', resolved_by_dept: 'TS_AUTO', onsite_visit: false, version: 9 });
  expect(api.posts[0].init.headers).toMatchObject({ 'X-CSRF-Token': 'synthetic-csrf' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('이미 해결 기록이 있거나 종결 전이면 해결 기록 남기기를 열지 않고 이유를 적는다', async () => {
  const closedWithCase = server('CLOSED', 9);
  const first = view(); await screen.findByText('종결', { selector: 'strong' });
  await waitFor(() => expect(closedWithCase.get).toHaveBeenCalled());
  openMore(); expect(menu('해결 기록 남기기').disabled).toBe(true); expect(menu('해결 기록 남기기').textContent).toContain('이미 해결 기록이 있습니다');
  first.unmount(); vi.unstubAllGlobals();
  const notClosed = server('IN_REVIEW', 3, undefined, undefined, { resolution_case: null });
  view(); await screen.findByText('검토 중', { selector: 'strong' });
  await waitFor(() => expect(notClosed.get).toHaveBeenCalled());
  openMore(); expect(menu('해결 기록 남기기').disabled).toBe(true); expect(menu('해결 기록 남기기').textContent).toContain('종결 후 한 건');
});

test('해결 기록이 이미 등록된 경우(409)는 다시 보내지 않고 안내한다', async () => {
  const api = server('CLOSED', 9, () => jsonResponse(409, errorBody('CS_RESOLUTION_CASE_EXISTS', 'exists')), undefined, { resolution_case: null });
  view(); await screen.findByText('종결', { selector: 'strong' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  openMore(); await waitFor(() => expect(menu('해결 기록 남기기').disabled).toBe(false));
  fireEvent.click(menu('해결 기록 남기기')); await screen.findByRole('form', { name: '해결 기록' });
  fillResolution(); fireEvent.click(screen.getByRole('button', { name: '해결 기록 저장' }));
  expect((await screen.findByRole('alert')).textContent).toMatch(/이미 해결 기록이 있습니다/);
  expect(api.posts).toHaveLength(1);
});

const transfer = (extra: Record<string, unknown> = {}) => ({ id: 't1', dept_code: 'TS_AUTO', end_reason: null, ended_at: null, ended_by: null, reason: '터치 무반응 확인', round: 1, status: 'RESULT_SUBMITTED', transferred_at: '2026-10-28T01:30:00Z', transferred_by: 'u1', ...extra });
const answer = (status: string) => ({ id: 'r1', transfer_id: 't1', dept_code: 'TS_AUTO', result: 'FW 2.33 적용 후 재현 없음', reply_content: 'Please update the firmware.', review_status: status, review_note: null, reviewed_at: null, reviewed_by: null, submitted_at: '2026-10-28T05:05:00Z', submitted_by: 'd1', version: 3 });

test('확인 전 부서 답변이 있으면 부서 검토 요청과 종결을 막고 답변 확인 상자를 보인다', async () => {
  const api = server('IN_REVIEW', 5, undefined, undefined, undefined, { transfers: [transfer()], dept_results: [answer('SUBMITTED')] });
  view(); await screen.findByRole('region', { name: '검토 답변 확인' });
  expect((screen.getByRole('button', { name: '부서 검토 요청' }) as HTMLButtonElement).title).toBe('확인 전 답변이 있으면 요청할 수 없습니다');
  expect((screen.getByRole('button', { name: '부서 검토 요청' }) as HTMLButtonElement).disabled).toBe(true);
  openMore(); expect(menu('종결').disabled).toBe(true); expect(menu('종결').textContent).toContain('확인 전 답변이 있으면 불가');
  expect(screen.getByText('확인 전', { selector: 'td' })).toBeTruthy();
  expect(api.posts).toHaveLength(0);
});

test('부서 답변 확인은 결과 판본과 업무 판본을 함께 보낸다', async () => {
  const api = server('IN_REVIEW', 5, () => jsonResponse(200, flow('IN_REVIEW', 6)), undefined, undefined, { transfers: [transfer()], dept_results: [answer('SUBMITTED')] });
  view(); await screen.findByRole('region', { name: '검토 답변 확인' });
  await waitFor(() => expect((screen.getByRole('button', { name: '검토 저장' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '검토 저장' }));
  await screen.findByText('부서 답변을 확인했습니다.');
  expect(api.posts[0].url).toMatch(/\/workflow\/dept-results\/r1\/review$/);
  expect(body(api.posts[0])).toEqual({ decision: 'ACCEPT', note: null, result_version: 3, version: 5 });
  expect(api.posts[0].init.headers).toMatchObject({ 'X-CSRF-Token': 'synthetic-csrf' });
});

test('다시 요청은 메모를 보내고 부서 답변이 바뀌었으면(409) 다시 보내지 않고 안내한다', async () => {
  const api = server('IN_REVIEW', 5, () => jsonResponse(409, errorBody('CS_DEPT_RESULT_VERSION_CONFLICT', 'conflict')), undefined, undefined, { transfers: [transfer()], dept_results: [answer('SUBMITTED')] });
  view(); await screen.findByRole('region', { name: '검토 답변 확인' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  fireEvent.click(screen.getByLabelText('다시 요청'));
  fireEvent.change(screen.getByLabelText('메모'), { target: { value: '접지 점검 추가' } });
  await waitFor(() => expect((screen.getByRole('button', { name: '검토 저장' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '검토 저장' }));
  expect((await screen.findByRole('alert')).textContent).toMatch(/부서 답변이 그 사이 바뀌었습니다/);
  expect(body(api.posts[0])).toEqual({ decision: 'REWORK', note: '접지 점검 추가', result_version: 3, version: 5 });
  expect(api.posts).toHaveLength(1);
});

test('이미 확인한 답변은 확인 상자를 보이지 않고 이력에 확인 완료로 적고 요청·종결을 막지 않는다', async () => {
  const api = server('IN_REVIEW', 5, undefined, undefined, undefined, { transfers: [transfer({ status: 'RESULT_SUBMITTED' })], dept_results: [answer('ACCEPTED')] });
  view(); await screen.findByText('확인 완료', { selector: 'td' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  expect(screen.queryByRole('region', { name: '검토 답변 확인' })).toBeNull();
  await waitFor(() => expect((screen.getByRole('button', { name: '부서 검토 요청' }) as HTMLButtonElement).disabled).toBe(false));
  openMore(); expect(menu('종결').disabled).toBe(false);
});

const manager = ['cs_agent', 'manager'];

test('담당 변경은 관리자가 현재 담당을 뺀 후보에서 골라 읽은 판본과 보호 토큰으로 보낸다', async () => {
  const api = server('IN_REVIEW', 5, () => jsonResponse(200, flow('IN_REVIEW', 5, { owner_user_id: 'u2' })), undefined, undefined, { owner_user_id: 'u1' }, manager);
  view(); await screen.findByText('검토 중', { selector: 'strong' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  openMore(); await waitFor(() => expect(menu('담당 변경').disabled).toBe(false));
  fireEvent.click(menu('담당 변경'));
  const select = await screen.findByLabelText('새 담당') as HTMLSelectElement;
  expect([...select.options].map(option => option.textContent)).toEqual(['선택', '박민수']);
  fireEvent.change(select, { target: { value: 'u2' } });
  fireEvent.click(screen.getByRole('button', { name: '변경' }));
  await screen.findByText('담당자를 바꿨습니다.');
  expect(api.posts[0].init.method).toBe('PUT');
  expect(api.posts[0].url).toMatch(/\/workflow\/owner$/);
  expect(body(api.posts[0])).toEqual({ owner_user_id: 'u2', version: 5 });
  expect(api.posts[0].init.headers).toMatchObject({ 'X-CSRF-Token': 'synthetic-csrf' });
});

test('관리자가 아니거나 종결된 문의는 담당 변경을 열지 않고 이유를 적는다', async () => {
  const agent = server('IN_REVIEW', 5);
  const first = view(); await screen.findByText('검토 중', { selector: 'strong' });
  await waitFor(() => expect(agent.get).toHaveBeenCalled());
  openMore(); expect(menu('담당 변경').disabled).toBe(true); expect(menu('담당 변경').textContent).toContain('C/S 관리자만');
  first.unmount(); vi.unstubAllGlobals();
  const closed = server('CLOSED', 5, undefined, undefined, undefined, {}, manager);
  view(); await screen.findByText('종결', { selector: 'strong' });
  await waitFor(() => expect(closed.get).toHaveBeenCalled());
  openMore(); await waitFor(() => expect(menu('담당 변경').textContent).toContain('종결된 문의'));
  expect(menu('담당 변경').disabled).toBe(true);
});

test('새 담당이 활성 C/S 담당자가 아니면(422) 다시 보내지 않고 안내한다', async () => {
  const api = server('IN_REVIEW', 5, () => jsonResponse(422, errorBody('CS_INVALID_OWNER', 'invalid')), undefined, undefined, { owner_user_id: 'u1' }, manager);
  view(); await screen.findByText('검토 중', { selector: 'strong' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  openMore(); await waitFor(() => expect(menu('담당 변경').disabled).toBe(false));
  fireEvent.click(menu('담당 변경'));
  fireEvent.change(await screen.findByLabelText('새 담당'), { target: { value: 'u2' } });
  fireEvent.click(screen.getByRole('button', { name: '변경' }));
  expect((await screen.findByRole('alert')).textContent).toMatch(/지정할 수 없는 사용자입니다/);
  expect(api.posts).toHaveLength(1);
});

test('담당자 목록을 못 읽으면 변경을 막고 안내한다', async () => {
  const api = server('IN_REVIEW', 5, undefined, undefined, undefined, { owner_user_id: 'u1' }, manager, jsonResponse(403, errorBody('FORBIDDEN', '금지')));
  view(); await screen.findByText('검토 중', { selector: 'strong' });
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  openMore(); await waitFor(() => expect(menu('담당 변경').disabled).toBe(false));
  fireEvent.click(menu('담당 변경'));
  await screen.findByText('담당자 목록을 불러오지 못했습니다');
  expect((screen.getByRole('button', { name: '변경' }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.posts).toHaveLength(0);
});
