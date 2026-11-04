// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useQueryClient } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { useNavigate } from 'react-router-dom';
import RfpDraft from './RfpDraft';
import { errorBody, jsonResponse, renderAt, renderApp } from '../test/render';
import { DOCX_MIME } from '../api/rfp-draft';

const P = '11111111-1111-4111-8111-111111111111';
const USER = '22222222-2222-4222-8222-222222222222';
const SENTENCE = '33333333-3333-4333-8333-333333333333';
const JOB = '44444444-4444-4444-8444-444444444444';
const EXPORT = '55555555-5555-4555-8555-555555555555';
const BASE = `/api/v1/tft/projects/${P}/draft`;
const identity = { id: USER, display_name: '검토자', roles: ['sales'], dept: null };
const draft = { project_id: P, version: 7, data_origin: 'synthetic', derivation_kind: 'DERIVED', known_at: '2026-10-31T01:00:00Z', disclaimer: '검토용 초안입니다. 고객 제출 전 담당자 확인이 필요합니다.', evidence_missing_count: 0,
  sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: [{ sentence_id: SENTENCE, text: '고객의 장비 검토 문장', evidence: [{ type: 'requirement', id: 'requirement-1' }], conditions: ['실제 공급과 납기는 별도 확인'], unconfirmed: true, needs_rereview: true, evidence_missing: false, provenance_kind: 'system_record' }] }, { section: 'OUR_RESPONSE', sentences: [] }] };
const accepted = { job_id: JOB, export_id: EXPORT, status_url: `/api/v1/platform/jobs/${JOB}`, status: 'QUEUED' };
const job = (status = 'SUCCEEDED') => ({ job_id: JOB, project_id: P, owner: 'tft', kind: 'rfp.export', status, version: 1, last_event_sequence: 1 });
const saved = { project_id: P, draft_version: 7, review_version: 3, output_status: 'REVIEW_DRAFT', judgement_status: 'CONDITION_CHECK', saved_by: USER, saved_at: '2026-10-31T01:01:00Z' };
const reviewRow = { ...saved, note: '기술 검토 반영', saved_by_name: null };
const data = (value: unknown, status = 200) => jsonResponse(status, { data: value });
type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;
function serve(handler?: Handler, roles = ['sales'], owner = USER, responsible = false) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/v1/platform/me') return new Response(JSON.stringify({ data: { ...identity, roles } }), { headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'current-session-csrf' } });
    if (url === `/api/v1/tft/projects/${P}`) return data({ id: P, name: '실제 API 프로젝트', sales_user_id: responsible ? null : owner, responsible_user_id: responsible ? owner : null });
    if (handler) return handler(url, init);
    return data(draft);
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
// Export scenarios must select a review returned by the history API.
function serveWithReview(handler: Handler) {
  return serve((url, init) => url === `${BASE}/review-versions` && init?.method !== 'POST'
    ? data({ items: [reviewRow] }) : handler(url, init));
}
// 셸 전체의 접근성 계산이 비동기 조회 완료를 밀지 않도록 현재 업무 화면에서 같은 버튼을 찾는다.
const disabled = (name: string) => (within(screen.getByRole('main')).getByRole('button', { name }) as HTMLButtonElement).disabled;
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it('kv 조각으로 실제 응답의 고지·근거·조건·검토표시를 표시한다', async () => {
  serve(); renderApp(`/tft/projects/${P}/draft`);
  await screen.findByText(draft.disclaimer);
  const row = screen.getByRole('article', { name: '문장 S1' });
  expect(row.querySelector('.draft-evidence')?.textContent).toBe('근거 요구');
  expect(row.querySelector('.draft-status')?.textContent).toBe('자동 정리, 확인할 것: 실제 공급과 납기는 별도 확인재검토');
  expect(row.textContent).not.toContain('미확약');
  expect(screen.getByRole('tab').textContent).toBe('초안 및 검토 이력');
  expect(document.querySelector('.ls-root .ls-legacy')).toBeNull();
  expect(screen.queryByRole('button', { name: /PDF/ })).toBeNull();
  await waitFor(() => expect(disabled('문장 수정')).toBe(false));
});

it.each([['dept_staff'], ['manager']])('프로젝트 담당이 아닌 %s는 조회만 가능하다', async role => {
  serve(undefined, [role], '다른 담당자'); renderApp(`/tft/projects/${P}/draft`);
  await screen.findByText(draft.disclaimer);
  expect(disabled('문장 수정')).toBe(true); expect(disabled('검토본 저장')).toBe(true);
});

it('담당 책임자는 실제 역할과 ID로 편집할 수 있다', async () => {
  serve(undefined, ['dept_staff'], USER, true); renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('문장 수정')).toBe(false));
});

it('프로젝트 응답이 오기 전에는 저장을 막고 담당 확인 뒤에만 연다', async () => {
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  const respond = serve();
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === `/api/v1/tft/projects/${P}`) await pending;
    return respond(input, init);
  });
  vi.stubGlobal('fetch', fetcher);
  renderApp(`/tft/projects/${P}/draft`);
  await screen.findByText(draft.disclaimer);
  expect(disabled('검토본 저장')).toBe(true);
  expect(fetcher.mock.calls.some(([url]) => String(url) === `/api/v1/tft/projects/${P}`)).toBe(true);
  finish();
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
});

