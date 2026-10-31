// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Link } from 'react-router-dom';
import RfpSupplement from './RfpSupplement';
import { render, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderApp } from '../test/render';

const P = '33333333-3333-4333-8333-333333333333';
const R = '44444444-4444-4444-8444-444444444441';
const R2 = '44444444-4444-4444-8444-444444444442';
const U = '11111111-1111-4111-8111-111111111111';
const response = (data: unknown) => jsonResponse(200, { data, meta: { request_id: 'test' } });
const requirement = (id: string, code: string) => ({ requirement_id: id, code, equipment: '변압기', task: null, attribute: '인증', value_text: 'UL 1562', necessity: 'MANDATORY', status: 'SPEC_GAP', path: null, version: 1, needs_rereview: false, rnd_check: 'PENDING', open_questions: 1 });
const options = (id = R, version = 1) => ({ requirement_id: id, version, data_origin: 'synthetic', derivation_kind: 'DERIVED', known_at: '2026-10-31T00:00:00Z', deficiency_cause: 'CERTIFICATION', customer_permission: 'ALLOWED', permission_clause_id: null, options: [
  { path: 'OTHER_OWN_PRODUCT', rank: 1, allowed: false, blocked_reason: '자사 인증 근거 없음', gate: { requires: 'NONE', status: 'NOT_REQUIRED' }, conditions_to_confirm: [], required_expertise: [], candidate_codes: [] },
  { path: 'EXTERNAL_PRODUCT', rank: 3, allowed: true, blocked_reason: null, gate: { requires: 'OWN_DEVELOPMENT_CHECK', status: 'PENDING' }, conditions_to_confirm: ['보증 주체 확인'], required_expertise: ['변압기'], candidate_codes: ['P-A', 'L-A'] },
  { path: 'NONE', rank: 9, allowed: true, blocked_reason: null, gate: { requires: 'NONE', status: 'NOT_REQUIRED' }, conditions_to_confirm: [], required_expertise: [], candidate_codes: [] },
] });
const candidates = (id = R) => ['P-A', 'L-A'].map((code, i) => ({ candidate_code: code, display_name: i ? '협력사 L' : '협력사 A', profile_source: 'synthetic', profile_version: 2, scope: ['TRANSFORMER'], spec_facts_attributes: ['CERTIFICATION'], certifications: i ? ['IEC 60076'] : ['UL 1562'], integrator_capable: {}, on_customer_manufacturer_list: null, lead_time: null, warranty: null, confirmation_status: 'UNCONFIRMED', overall_grade: 'EXCLUDED', conditions_to_confirm: ['공급 확약 필요'], grades: [{ requirement_id: id, grade: i ? 'EXCLUDED' : 'MANDATORY_CONFIRMED', reasons: [i ? '인증 없음' : 'UL 근거 확인'], exclusion_reasons: i ? ['필수 인증 미충족'] : [], mandatory_check: { passed: i ? [] : ['UL 1562'], failed: i ? ['UL 1562'] : [], unknown: ['납기 미확인'] } }] }));
const saved = (id = R) => ({ requirement_id: id, version: 2, data_origin: 'synthetic', derivation_kind: 'PROVIDED', known_at: '2026-10-31T00:00:00Z', path: 'EXTERNAL_PRODUCT', candidate_codes: ['P-A'], role_split: '자사 설치 / 협력사 공급', conditions: [], note: null, provenance_kind: 'human_input', decided_by: U, decided_at: '2026-10-31T00:00:00Z' });

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response> | undefined;
function live(handler: Handler = () => undefined, owner = U) {
  setApiMode('live');
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const override = await handler(url, init);
    if (override) return override;
    if (url.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: U, tid: U, oid: U, display_name: '담당자', email: 'test@example.invalid', roles: ['sales'], dept: null } }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'session-test' } });
    if (url.endsWith(`/projects/${P}`)) return response({ id: P, name: '실제 응답 프로젝트', responsible_user_id: owner, sales_user_id: null });
    if (url.includes('/requirements?')) return response({ items: [requirement(R, 'R-021'), requirement(R2, 'R-024')], next_cursor: null });
    if (url.endsWith('/supplement-options')) return response(options(url.includes(R2) ? R2 : R));
    if (url.includes('/partner-candidates?')) return response({ items: candidates(url.includes(R2) ? R2 : R) });
    if (init?.method === 'POST') return response(saved(url.includes(R2) ? R2 : R));
    return jsonResponse(404, errorBody('NOT_FOUND', '없음'));
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
async function choose() {
  fireEvent.click(await screen.findByRole('button', { name: 'R-021' }));
  fireEvent.click(await screen.findByRole('radio', { name: '외부 제품' }));
  fireEvent.click(screen.getByRole('checkbox', { name: '후보 P-A' }));
  fireEvent.change(screen.getByRole('textbox', { name: '역할 분담' }), { target: { value: '자사 설치 / 협력사 공급' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'R&D 확인 면제 사유' }), { target: { value: '고객 일정 요청에 따라 자사 개발 확인 전 협력사 경로를 검토합니다.' } });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); clearSessionCsrfToken(); setApiMode('mock'); });

