import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import TicketDetail from './TicketDetail';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import examples from '../mocks/generated/cs.json';

const source = examples.find(x => x.path === '/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222')!;
const base = (source.body as { data: Record<string, unknown> }).data;
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

const flow = (state: string, version: number, extra: Record<string, unknown> = {}) => ({ data: { ticket_id: 'x', state, version, owner_user_id: null, closed_at: null, closed_by: null, customer_sent_at: null, dept_results: [], transfers: [], ...extra } });
type Post = { url: string; init: RequestInit };

// 문의 상세·업무 흐름 조회와 쓰기 호출을 한 곳에서 받는 가짜 서버. post가 Promise를 돌려주면 응답을 늦출 수 있다.
function server(state: string, version: number, post: (call: Post) => Response | Promise<Response> = () => jsonResponse(200, flow('IN_REVIEW', version + 1)), analysis?: unknown) {
  const posts: Post[] = [];
  const get = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u1', roles: ['cs_agent'] } }), { status: 200, headers: { 'X-CSRF-Token': 'synthetic-csrf' } });
    if ((init?.method ?? 'GET') === 'POST') { const call = { url: path, init: init! }; posts.push(call); return post(call); }
    if (path.endsWith('/workflow')) { get(path); return jsonResponse(200, flow(state, version)); }
    if (path.endsWith('/analysis')) return analysis ? jsonResponse(200, analysis) : jsonResponse(404, errorBody('CS_ANALYSIS_MISSING', '분석 없음'));
    return jsonResponse(200, { data: { ...base, workflow_state: state } });
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