it('CAS 409는 사유/입력과 원래 판본을 보존하며 자동 저장 재시도를 하지 않는다', async () => {
  const f = serve((_url, init) => init?.method === 'PATCH' ? jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', '판본 충돌')) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('문장 수정')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '문장 수정' }));
  fireEvent.change(screen.getByLabelText('수정 문장'), { target: { value: '사람이 입력한 문장' } });
  fireEvent.change(screen.getByLabelText('수정 사유'), { target: { value: '근거 확인' } });
  fireEvent.click(screen.getByRole('button', { name: '문장 저장' }));
  await screen.findByText(/입력은 보존했습니다/);
  expect((screen.getByLabelText('수정 문장') as HTMLTextAreaElement).value).toBe('사람이 입력한 문장');
  const calls = f.mock.calls.filter(([, init]) => init?.method === 'PATCH');
  expect(calls.length).toBe(1);
  expect(JSON.parse(calls[0][1]!.body as string)).toEqual({ version: 7, edits: [{ sentence_id: SENTENCE, text: '사람이 입력한 문장', reason: '근거 확인', evidence: draft.sections[0].sentences[0].evidence }] });
  expect(new Headers(calls[0][1]!.headers).get('X-CSRF-Token')).toBe('current-session-csrf');
});

it('불명확 POST는 고정 본문/키로 재확인하고 다음 변경은 새 키를 쓴다', async () => {
  let attempts = 0;
  const f = serve((url, init) => {
    if (url.endsWith('/review-versions') && init?.method === 'POST') {
      if (++attempts === 1) throw new TypeError('network');
      return data(saved, 201);
    }
    return data(draft);
  });
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('검토 메모'), { target: { value: '첫 검토' } });
  fireEvent.click(screen.getByRole('button', { name: '검토본 저장' }));
  await screen.findByRole('button', { name: '같은 요청 다시 확인' });
  expect((screen.getByLabelText('검토 메모') as HTMLTextAreaElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '같은 요청 다시 확인' }));
  await screen.findByText(/검토본 3판을 저장했습니다/);
  fireEvent.change(screen.getByLabelText('검토 메모'), { target: { value: '두번째 검토' } });
  fireEvent.click(screen.getByRole('button', { name: '검토본 저장' }));
  await waitFor(() => expect(attempts).toBe(3));
  const calls = f.mock.calls.filter(([url, init]) => String(url).endsWith('/review-versions') && init?.method === 'POST');
  const keys = calls.map(([, init]) => new Headers(init!.headers).get('Idempotency-Key'));
  expect(keys[0]).toBe(keys[1]); expect(keys[2]).not.toBe(keys[0]);
  expect(calls[0][1]!.body).toBe(calls[1][1]!.body);
  expect(JSON.parse(calls[2][1]!.body as string).note).toBe('두번째 검토');
});

it('완료 작업의 export_id에 연결된 DOCX만 내려받는다', async () => {
  const objectUrl = vi.fn(() => 'blob:test');
  vi.stubGlobal('URL', class extends URL { static createObjectURL = objectUrl; static revokeObjectURL = vi.fn(); });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const f = serveWithReview((url, init) => {
    if (url.endsWith('/exports') && init?.method === 'POST') return data(accepted, 202);
    if (url.includes('/platform/jobs/')) return data(job());
    if (url.endsWith('/file')) return new Response('DOCX bytes', { headers: { 'Content-Type': DOCX_MIME, 'Content-Disposition': 'attachment; filename="export.docx"' } });
    return data(draft);
  });
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await waitFor(() => expect(disabled('DOCX 다운로드')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 다운로드' }));
  await waitFor(() => expect(objectUrl).toHaveBeenCalledOnce());
  expect(f.mock.calls.some(([url]) => url === `${BASE}/exports/${EXPORT}/file`)).toBe(true);
  expect(f.mock.calls.find(([url, init]) => String(url).endsWith('/exports') && init?.method === 'POST')?.[1]?.body).toBe(JSON.stringify({ review_version: 3, format: 'DOCX' }));
});

it.each(['FAILED', 'CANCELLED', 'PARTIAL'])('%s 작업은 다운로드를 열지 않는다', async status => {
  serveWithReview((url, init) => url.endsWith('/exports') && init?.method === 'POST' ? data(accepted, 202) : url.includes('/platform/jobs/') ? data(job(status)) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await screen.findByText(/파일을 다운로드할 수 없습니다/);
  expect(disabled('DOCX 다운로드')).toBe(true);
});

it('페이지를 떠나면 진행 중 작업 조회 signal을 취소한다', async () => {
  let signal: AbortSignal | undefined;
  serveWithReview((url, init) => {
    if (url.endsWith('/exports') && init?.method === 'POST') return data(accepted, 202);
    if (url.includes('/platform/jobs/')) { signal = init?.signal as AbortSignal; return new Promise(() => {}); }
    return data(draft);
  });
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await waitFor(() => expect(signal).toBeDefined());
  fireEvent.click(within(screen.getByRole('main')).getByRole('link', { name: '프로젝트' }));
  await waitFor(() => expect(signal!.aborted).toBe(true));
});

it('작업 조회 세션 만료를 표시하고 쓰기·다운로드를 차단한다', async () => {
  serveWithReview((url, init) => url.endsWith('/exports') && init?.method === 'POST' ? data(accepted, 202) : url.includes('/platform/jobs/') ? jsonResponse(401, errorBody('SESSION_EXPIRED', '만료')) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await screen.findByText('세션이 없거나 만료됐습니다.');
  expect(disabled('검토본 저장')).toBe(true); expect(disabled('DOCX 다운로드')).toBe(true);
});

it('대기 작업을 polling해서 실제 완료 응답 후에만 다운로드를 연다', async () => {
  let reads = 0;
  serveWithReview((url, init) => url.endsWith('/exports') && init?.method === 'POST' ? data(accepted, 202) : url.includes('/platform/jobs/') ? data(job(++reads === 1 ? 'QUEUED' : 'SUCCEEDED')) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await screen.findByText('대기 중'); expect(disabled('DOCX 다운로드')).toBe(true);
  await waitFor(() => expect(disabled('DOCX 다운로드')).toBe(false), { timeout: 4000 });
});

it('같은 화면의 프로젝트를 바꿔도 이전 polling을 취소하고 export 선택을 초기화한다', async () => {
  let signal: AbortSignal | undefined;
  const OTHER = '66666666-6666-4666-8666-666666666666';
  serveWithReview((url, init) => {
    if (url.endsWith('/exports') && init?.method === 'POST') return data(accepted, 202);
    if (url.includes('/platform/jobs/')) { signal = init?.signal as AbortSignal; return new Promise(() => {}); }
    if (url.includes(OTHER)) return jsonResponse(404, errorBody('TFT_PROJECT_NOT_FOUND', '없음'));
    return data(draft);
  });
  function Switch() {
    const navigate = useNavigate();
    return <><button onClick={() => navigate(`/tft/projects/${OTHER}/draft`)}>프로젝트 전환</button><RfpDraft /></>;
  }
  renderAt(`/tft/projects/${P}/draft`, '/tft/projects/:projectId/draft', <Switch />);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await waitFor(() => expect(signal).toBeDefined());
  fireEvent.click(screen.getByRole('button', { name: '프로젝트 전환' }));
  await waitFor(() => expect(signal!.aborted).toBe(true));
  expect(screen.queryByRole('button', { name: 'DOCX 다운로드' })).toBeNull();
});

it('불명확 요청 재확인에서 422가 확정되면 이유를 표시하고 새 본문은 새 키로 보낸다', async () => {
  let attempts = 0;
  const f = serve((url, init) => {
    if (url.endsWith('/review-versions') && init?.method === 'POST') {
      ++attempts;
      if (attempts === 1) throw new TypeError('network');
      if (attempts === 2) return jsonResponse(422, errorBody('TFT_DRAFT_INCOMPLETE', '근거 재검토 필요'));
      return data(saved, 201);
    }
    return data(draft);
  });
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '검토본 저장' }));
  fireEvent.click(await screen.findByRole('button', { name: '같은 요청 다시 확인' }));
  await screen.findByText('근거 재검토 필요');
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('검토 메모'), { target: { value: '후속 확인' } });
  fireEvent.click(screen.getByRole('button', { name: '검토본 저장' }));
  await waitFor(() => expect(attempts).toBe(3));
  const posts = f.mock.calls.filter(([url, init]) => String(url).endsWith('/review-versions') && init?.method === 'POST');
  expect(new Headers(posts[0][1]!.headers).get('Idempotency-Key')).toBe(new Headers(posts[1][1]!.headers).get('Idempotency-Key'));
  expect(new Headers(posts[2][1]!.headers).get('Idempotency-Key')).not.toBe(new Headers(posts[0][1]!.headers).get('Idempotency-Key'));
});

it('문장 필터는 표시만 바꾸며 422 후 메모·선택판본·전체판본 요청을 보존한다', async () => {
  const f = serve((url, init) => {
    if (url.endsWith('/review-versions') && init?.method === 'POST') return jsonResponse(422, errorBody('TFT_DRAFT_INCOMPLETE', '통합 책임 확인 필요'));
    return data(draft);
  });
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('보기'), { target: { value: 'evidence' } });
  expect(screen.queryByText('고객의 장비 검토 문장')).toBeNull();
  fireEvent.change(screen.getByLabelText('검토 메모'), { target: { value: '숨겨진 문장도 전체 검토' } });
  fireEvent.click(screen.getByRole('button', { name: '검토본 저장' }));
  await screen.findByText('통합 책임 확인 필요');
  expect((screen.getByLabelText('검토 메모') as HTMLTextAreaElement).value).toBe('숨겨진 문장도 전체 검토');
  expect((screen.getByLabelText('보기') as HTMLSelectElement).value).toBe('evidence');
  const post = f.mock.calls.find(([url, init]) => String(url).endsWith('/review-versions') && init?.method === 'POST');
  expect(JSON.parse(post![1]!.body as string).version).toBe(7);
  fireEvent.change(screen.getByLabelText('조회할 초안 판본 (비우면 최신)'), { target: { value: '4' } });
  fireEvent.click(screen.getByRole('button', { name: '판본 조회' }));
  await waitFor(() => expect(f.mock.calls.some(([url]) => String(url) === `${BASE}?version=4`)).toBe(true));
  expect((screen.getByLabelText('조회할 초안 판본 (비우면 최신)') as HTMLInputElement).value).toBe('4');
  expect(disabled('검토본 저장')).toBe(true);
  await waitFor(() => expect(disabled('최신 조회')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '최신 조회' }));
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  expect((screen.getByLabelText('검토 메모') as HTMLTextAreaElement).value).toBe('숨겨진 문장도 전체 검토');
  fireEvent.change(screen.getByLabelText('보기'), { target: { value: 'all' } });
  expect(await screen.findByText('고객의 장비 검토 문장')).toBeTruthy();
});