test('보드 순서대로 선택지·후보근거·사람 선택을 Kv조각으로 표시한다', async () => {
  live(); renderApp(`/tft/projects/${P}/supplement`); await choose();
  expect(screen.getByRole('heading', { name: '보완 및 협력사 비교' })).toBeTruthy();
  expect(document.querySelector('.ls-legacy')).toBeNull();
  expect((screen.getByRole('radio', { name: '다른 자사 제품' }) as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole('checkbox', { name: '후보 L-A' }) as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole('checkbox', { name: '후보 P-A' }) as HTMLInputElement).disabled).toBe(false);
  const table = screen.getByRole('table', { name: '협력사 후보 근거' });
  expect(within(table).getByRole('columnheader', { name: '이 요구의 등급' })).toBeTruthy();
  expect(within(table).getByText('필수 기술조건 확인')).toBeTruthy();
  expect(within(table).getByText(/UL 근거 확인/)).toBeTruthy();
  expect(within(table).getByText(/필수 인증 미충족/)).toBeTruthy();
  expect(screen.queryByText(/가격 비교/)).toBeNull();
});

test('담당 sales·책임자가 아니면 조회만 하고 저장하지 않는다', async () => {
  const f = live(() => undefined, '다른 사용자'); renderApp(`/tft/projects/${P}/supplement`);
  fireEvent.click(await screen.findByRole('button', { name: 'R-021' }));
  const radio = await screen.findByRole('radio', { name: '외부 제품' });
  expect((radio as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: '보완 선택 저장' }) as HTMLButtonElement).disabled).toBe(true);
  expect(f.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

test('불명확 응답 재시도는 원래 본문·멱등키, 수정 입력은 새 키·CSRF를 사용한다', async () => {
  let attempts = 0;
  const f = live((url, init) => init?.method === 'POST' ? (++attempts < 3 ? jsonResponse(503, errorBody('UNAVAILABLE', '결과 불명')) : response(saved())) : undefined);
  renderApp(`/tft/projects/${P}/supplement`); await choose();
  const save = screen.getByRole('button', { name: '보완 선택 저장' });
  fireEvent.click(save); await screen.findByText('결과 불명');
  fireEvent.click(save); await waitFor(() => expect(attempts).toBe(2));
  await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByRole('textbox', { name: '선택 메모' }), { target: { value: '조건 변경 재검토' } });
  fireEvent.click(save); await screen.findByText(/보완 선택 저장 완료/);
  const posts = f.mock.calls.filter(([, init]) => init?.method === 'POST').map(([, init]) => init!);
  expect(posts[0].body).toBe(posts[1].body);
  expect((posts[0].headers as Record<string, string>)['Idempotency-Key']).toBe((posts[1].headers as Record<string, string>)['Idempotency-Key']);
  expect((posts[0].headers as Record<string, string>)['Idempotency-Key']).not.toBe((posts[2].headers as Record<string, string>)['Idempotency-Key']);
  expect((posts[2].headers as Record<string, string>)['X-CSRF-Token']).toBe('session-test');
  expect(JSON.parse(posts[2].body as string).version).toBe(1);
});

