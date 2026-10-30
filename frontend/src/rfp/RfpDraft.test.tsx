// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
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
const draft = { project_id: P, version: 7, data_origin: 'synthetic', derivation_kind: 'DERIVED', known_at: '2026-10-31T01:00:00Z', disclaimer: '시연용 검토본이며 실제 제안서가 아니다.', evidence_missing_count: 0,
  sections: [{ section: 'CUSTOMER_GOAL_SCOPE', sentences: [{ sentence_id: SENTENCE, text: '고객의 장비 검토 문장', evidence: [{ type: 'requirement', id: 'requirement-1' }], conditions: ['실제 공급과 납기는 별도 확인'], unconfirmed: true, needs_rereview: true, evidence_missing: false, provenance_kind: 'system_record' }] }, { section: 'OUR_RESPONSE', sentences: [] }] };
const accepted = { job_id: JOB, export_id: EXPORT, status_url: `/api/v1/platform/jobs/${JOB}`, status: 'QUEUED' };
const job = (status = 'SUCCEEDED') => ({ job_id: JOB, project_id: P, owner: 'tft', kind: 'rfp.export', status, version: 1, last_event_sequence: 1 });
const saved = { project_id: P, draft_version: 7, review_version: 3, output_status: 'REVIEW_DRAFT', judgement_status: 'CONDITION_CHECK', saved_by: USER, saved_at: '2026-10-31T01:01:00Z' };
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
const disabled = (name: string) => (screen.getByRole('button', { name }) as HTMLButtonElement).disabled;
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it('kv 조각으로 실제 응답의 고지·근거·조건·검토표시와 이력 제한을 표시한다', async () => {
  serve(); renderApp(`/tft/projects/${P}/draft`);
  await screen.findByText(draft.disclaimer);
  expect(screen.getByText('실제 공급과 납기는 별도 확인')).toBeTruthy();
  expect(screen.getByText('requirement: requirement-1')).toBeTruthy();
  expect(screen.getByText('미확약·미확인 / 재검토 필요')).toBeTruthy();
  expect(screen.getByText('판본·검토본·내보내기 이력 목록은 제공되지 않습니다.')).toBeTruthy();
  expect(screen.getByRole('tab').textContent).toBe('초안 및 검토 이력');
  expect(document.querySelector('.ls-root .ls-legacy')).toBeNull();
  expect(disabled('PDF (미지원)')).toBe(true);
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
  const f = serve((url, init) => {
    if (url.endsWith('/exports') && init?.method === 'POST') return data(accepted, 202);
    if (url.includes('/platform/jobs/')) return data(job());
    if (url.endsWith('/file')) return new Response('DOCX bytes', { headers: { 'Content-Type': DOCX_MIME, 'Content-Disposition': 'attachment; filename="export.docx"' } });
    return data(draft);
  });
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본 판본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await waitFor(() => expect(disabled('DOCX 다운로드')).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 다운로드' }));
  await waitFor(() => expect(objectUrl).toHaveBeenCalledOnce());
  expect(f.mock.calls.some(([url]) => url === `${BASE}/exports/${EXPORT}/file`)).toBe(true);
  expect(f.mock.calls.find(([url, init]) => String(url).endsWith('/exports') && init?.method === 'POST')?.[1]?.body).toBe(JSON.stringify({ review_version: 3, format: 'DOCX' }));
});

it.each(['FAILED', 'CANCELLED', 'PARTIAL'])('%s 작업은 다운로드를 열지 않는다', async status => {
  serve((url, init) => url.endsWith('/exports') && init?.method === 'POST' ? data(accepted, 202) : url.includes('/platform/jobs/') ? data(job(status)) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본 판본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await screen.findByText(/파일을 다운로드할 수 없습니다/);
  expect(disabled('DOCX 다운로드')).toBe(true);
});

it('페이지를 떠나면 진행 중 작업 조회 signal을 취소한다', async () => {
  let signal: AbortSignal | undefined;
  serve((url, init) => {
    if (url.endsWith('/exports') && init?.method === 'POST') return data(accepted, 202);
    if (url.includes('/platform/jobs/')) { signal = init?.signal as AbortSignal; return new Promise(() => {}); }
    return data(draft);
  });
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본 판본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await waitFor(() => expect(signal).toBeDefined());
  fireEvent.click(within(screen.getByRole('main')).getByRole('link', { name: '프로젝트' }));
  await waitFor(() => expect(signal!.aborted).toBe(true));
});

it('작업 조회 세션 만료를 표시하고 쓰기·다운로드를 차단한다', async () => {
  serve((url, init) => url.endsWith('/exports') && init?.method === 'POST' ? data(accepted, 202) : url.includes('/platform/jobs/') ? jsonResponse(401, errorBody('SESSION_EXPIRED', '만료')) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본 판본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await screen.findByText('세션이 없거나 만료됐습니다.');
  expect(disabled('검토본 저장')).toBe(true); expect(disabled('DOCX 다운로드')).toBe(true);
});

it('대기 작업을 polling해서 실제 완료 응답 후에만 다운로드를 연다', async () => {
  let reads = 0;
  serve((url, init) => url.endsWith('/exports') && init?.method === 'POST' ? data(accepted, 202) : url.includes('/platform/jobs/') ? data(job(++reads === 1 ? 'QUEUED' : 'SUCCEEDED')) : data(draft));
  renderApp(`/tft/projects/${P}/draft`);
  await waitFor(() => expect(disabled('검토본 저장')).toBe(false));
  fireEvent.change(screen.getByLabelText('내보낼 검토본 판본'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'DOCX 내보내기' }));
  await screen.findByText('대기 중'); expect(disabled('DOCX 다운로드')).toBe(true);
  await waitFor(() => expect(disabled('DOCX 다운로드')).toBe(false), { timeout: 4000 });
});

it('같은 화면의 프로젝트를 바꿔도 이전 polling을 취소하고 export 선택을 초기화한다', async () => {
  let signal: AbortSignal | undefined;
  const OTHER = '66666666-6666-4666-8666-666666666666';
  serve((url, init) => {
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
  fireEvent.change(screen.getByLabelText('내보낼 검토본 판본'), { target: { value: '3' } });
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