it('A 불명확 요청을 B 세션으로 재확인하면 POST를 막고 이전 입력·캐시를 분리한다', async () => {
  const B = '77777777-7777-4777-8777-777777777777';
  let actor = USER;
  let posts = 0;
  const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/v1/platform/me') return new Response(JSON.stringify({ data: { ...identity, id: actor } }), { headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf' } });
    if (url === `/api/v1/tft/projects/${P}`) return data({ id: P, name: actor, sales_user_id: actor, responsible_user_id: null });
    if (url.endsWith('/review-versions') && init?.method === 'POST') { ++posts; throw new TypeError('network'); }
    return data(actor === USER ? draft : { ...draft, sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: [{ ...draft.sections[0].sentences[0], text: 'B 세션의 초안' }] }] });
  });
  vi.stubGlobal('fetch', f);
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('검토 메모'), { target: { value: 'A만의 메모' } });
  fireEvent.click(screen.getByRole('button', { name: '검토본 저장' }));
  await screen.findByRole('button', { name: '같은 요청 다시 확인' });
  actor = B;
  fireEvent.click(screen.getByRole('button', { name: '같은 요청 다시 확인' }));
  await screen.findByText('B 세션의 초안');
  expect(posts).toBe(1);
  expect((screen.getByLabelText('검토 메모') as HTMLTextAreaElement).value).toBe('');
  expect(screen.queryByRole('button', { name: '같은 요청 다시 확인' })).toBeNull();
  expect(screen.queryByText('고객의 장비 검토 문장')).toBeNull();
  expect(screen.getByText(/이전 입력을 전송하지 않았습니다/)).toBeTruthy();
});

const B_ACTOR = '88888888-8888-4888-8888-888888888888';
function SwitchActor({ change }: { change: () => void }) {
  const cache = useQueryClient();
  return <><button onClick={() => { change(); cache.setQueryData(['me'], { data: { ...identity, id: B_ACTOR } }); }}>계정 전환</button><RfpDraft /></>;
}
function accountServer(write?: Handler) {
  let actor = USER;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/v1/platform/me') return new Response(JSON.stringify({ data: { ...identity, id: actor } }), { headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf' } });
    if (url === `/api/v1/tft/projects/${P}`) return data({ id: P, name: actor, sales_user_id: actor, responsible_user_id: null });
    if (write && (init?.method === 'PATCH' || init?.method === 'POST')) return write(url, init);
    if (url.includes('/platform/jobs/')) return data(job());
    return data(actor === USER ? draft : { ...draft, sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: [{ ...draft.sections[0].sentences[0], text: '새 계정 B 초안' }] }] });
  }));
  return () => { actor = B_ACTOR; };
}