test('R&D 면제·역할 분담을 입력하기 전 저장을 차단한다', async () => {
  live(); renderApp(`/tft/projects/${P}/supplement`);
  fireEvent.click(await screen.findByRole('button', { name: 'R-021' }));
  fireEvent.click(await screen.findByRole('radio', { name: '외부 제품' }));
  const save = screen.getByRole('button', { name: '보완 선택 저장' });
  expect((save as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByRole('textbox', { name: '역할 분담' }), { target: { value: '역할 확정' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'R&D 확인 면제 사유' }), { target: { value: '짧은 사유' } });
  expect((save as HTMLButtonElement).disabled).toBe(true);
});

test('409는 자동 재저장 없이 최신 조회·사용자 재선택을 요구한다', async () => {
  const f = live((url, init) => init?.method === 'POST' ? jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', '다른 판본')) : undefined);
  renderApp(`/tft/projects/${P}/supplement`); await choose();
  fireEvent.click(screen.getByRole('button', { name: '보완 선택 저장' }));
  await screen.findByText(/선택 내용을 다시 확인해야/);
  expect((screen.getByRole('button', { name: '보완 선택 저장' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '보완 최신 조회' }));
  await waitFor(() => expect((screen.getByRole('radio', { name: '외부 제품' }) as HTMLInputElement).checked).toBe(false));
  expect(f.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
});

test('옵션과 요구 판본이 다르면 저장 폼을 만들지 않는다', async () => {
  live(url => url.endsWith('/supplement-options') ? response(options(R, 2)) : undefined);
  renderApp(`/tft/projects/${P}/supplement`);
  fireEvent.click(await screen.findByRole('button', { name: 'R-021' }));
  await screen.findByText(/요구 판본이 변경되었습니다/);
  expect(screen.queryByRole('button', { name: '보완 선택 저장' })).toBeNull();
});

test('요구 전환 뒤 늦게 온 저장 결과는 새 폼을 덮지 않는다', async () => {
  let resolve!: (value: Response) => void;
  const f = live((url, init) => init?.method === 'POST' ? new Promise<Response>(r => { resolve = r; }) : undefined);
  renderApp(`/tft/projects/${P}/supplement`); await choose();
  fireEvent.click(screen.getByRole('button', { name: '보완 선택 저장' }));
  await waitFor(() => expect(f.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: 'R-024' }));
  await screen.findByRole('heading', { name: 'R-024 보완 선택' });
  resolve(response(saved()));
  await waitFor(() => expect((screen.getByRole('textbox', { name: '역할 분담' }) as HTMLTextAreaElement).value).toBe(''));
  expect(screen.queryByText(/보완 선택 저장 완료/)).toBeNull();
});

test('빈 요구·조회 오류·로그인 없음은 가짜 자료 없이 구분한다', async () => {
  live(url => url.includes('/requirements?') ? response({ items: [], next_cursor: null }) : undefined);
  renderApp(`/tft/projects/${P}/supplement`); await screen.findByText('정리된 요구가 없습니다.');
  expect(screen.queryByRole('table', { name: '협력사 후보 근거' })).toBeNull();
});

test.each(['user', 'project'])('%s 전환 뒤 지연 응답·이전 저장 결과를 새 폼에 쓰지 않는다', async transition => {
  let finish!: (value: Response) => void;
  const P2 = '33333333-3333-4333-8333-333333333334';
  const R3 = '44444444-4444-4444-8444-444444444443';
  let posts = 0;
  live((url, init) => {
    if (init?.method === 'POST') { posts++; return new Promise<Response>(resolve => { finish = resolve; }); }
    if (url.endsWith(`/projects/${P2}`)) return response({ id: P2, name: '두 번째 프로젝트', responsible_user_id: U, sales_user_id: null });
    if (url.includes(`/projects/${P2}/requirements?`)) return response({ items: [requirement(R3, 'R-030')], next_cursor: null });
    if (url.includes(`/projects/${P2}/partner-candidates?`)) return response({ items: candidates(R3) });
    if (url.includes(`/requirements/${R3}/supplement-options`)) return response(options(R3));
    return undefined;
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/tft/projects/${P}/supplement`]}>
    <button onClick={() => client.setQueryData(['me'], { data: { id: '22222222-2222-4222-8222-222222222222', roles: ['manager'] } })}>사용자 변경</button>
    <Link to={`/tft/projects/${P2}/supplement`}>프로젝트 변경</Link>
    <Routes><Route path="/tft/projects/:projectId/supplement" element={<RfpSupplement />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  await choose(); fireEvent.click(screen.getByRole('button', { name: '보완 선택 저장' }));
  await waitFor(() => expect(posts).toBe(1));
  fireEvent.click(screen.getByText(transition === 'user' ? '사용자 변경' : '프로젝트 변경'));
  if (transition === 'user') await screen.findByText(/읽기 전용/);
  fireEvent.click(await screen.findByRole('button', { name: transition === 'user' ? 'R-021' : 'R-030' }));
  finish(response(saved()));
  await screen.findByRole('textbox', { name: '역할 분담' });
  expect((screen.getByRole('textbox', { name: '역할 분담' }) as HTMLTextAreaElement).value).toBe('');
  expect(screen.queryByText(/보완 선택 저장 완료/)).toBeNull();
});

test('늦은 옵션 조회 응답도 다른 요구에 붙지 않는다', async () => {
  let finish!: (value: Response) => void;
  live(url => url.includes(`/requirements/${R}/supplement-options`) ? new Promise<Response>(resolve => { finish = resolve; }) : undefined);
  renderApp(`/tft/projects/${P}/supplement`);
  fireEvent.click(await screen.findByRole('button', { name: 'R-021' }));
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  fireEvent.click(screen.getByRole('button', { name: 'R-024' }));
  await screen.findByRole('radio', { name: '외부 제품' });
  finish(response(options(R, 5)));
  await waitFor(() => expect(screen.getByRole('heading', { name: 'R-024 보완 선택' })).toBeTruthy());
  expect(screen.queryByText(/요구 판본이 변경되었습니다/)).toBeNull();
});

test.each([401, 403, 404, 503])('보완 조회 %i 오류를 숨기거나 저장 가능으로 보이지 않는다', async status => {
  live(url => url.endsWith('/supplement-options') ? jsonResponse(status, errorBody('FAILED', '조회 실패')) : undefined);
  renderApp(`/tft/projects/${P}/supplement`);
  fireEvent.click(await screen.findByRole('button', { name: 'R-021' }));
  await screen.findByRole('alert');
  expect(screen.queryByRole('button', { name: '보완 선택 저장' })).toBeNull();
});

test('빈 선택지와 빈 후보는 자동 선택이나 가짜 등급을 만들지 않는다', async () => {
  live(url => url.endsWith('/supplement-options') ? response({ ...options(), options: [] }) : url.includes('/partner-candidates?') ? response({ items: [] }) : undefined);
  renderApp(`/tft/projects/${P}/supplement`);
  fireEvent.click(await screen.findByRole('button', { name: 'R-021' }));
  await screen.findByText('현재 선택 가능한 보완 경로가 없습니다.');
  expect(screen.getByText('이 요구의 저장된 후보 등급이 없습니다.')).toBeTruthy();
  expect((screen.getByRole('button', { name: '보완 선택 저장' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryAllByRole('radio')).toHaveLength(0);
});

test('세션이 없으면 프로젝트 조회와 선택 요청을 시작하지 않는다', async () => {
  const f = live(url => url.endsWith('/platform/me') ? jsonResponse(401, errorBody('UNAUTHENTICATED', '세션 없음')) : undefined);
  renderApp(`/tft/projects/${P}/supplement`);
  await screen.findByText('로그인이 필요합니다');
  expect(f.mock.calls.some(([url]) => String(url).includes('/projects/'))).toBe(false);
});

test('요구 페이지 전환은 커서를 사용하고 이전 선택 입력을 폐기한다', async () => {
  const f = live(url => url.includes('/requirements?') ? response({ items: [requirement(url.includes('cursor=next') ? R2 : R, url.includes('cursor=next') ? 'R-024' : 'R-021')], next_cursor: url.includes('cursor=next') ? null : 'next' }) : undefined);
  renderApp(`/tft/projects/${P}/supplement`); await choose();
  fireEvent.click(screen.getByRole('button', { name: '다음' }));
  fireEvent.click(await screen.findByRole('button', { name: 'R-024' }));
  await screen.findByRole('textbox', { name: '역할 분담' });
  expect((screen.getByRole('textbox', { name: '역할 분담' }) as HTMLTextAreaElement).value).toBe('');
  expect(f.mock.calls.some(([url]) => String(url).includes('cursor=next'))).toBe(true);
});

test('결과 불명 뒤 다른 요구를 보고 돌아와도 원래 본문 재시도 키를 보존한다', async () => {
  const f = live((url, init) => init?.method === 'POST' ? jsonResponse(503, errorBody('UNKNOWN', '결과 불명 유지')) : undefined);
  renderApp(`/tft/projects/${P}/supplement`); await choose();
  fireEvent.click(screen.getByRole('button', { name: '보완 선택 저장' }));
  await screen.findByText('결과 불명 유지');
  fireEvent.click(screen.getByRole('button', { name: 'R-024' }));
  await screen.findByRole('heading', { name: 'R-024 보완 선택' });
  await choose();
  fireEvent.click(screen.getByRole('button', { name: '보완 선택 저장' }));
  await screen.findByText('결과 불명 유지');
  const posts = f.mock.calls.filter(([, init]) => init?.method === 'POST').map(([, init]) => init!);
  expect(posts).toHaveLength(2);
  expect(posts[0].body).toBe(posts[1].body);
  expect((posts[0].headers as Record<string, string>)['Idempotency-Key']).toBe((posts[1].headers as Record<string, string>)['Idempotency-Key']);
});

test('저장 직전 세션이 바뀌면 재확인 중 이전 폼을 숨기고 다른 사용자 POST를 보내지 않는다', async () => {
  let active = U;
  let reads = 0;
  let confirm!: (value: Response) => void;
  const f = live(url => {
    if (!url.endsWith('/platform/me')) return undefined;
    reads++;
    if (reads === 3) return new Promise<Response>(resolve => { confirm = resolve; });
    return new Response(JSON.stringify({ data: { id: active, roles: ['sales'] } }), { headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'fresh-session-token' } });
  });
  renderApp(`/tft/projects/${P}/supplement`); await choose();
  active = '22222222-2222-4222-8222-222222222222';
  fireEvent.click(screen.getByRole('button', { name: '보완 선택 저장' }));
  await screen.findByText('선택 권한 확인 중');
  expect(screen.queryByRole('textbox', { name: '역할 분담' })).toBeNull();
  expect(screen.queryByRole('table', { name: '협력사 후보 근거' })).toBeNull();
  await waitFor(() => expect(confirm).toBeTypeOf('function'));
  confirm(response({ id: active, roles: ['sales'] }));
  await screen.findByText(/읽기 전용/);
  expect(f.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

test.each([
  ['EVIDENCE_FOUND', 'PREFERRED', '근거 있음', '우선'],
  ['CONDITIONAL', 'CONDITION_BOUND', '조건부 검토', '조건 따름'],
  ['INSUFFICIENT_EVIDENCE', 'UNDETERMINED', '근거 부족', '미정'],
])('판단 %s·성격 %s는 공용 보드 문구로 표시한다', async (status, necessity, statusText, necessityText) => {
  live(url => url.includes('/requirements?') ? response({ items: [{ ...requirement(R, 'R-021'), status, necessity }], next_cursor: null }) : undefined);
  renderApp(`/tft/projects/${P}/supplement`);
  const table = await screen.findByRole('table', { name: '요구 목록' });
  await within(table).findByText(statusText);
  expect(within(table).getByText(necessityText)).toBeTruthy();
  expect(within(table).queryByText(status)).toBeNull();
});

test.each(['TFT_CANDIDATE_EXCLUDED', 'TFT_RND_CHECK_REQUIRED'])('서버 %s 거부는 최신 조건 확인 없이 다시 전송하지 않는다', async code => {
  const f = live((url, init) => init?.method === 'POST' ? jsonResponse(422, errorBody(code, '선택 조건 변경')) : undefined);
  renderApp(`/tft/projects/${P}/supplement`); await choose();
  fireEvent.click(screen.getByRole('button', { name: '보완 선택 저장' }));
  await screen.findByText(/선택 내용을 다시 확인해야/);
  expect((screen.getByRole('button', { name: '보완 선택 저장' }) as HTMLButtonElement).disabled).toBe(true);
  expect(f.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
});

test('저장 뒤 옵션·후보를 재조회하고 재확인한 최신 요구 판본으로 연속 저장한다', async () => {
  let version = 1;
  let posts = 0;
  let finishOptions!: (value: Response) => void;
  let finishCandidates!: (value: Response) => void;
  const f = live((url, init) => {
    if (init?.method === 'POST') {
      posts++; version = 2;
      return response({ ...saved(), version: posts + 1 });
    }
    if (url.includes('/requirements?')) return response({ items: [{ ...requirement(R, 'R-021'), version }], next_cursor: null });
    if (url.endsWith('/supplement-options')) return posts === 1
      ? new Promise<Response>(resolve => { finishOptions = resolve; }) : response(options(R, version));
    if (url.includes('/partner-candidates?')) return posts === 1
      ? new Promise<Response>(resolve => { finishCandidates = resolve; }) : response({ items: candidates() });
    return undefined;
  });
  renderApp(`/tft/projects/${P}/supplement`); await choose();
  fireEvent.click(screen.getByRole('button', { name: '보완 선택 저장' }));
  await screen.findByText(/보완 선택 저장 완료/);
  await waitFor(() => {
    expect(finishOptions).toBeTypeOf('function');
    expect(finishCandidates).toBeTypeOf('function');
  });
  expect((screen.queryByRole('button', { name: '보완 선택 저장' }) as HTMLButtonElement | null)?.disabled ?? true).toBe(true);
  finishOptions(response(options(R, 2)));
  await waitFor(() => expect((screen.getByRole('radio', { name: '외부 제품' }) as HTMLInputElement).disabled).toBe(true));
  finishCandidates(response({ items: candidates() }));
  await waitFor(() => expect((screen.getByRole('radio', { name: '외부 제품' }) as HTMLInputElement).disabled).toBe(false));
  expect((screen.getByRole('radio', { name: '외부 제품' }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole('textbox', { name: '역할 분담' }) as HTMLTextAreaElement).value).toBe('');
  const shownVersion = screen.getAllByText('요구 판본').find(node => node.tagName === 'SPAN');
  expect(shownVersion?.parentElement?.textContent).toContain('2');
  expect(posts).toBe(1);
  await choose();
  fireEvent.click(screen.getByRole('button', { name: '보완 선택 저장' }));
  await waitFor(() => expect(posts).toBe(2));
  const requests = f.mock.calls.filter(([, init]) => init?.method === 'POST').map(([, init]) => init!);
  expect(requests.map(init => JSON.parse(init.body as string).version)).toEqual([1, 2]);
  expect((requests[0].headers as Record<string, string>)['Idempotency-Key']).not.toBe((requests[1].headers as Record<string, string>)['Idempotency-Key']);
});