it('실제 identity 변경은 편집/메모/검토본/내보내기 선택을 모두 폐기한다', async () => {
  const change = accountServer(url => data(url.endsWith('/exports') ? accepted : saved, 201));
  renderAt(`/tft/projects/${P}/draft`, '/tft/projects/:projectId/draft', <SwitchActor change={change} />);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '검토본 저장' }));
  await screen.findByText(/검토본 3판을 저장했습니다/);
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await waitFor(() => expect(disabled('DOCX 다운로드')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '문장 수정' }));
  fireEvent.change(screen.getByLabelText('수정 문장'), { target: { value: 'A 전용 수정 문장' } });
  fireEvent.change(screen.getByLabelText('검토 메모'), { target: { value: 'A 전용 메모' } });
  fireEvent.click(screen.getByRole('button', { name: '계정 전환' }));
  await screen.findByText('새 계정 B 초안');
  expect(screen.queryByLabelText('수정 문장')).toBeNull();
  expect((screen.getByLabelText('검토 메모') as HTMLTextAreaElement).value).toBe('');
  expect(screen.queryByText(EXPORT)).toBeNull();
  expect(screen.queryByText('저장한 검토본')).toBeNull();
  expect(screen.queryByRole('button', { name: 'DOCX 다운로드' })).toBeNull();
});

it('계정 전환 뒤 늦은 A 편집 응답은 B 캐시나 폼에 적용되지 않는다(StrictMode)', async () => {
  let resolve: ((response: Response) => void) | undefined;
  let signal: AbortSignal | undefined;
  const change = accountServer((_url, init) => { signal = init?.signal as AbortSignal; return new Promise<Response>(done => { resolve = done; }); });
  renderAt(`/tft/projects/${P}/draft`, '/tft/projects/:projectId/draft', <StrictMode><SwitchActor change={change} /></StrictMode>);
  await waitFor(() => expect(disabled('문장 수정')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '문장 수정' }));
  fireEvent.change(screen.getByLabelText('수정 사유'), { target: { value: 'A 수정 확인' } });
  fireEvent.click(screen.getByRole('button', { name: '문장 저장' }));
  await waitFor(() => expect(resolve).toBeDefined());
  fireEvent.click(screen.getByRole('button', { name: '계정 전환' }));
  await screen.findByText('새 계정 B 초안');
  expect(signal!.aborted).toBe(true);
  await act(async () => { resolve!(data({ ...draft, version: 8, sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: [{ ...draft.sections[0].sentences[0], text: '늦은 A 저장 결과' }] }] })); });
  await waitFor(() => expect(screen.queryByLabelText('수정 문장')).toBeNull());
  expect(screen.queryByText('늦은 A 저장 결과')).toBeNull();
  expect(screen.getByText('새 계정 B 초안')).toBeTruthy();
});


it('문서의 문장 번호와 접힌 전체 근거를 유지하고 편집은 해당 문장에 표시한다', async () => {
  serve(); renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('문장 수정')).toBe(false));
  const row = screen.getByRole('article', { name: '문장 S1' });
  expect(within(row).getByText('S1')).toBeTruthy();
  expect(row.getAttribute('aria-label')).toBe('문장 S1');
  expect(row.getAttribute('aria-label')).not.toContain(SENTENCE);
  expect(row.id).toBe(`draft-sentence-${SENTENCE}`);
  expect(row.textContent).not.toContain(SENTENCE);
  expect(row.querySelector('details')).toBeNull();
  expect(screen.getByRole('complementary', { name: '검토본과 내보내기' })).toBeTruthy();
  fireEvent.click(within(row).getByRole('button', { name: '문장 수정' }));
  expect(within(row).getByLabelText('수정 문장')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('수정 문장'), { target: { value: '필터를 바꿔도 보존하는 편집' } });
  fireEvent.change(screen.getByLabelText('구역'), { target: { value: 'OUR_RESPONSE' } });
  expect((screen.getByLabelText('수정 문장') as HTMLTextAreaElement).value).toBe('필터를 바꿔도 보존하는 편집');
});

it('근거는 번호로 보이고 번호가 없거나 ID 꼴이면 종류 이름만 남긴다', async () => {
  const req = '8b8c4590-e77f-4e3c-95ac-23b94f9227f1';
  const clauseId = 'a568c6a8-5519-4359-94c5-6636b343502a';
  const evidence = [{ type: 'clause', id: clauseId }, { type: 'requirement', id: req }, { type: 'product', id: 'p1' }, { type: 'product', id: 'p2' }, { type: 'question', id: 'q1' }, { type: 'partner', id: 'pa' }];
  const row = { ...draft.sections[0].sentences[0], evidence };
  const evidence_labels = { [req]: '1.5.1.2-1', [clauseId]: `${clauseId}-1`, q1: 'Q1', pa: 'P-C' };
  serve(url => url === BASE ? data({ ...draft, evidence_labels, sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: [row] }] }) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  const article = await screen.findByRole('article', { name: '문장 S1' });
  expect(article.querySelector('.draft-evidence')?.textContent).toBe('근거 요구 1.5.1.2-1, 조항, 제품 2건, 협력사 P-C, 질문 Q1');
  expect(within(article).getByRole('link', { name: '1.5.1.2-1' }).getAttribute('href')).toBe(`/tft/projects/${P}/requirements/${req}`);
  expect(article.textContent).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-/);
});

it('출처는 자동 정리·AI 작성·직접 입력으로 보이고 재검토·근거 없음은 참일 때만 붙는다', async () => {
  const base = draft.sections[0].sentences[0];
  const rows = [
    { ...base, sentence_id: 'a1', provenance_kind: 'system_record', needs_rereview: false, evidence_missing: false, conditions: [] },
    { ...base, sentence_id: 'a2', provenance_kind: 'ai_draft', needs_rereview: true, evidence_missing: false, conditions: [] },
    { ...base, sentence_id: 'a3', provenance_kind: 'human_input', needs_rereview: false, evidence_missing: true, evidence: [], conditions: [] },
  ];
  serve(url => url === BASE ? data({ ...draft, sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: rows }] }) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await screen.findByRole('article', { name: '문장 S3' });
  const status = (no: number) => screen.getByRole('article', { name: `문장 S${no}` }).querySelector('.draft-status')?.textContent;
  expect([status(1), status(2), status(3)]).toEqual(['자동 정리', 'AI 작성재검토', '직접 입력근거 없음']);
  expect(screen.getByRole('article', { name: '문장 S1' }).querySelectorAll('.draft-flag')).toHaveLength(0);
});

it('확인할 조건은 두 개까지 보이고 외 n개로 나머지를 펼친다', async () => {
  const row = { ...draft.sections[0].sentences[0], conditions: ['고객 허용 확인', '납기 확인', '시험 성적서 확인', '보증 주체 확인'] };
  serve(url => url === BASE ? data({ ...draft, sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: [row] }] }) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  const article = await screen.findByRole('article', { name: '문장 S1' });
  expect(article.querySelector('.draft-status')?.textContent).toBe('자동 정리, 확인할 것: 고객 허용 확인 · 납기 확인 외 2개재검토');
  expect(within(article).queryByText('시험 성적서 확인')).toBeNull();
  const more = within(article).getByRole('button', { name: '외 2개' });
  expect(more.getAttribute('aria-expanded')).toBe('false');
  fireEvent.click(more);
  expect(within(article).getByRole('list', { name: '나머지 확인할 것' }).textContent).toBe('시험 성적서 확인보증 주체 확인');
  expect(within(article).getByRole('button', { name: '접기' }).getAttribute('aria-expanded')).toBe('true');
});

it('다음 할 일과 저장 조건은 근거 없는·재검토 문장 번호를 적고 다섯 개 넘으면 줄인다', async () => {
  const base = draft.sections[0].sentences[0];
  const rows = Array.from({ length: 8 }, (_, i) => ({ ...base, sentence_id: `s${i + 1}`, evidence_missing: [2, 4].includes(i + 1), needs_rereview: i + 1 !== 2 && i + 1 !== 8 }));
  serve(url => url === BASE ? data({ ...draft, evidence_missing_count: 2, sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: rows }] }) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await screen.findByRole('article', { name: '문장 S8' });
  const next = document.querySelector('.kv-next')!.textContent!;
  expect(next).toContain('다음: S2·S4 근거 추가, S1·S3·S4·S5·S6 외 1문장 재검토');
  const checks = screen.getByRole('complementary', { name: '검토본과 내보내기' });
  expect(within(checks).getByText('근거 없는 문장').nextElementSibling?.textContent).toBe('S2·S4 확인');
  expect(within(checks).getByText('재검토 필요 문장').nextElementSibling?.textContent).toBe('S1·S3·S4·S5·S6 외 1문장 확인');
});

it('보기 필터 이름은 문장 기준으로 적는다', async () => {
  serve(); renderApp(`/tft/projects/${P}/draft`);
  await screen.findByText(draft.disclaimer);
  expect([...(screen.getByLabelText('보기') as HTMLSelectElement).options].map(option => option.textContent)).toEqual(['전체 문장 1', '재검토 문장 1', '근거 없는 문장 0', '확인할 조건이 남은 문장 1']);
});

const REGENERATE = '최신 검토 결과로 초안 다시 만들기';
it('초안 다시 만들기는 본 판본으로 보내고 새 판을 읽는다', async () => {
  const f = serve((url, init) => url === `${BASE}/regenerate` && init?.method === 'POST'
    ? data({ ...draft, version: 8, sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: [{ ...draft.sections[0].sentences[0], text: '다시 만든 문장' }] }] })
    : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled(REGENERATE)).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: REGENERATE }));
  await screen.findByText('초안 8판을 새로 만들었습니다. 직접 고친 문장은 그대로 두었습니다.');
  expect(screen.getByText('다시 만든 문장')).toBeTruthy();
  const sent = f.mock.calls.filter(([url, init]) => url === `${BASE}/regenerate` && init?.method === 'POST');
  expect(sent).toHaveLength(1);
  expect(JSON.parse(sent[0][1]!.body as string)).toEqual({ version: 7 });
  const headers = new Headers(sent[0][1]!.headers);
  expect(headers.get('X-CSRF-Token')).toBe('current-session-csrf'); expect(headers.get('Idempotency-Key')).toBeTruthy();
});

it.each([
  ['TFT_DRAFT_JOB_ACTIVE', '진행 중인 처리 작업이 끝난 뒤 다시 만드세요.'],
  ['TFT_VERSION_CONFLICT', '다른 변경이 먼저 저장됐습니다. 최신 조회 뒤 다시 만드세요.'],
])('초안 다시 만들기 409 %s는 이유를 알리고 다시 누를 수 있다', async (code, text) => {
  serve((url, init) => url === `${BASE}/regenerate` && init?.method === 'POST' ? jsonResponse(409, errorBody(code, '충돌')) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled(REGENERATE)).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: REGENERATE }));
  await screen.findByText(text);
  await waitFor(() => expect(disabled(REGENERATE)).toBe(false));
});

it.each([[404, 'NOT_FOUND'], [405, 'METHOD_NOT_ALLOWED']])('초안 다시 만들기가 없는 서버(%s)는 아직 지원하지 않음으로 막는다', async (status, code) => {
  serve((url, init) => url === `${BASE}/regenerate` && init?.method === 'POST' ? jsonResponse(status, errorBody(code, 'x')) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled(REGENERATE)).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: REGENERATE }));
  await screen.findByText('이 서버는 아직 초안 다시 만들기를 지원하지 않습니다.');
  expect(disabled(REGENERATE)).toBe(true);
  expect(screen.queryByText('찾을 수 없습니다')).toBeNull();
});

it('검토 이력과 내보내기 표를 보이고 완료 파일만 내려받는다', async () => {
  const objectUrl = vi.fn(() => 'blob:test');
  vi.stubGlobal('URL', class extends URL { static createObjectURL = objectUrl; static revokeObjectURL = vi.fn(); });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const reviews = [{ review_version: 2, draft_version: 7, note: '승인 4건 반영', output_status: 'REVIEW_DRAFT', judgement_status: 'OWNER_CONFIRMED', saved_by: USER, saved_by_name: '이준호', saved_at: '2026-10-28T00:49:00Z' }];
  const exportsList = [
    { export_id: 'e2', review_version: 2, format: 'DOCX', status: 'QUEUED', requested_by: USER, requested_by_name: null, requested_at: '2026-10-28T00:50:00Z', finished_at: null, file_name: null },
    { export_id: EXPORT, review_version: 2, format: 'DOCX', status: 'SUCCEEDED', requested_by: USER, requested_by_name: null, requested_at: '2026-10-28T00:40:00Z', finished_at: '2026-10-28T00:41:00Z', file_name: 'P26-0007_검토본_2판.docx' },
  ];
  const f = serve(url => url === `${BASE}/review-versions` ? data({ items: reviews }) : url === `${BASE}/exports` ? data({ items: exportsList })
    : url.endsWith('/file') ? new Response('DOCX bytes', { headers: { 'Content-Type': DOCX_MIME, 'Content-Disposition': 'attachment' } }) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  const history = await screen.findByRole('table', { name: '검토 이력' });
  const row = (await within(history).findByText('승인 4건 반영')).closest('tr')!;
  expect(within(row).getAllByRole('cell').map(cell => cell.textContent)).toEqual(['2판', expect.stringMatching(/^2026-10-2[78] \d\d:\d\d, 이준호$/), '승인 4건 반영', '검토본 작성', '담당자 확인', 'DOCX']);
  const exportsTable = screen.getByRole('table', { name: '내보내기' });
  const queued = within(exportsTable).getByText('완료 기록 없음').closest('tr')!;
  expect(within(queued).queryByRole('button')).toBeNull();
  expect(exportsTable.textContent).not.toContain(USER);
  expect(screen.getByRole('heading', { name: /내보내기 2건/ })).toBeTruthy();
  fireEvent.click(within(exportsTable).getByRole('button', { name: 'P26-0007_검토본_2판.docx' }));
  await waitFor(() => expect(objectUrl).toHaveBeenCalledOnce());
  expect(f.mock.calls.some(([url]) => url === `${BASE}/exports/${EXPORT}/file`)).toBe(true);
  expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe('P26-0007_검토본_2판.docx');
});

it('이력 목록이 없는 서버(405)는 아직 지원하지 않음으로 보인다', async () => {
  serve(url => url.endsWith('/review-versions') || url.endsWith('/exports') ? jsonResponse(405, errorBody('METHOD_NOT_ALLOWED', 'x')) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(screen.getAllByText('이 서버는 아직 이력 목록을 지원하지 않습니다.')).toHaveLength(2));
});

it('필터에서는 0문장 구역을 빼고 전체 0건은 빈 상태를 한 번만 표시한다', async () => {
  serve(); renderApp(`/tft/projects/${P}/draft`);
  await screen.findByText(draft.disclaimer);
  fireEvent.change(screen.getByLabelText('보기'), { target: { value: 'review' } });
  const list = screen.getByLabelText('초안 문장 목록');
  expect(within(list).getByRole('heading', { name: /고객 목표와 범위/ })).toBeTruthy();
  expect(within(list).queryByRole('heading', { name: /자사 대응/ })).toBeNull();
  fireEvent.change(screen.getByLabelText('보기'), { target: { value: 'evidence' } });
  expect(within(list).queryAllByRole('heading')).toHaveLength(0);
  expect(within(list).getAllByText('선택한 조건의 문장이 없습니다.')).toHaveLength(1);
  expect(disabled('선택 문장 수정')).toBe(true);
});

it('제목줄 문장 수정은 실제 선택한 표시 문장을 열고 전송하지 않는다', async () => {
  const second = { ...draft.sections[0].sentences[0], sentence_id: '66666666-6666-4666-8666-666666666666', text: '두 번째 실제 문장' };
  const f = serve(url => url === BASE ? data({ ...draft, sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: [draft.sections[0].sentences[0], second] }] }) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('선택 문장 수정')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'S2 선택' }));
  fireEvent.click(screen.getByRole('button', { name: '선택 문장 수정' }));
  expect((screen.getByLabelText('수정 문장') as HTMLTextAreaElement).value).toBe(second.text);
  expect(f.mock.calls.some(([, init]) => init?.method === 'PATCH' || init?.method === 'POST')).toBe(false);
});

it('공용 라벨에 없는 출처와 검토 상태는 원문으로 표시한다', async () => {
  serve((url, init) => url.endsWith('/review-versions') && init?.method === 'POST'
    ? data({ ...saved, output_status: 'FUTURE_OUTPUT', judgement_status: 'FUTURE_JUDGEMENT' }, 201)
    : data({ ...draft, data_origin: 'future_source' }));
  renderApp(`/tft/projects/${P}/draft`);
  await screen.findByText('future_source');
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '검토본 저장' }));
  await screen.findByText('FUTURE_OUTPUT / FUTURE_JUDGEMENT');
});

it('전체가 빈 초안은 구역별 빈 문구를 반복하지 않는다', async () => {
  serve(() => data({ ...draft, sections: draft.sections.map(part => ({ ...part, sentences: [] })) }));
  renderApp(`/tft/projects/${P}/draft`);
  await screen.findByText(draft.disclaimer);
  const list = screen.getByLabelText('초안 문장 목록');
  expect(within(list).queryAllByRole('heading')).toHaveLength(0);
  expect(within(list).getAllByText('선택한 조건의 문장이 없습니다.')).toHaveLength(1);
  expect(within(list).queryByText('저장된 문장이 없습니다.')).toBeNull();
});

it('모델 작성은 비용 안내 후 명시 접수하고 완료 뒤 최신 초안을 읽는다', async () => {
  let generated = false;
  const f = serve((url, init) => {
    if (url.endsWith('/draft/rewrite') && init?.method === 'POST') {
      generated = true;
      return data({ job_id: JOB, status: 'QUEUED', status_url: `/api/v1/platform/jobs/${JOB}` }, 202);
    }
    if (url.includes('/platform/jobs/')) return data({ ...job(), kind: 'rfp.draft_rewrite' });
    return data(generated ? { ...draft, version: 8, sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: [{ ...draft.sections[0].sentences[0], text: '모델이 작성한 검토 문장', provenance_kind: 'ai_draft' }] }] } : draft);
  });
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('AI로 문장 다듬기')).toBe(false));
  expect(screen.getByText(/AI 문장 다듬기는 선택 사항이며 비용이 듭니다. 직접 고친 문장은 유지됩니다/)).toBeTruthy();
  expect(f.mock.calls.some(([url]) => String(url).endsWith('/rewrite'))).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'AI로 문장 다듬기' }));
  await screen.findByText('모델이 작성한 검토 문장');
  expect(screen.getByRole('article', { name: '문장 S1' }).querySelector('.draft-status')?.textContent).toMatch(/^AI 작성/);
  const sent = f.mock.calls.filter(([url, init]) => String(url).endsWith('/rewrite') && init?.method === 'POST');
  expect(sent).toHaveLength(1);
  expect(JSON.parse(sent[0][1]!.body as string)).toEqual({ version: 7 });
  expect(new Headers(sent[0][1]!.headers).get('Idempotency-Key')).toBeTruthy();
});

it('불명확 모델 작성 POST는 같은 키·판본으로 재확인한다', async () => {
  let attempts = 0;
  const f = serve((url, init) => {
    if (url.endsWith('/draft/rewrite') && init?.method === 'POST') {
      if (++attempts === 1) throw new TypeError('uncertain');
      return data({ job_id: JOB, status: 'QUEUED', status_url: `/api/v1/platform/jobs/${JOB}` }, 202);
    }
    if (url.includes('/platform/jobs/')) return data({ ...job('RUNNING'), kind: 'rfp.draft_rewrite' });
    return data(draft);
  });
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('AI로 문장 다듬기')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'AI로 문장 다듬기' }));
  await screen.findByText(/전송한 내용과 요청 키를 보존/);
  fireEvent.click(screen.getByRole('button', { name: '같은 요청 다시 확인' }));
  await screen.findByText('AI 문장 다듬기: 작성 중');
  const sent = f.mock.calls.filter(([url, init]) => String(url).endsWith('/rewrite') && init?.method === 'POST');
  expect(sent).toHaveLength(2);
  expect(sent[0][1]!.body).toBe(sent[1][1]!.body);
  expect(new Headers(sent[0][1]!.headers).get('Idempotency-Key')).toBe(new Headers(sent[1][1]!.headers).get('Idempotency-Key'));
  expect(disabled('AI로 문장 다듬기')).toBe(true);
});

it.each(['FAILED', 'CANCELLED'])('모델 작성 %s는 자동 정리 문장을 그대로 남긴다', async status => {
  serve((url, init) => url.endsWith('/draft/rewrite') && init?.method === 'POST'
    ? data({ job_id: JOB, status: 'QUEUED', status_url: `/api/v1/platform/jobs/${JOB}` }, 202)
    : url.includes('/platform/jobs/') ? data({ ...job(status), kind: 'rfp.draft_rewrite' }) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('AI로 문장 다듬기')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'AI로 문장 다듬기' }));
  await screen.findByText(new RegExp(`AI 문장 다듬기: ${status === 'FAILED' ? '실패' : '취소됨'}·자동 정리 문장 유지`));
  expect(screen.getByText(draft.sections[0].sentences[0].text)).toBeTruthy();
  expect(screen.getByRole('article', { name: '문장 S1' }).querySelector('.draft-status')?.textContent).toMatch(/^자동 정리/);
});

it.each([
  [{ outcome: 'SUCCEEDED', model_sentences: 20, template_sentences: 0, human_sentences: 1, failures: {} }, 'status', /AI 작성 문장 20개를 만들었습니다. 사람 문장 1개는 바꾸지 않았습니다/],
  [{ outcome: 'PARTIAL', model_sentences: 21, template_sentences: 27, human_sentences: 0, failures: { INPUT_BLOCKED: 27 } }, 'status', /일부만 다듬었습니다. AI 작성 21개, 자동 정리 유지 27개. 사유: 내부 용어 검사 27/],
  [{ outcome: 'PARTIAL', model_sentences: 5, template_sentences: 3, human_sentences: 0, failures: { SUBJECT_MISSING: 1, NEGATION_MISSING: 1, COMMITMENT: 1, FUTURE_CODE: 1 } }, 'status', /사유: 장비 이름 누락 1, 부정 표현 누락 1, 확약 표현 1, 내용 검사 1/],
  [{ outcome: 'FAILED', model_sentences: 0, template_sentences: 48, human_sentences: 0, failures: { CALL_FAILED: 21, INPUT_BLOCKED: 27 } }, 'alert', /AI로 다듬지 못했습니다. 자동 정리 문장 48개를 그대로 둡니다. 사유: 호출 실패 21, 내부 용어 검사 27/],
  [{ outcome: 'DISCARDED', model_sentences: 0, template_sentences: 0, human_sentences: 0, failures: {} }, 'status', /사람 저장이 먼저 들어와 AI 결과를 버렸습니다/],
] as const)('모델 작성 결과를 구분해 보여 준다 %#', async (result, role, text) => {
  serve((url, init) => url.endsWith('/draft/rewrite') && init?.method === 'POST'
    ? data({ job_id: JOB, status: 'QUEUED', status_url: `/api/v1/platform/jobs/${JOB}` }, 202)
    : url.includes('/platform/jobs/') ? data({ ...job('SUCCEEDED'), kind: 'rfp.draft_rewrite' })
      : url.includes('/draft/rewrites/') ? data({ job_id: JOB, result_version: null, ...result }) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('AI로 문장 다듬기')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'AI로 문장 다듬기' }));
  await waitFor(() => expect(screen.getAllByRole(role).some(node => text.test(node.textContent ?? ''))).toBe(true));
});

it('결과 요약을 읽지 못해도 끝난 사실을 알린다', async () => {
  serve((url, init) => url.endsWith('/draft/rewrite') && init?.method === 'POST'
    ? data({ job_id: JOB, status: 'QUEUED', status_url: `/api/v1/platform/jobs/${JOB}` }, 202)
    : url.includes('/platform/jobs/') ? data({ ...job('SUCCEEDED'), kind: 'rfp.draft_rewrite' })
      : url.includes('/draft/rewrites/') ? data({ error: { code: 'X', message: 'x' } }, 500) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('AI로 문장 다듬기')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'AI로 문장 다듬기' }));
  await screen.findByText(/결과 요약을 읽지 못했습니다/);
});

it('근거는 내부 ID 대신 읽을 수 있는 번호로 보여 준다', async () => {
  const evidence = draft.sections[0].sentences[0].evidence[0];
  serve(() => data({ ...draft, evidence_labels: { [evidence.id]: '2.4.E.1.a-2' } }));
  renderApp(`/tft/projects/${P}/draft`);
  await screen.findByRole('link', { name: '2.4.E.1.a-2' });
  expect(screen.queryByText(new RegExp(evidence.id))).toBeNull();
});

it('모델 작업 조회도 페이지 이동 시 취소한다', async () => {
  let signal: AbortSignal | undefined;
  serve((url, init) => {
    if (url.endsWith('/draft/rewrite') && init?.method === 'POST') return data({ job_id: JOB, status: 'QUEUED', status_url: `/api/v1/platform/jobs/${JOB}` }, 202);
    if (url.includes('/platform/jobs/')) { signal = init?.signal as AbortSignal; return new Promise(() => {}); }
    return data(draft);
  });
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('AI로 문장 다듬기')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'AI로 문장 다듬기' }));
  await waitFor(() => expect(signal).toBeDefined());
  fireEvent.click(within(screen.getByRole('main')).getByRole('link', { name: '프로젝트' }));
  await waitFor(() => expect(signal!.aborted).toBe(true));
});

it('모델 작업 조회의 세션 만료는 모델 작성과 편집·검토 쓰기를 막는다', async () => {
  serve((url, init) => url.endsWith('/draft/rewrite') && init?.method === 'POST'
    ? data({ job_id: JOB, status: 'QUEUED', status_url: `/api/v1/platform/jobs/${JOB}` }, 202)
    : url.includes('/platform/jobs/') ? jsonResponse(401, errorBody('SESSION_EXPIRED', '만료')) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('AI로 문장 다듬기')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'AI로 문장 다듬기' }));
  await screen.findByText('세션이 없거나 만료됐습니다.');
  expect(disabled('AI로 문장 다듬기')).toBe(true);
  expect(disabled('검토본 저장')).toBe(true);
  expect(disabled('선택 문장 수정')).toBe(true);
});

it.each([
  [{ required: true, holder: 'UNRESOLVED' }, true],
  [{ required: true, holder: 'OWN' }, false],
  [{ required: false, holder: 'UNRESOLVED' }, false],
])('통합 책임 %j 는 미확보로 막힐 때만 보완 화면으로 잇는다', async (integration_responsibility, shown) => {
  const inner = serve();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) !== `/api/v1/tft/projects/${P}`) return inner(input, init);
    return data({ id: P, name: '실제 API 프로젝트', sales_user_id: USER, responsible_user_id: null, integration_responsibility });
  }));
  renderApp(`/tft/projects/${P}/draft`);
  await screen.findByRole('heading', { name: '실제 API 프로젝트' });
  const link = screen.queryByRole('link', { name: '보완 화면에서 지정' });
  expect(!!link).toBe(shown);
  if (link) expect(link.getAttribute('href')).toBe(`/tft/projects/${P}/supplement`);
});


it('문서 구역 이동은 저장된 순서와 문장을 유지하며 편집 중 입력을 보존한다', async () => {
  const second = { ...draft.sections[0].sentences[0], sentence_id: '66666666-6666-4666-8666-666666666666', text: '자사 대응의 저장된 문장' };
  const sections = [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: draft.sections[0].sentences }, { section: 'OUR_RESPONSE', sentences: [second] }];
  const f = serve(() => data({ ...draft, sections }));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('선택 문장 수정')).toBe(false));
  const nav = screen.getByRole('navigation', { name: '문서 구역 이동' });
  const list = screen.getByLabelText('초안 문장 목록');
  expect(within(list).getAllByRole('heading').map(row => row.textContent)).toEqual(['고객 목표와 범위1문장', '자사 대응1문장']);
  expect(within(screen.getByRole('main')).getByRole('link', { name: 'RFP 검토' }).getAttribute('href')).toBe(`/tft/projects/${P}`);
  expect(within(list).getAllByRole('button', { name: '문장 수정' })).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '선택 문장 수정' }));
  fireEvent.change(screen.getByLabelText('수정 문장'), { target: { value: '구역 이동 중 작성 문장' } });
  fireEvent.change(screen.getByLabelText('수정 사유'), { target: { value: '담당자 검토' } });
  fireEvent.click(within(nav).getByRole('button', { name: /자사 대응/ }));
  expect(within(list).getByText(second.text)).toBeTruthy();
  expect((screen.getByLabelText('수정 문장') as HTMLTextAreaElement).value).toBe('구역 이동 중 작성 문장');
  fireEvent.click(within(nav).getByRole('button', { name: '전체 문서' }));
  expect((screen.getByLabelText('수정 사유') as HTMLTextAreaElement).value).toBe('담당자 검토');
  expect(f.mock.calls.some(([, init]) => init?.method === 'PATCH' || init?.method === 'POST')).toBe(false);
});


it('내보낼 검토본을 날짜와 메모로 고르고 선택한 판본만 요청한다', async () => {
  const old = { ...reviewRow, review_version: 1, note: '발주처 회신 반영', saved_at: '2026-10-29T01:01:00Z' };
  const f = serve((url, init) => url === `${BASE}/review-versions` ? data({ items: [reviewRow, old] })
    : url === `${BASE}/exports` && init?.method === 'POST' ? data(accepted, 202)
    : url.includes('/platform/jobs/') ? data(job()) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  const picker = await screen.findByRole('combobox', { name: '내보낼 검토본' });
  await waitFor(() => expect((picker as HTMLSelectElement).value).toBe('3'));
  expect(within(picker).getByRole('option', { name: /1판.*2026-10-29.*발주처 회신 반영/ })).toBeTruthy();
  fireEvent.change(picker, { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await waitFor(() => expect(f.mock.calls.some(([url, init]) => url === `${BASE}/exports` && init?.method === 'POST')).toBe(true));
  const write = f.mock.calls.find(([url, init]) => url === `${BASE}/exports` && init?.method === 'POST');
  expect(JSON.parse(write![1]!.body as string).review_version).toBe(1);
});

it('검토 이력 조회 실패 때 임의 판본은 내보낼 수 없고 방금 저장한 검토본은 유지한다', async () => {
  const f = serve((url, init) => url === `${BASE}/review-versions`
    ? init?.method === 'POST' ? data(saved, 201) : jsonResponse(503, errorBody('DEPENDENCY_UNAVAILABLE', '조회 실패'))
    : url === `${BASE}/exports` && init?.method === 'POST' ? data(accepted, 202)
    : url.includes('/platform/jobs/') ? data(job()) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  expect(disabled('DOCX 내보내기')).toBe(true);
  fireEvent.change(screen.getByLabelText('검토 메모'), { target: { value: '이번 회신 확인' } });
  fireEvent.click(screen.getByRole('button', { name: '검토본 저장' }));
  await screen.findByText(/검토본 3판을 저장했습니다/);
  expect((screen.getByLabelText('내보낼 검토본') as HTMLSelectElement).value).toBe('3');
  expect(screen.getByRole('option', { name: /3판.*이번 회신 확인/ })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await waitFor(() => expect(f.mock.calls.some(([url, init]) => url === `${BASE}/exports` && init?.method === 'POST')).toBe(true));
});

it('초안 작성 안내와 재검토 문장 바로보기를 제공하며 자동 모델 호출은 하지 않는다', async () => {
  const f = serve();
  renderApp(`/tft/projects/${P}/draft`);
  await screen.findByText(draft.disclaimer);
  expect(screen.getByRole('list', { name: '초안 작성 순서' }).textContent).toContain('모델 호출은 없습니다');
  fireEvent.click(screen.getByRole('button', { name: 'S1 확인' }));
  expect((screen.getByLabelText('보기') as HTMLSelectElement).value).toBe('review');
  expect(f.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});
