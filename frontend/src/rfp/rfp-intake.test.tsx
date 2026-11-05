// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setApiMode } from '../api/client';
import { Link } from 'react-router-dom';
import { errorBody, jsonResponse, renderApp, renderAt } from '../test/render';
import { fileProblem, latestVersions } from './IntakeForms';
import { DEPARTMENT_LABEL, requirementNo, shortDateTime } from './labels';
import RfpIntake from './RfpIntake';

// File rendering has dedicated SHA/MIME tests and real DOCX/PDF browser fixtures.
// These admission/upload fixtures return JSON only and intentionally isolate the binary viewer.
vi.mock('./OriginalDocument', () => ({ OriginalDocument: () => <div>원문 미리보기 검사 별도</div> }));

// 서버 tft.department_kinds와 대조되는 목록이다(tests/tft/test_rfp_schema.py).
const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf-8');
const SERVER_DEPARTMENTS: string[] = JSON.parse(read('../../../docs/openapi/tft-department-codes.json'));
const ID = '33333333-3333-4333-8333-333333333333';
const PATH = `/tft/projects/${ID}/settings`;
const ok = (data: unknown, status = 200) => jsonResponse(status, { data, meta: { request_id: 'x' } });
const doc = (document_id: string, version: number, title: string) => ({
  document_id, document_version_id: `${document_id}-v${version}`, version, title, kind: 'RFP_SPEC', base_document_id: null,
  mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', edit_trace_support: true, uploaded_at: '2026-10-20T09:00:00Z',
});
const base = {
  id: ID, external_id: 'P26-0001', name: '합성 프로젝트', client_name: '가상 고객', purpose: 'INITIAL_PROPOSAL', output_status: 'REVIEW_DRAFT',
  judgement_status: 'CONDITION_CHECK', sales_user_id: 's', responsible_user_id: null, review_departments: ['SCM'], review_version: null, data_origin: 'synthetic',
  requirements_count: 48, unresolved_mandatory_count: 3, pending_proposals_count: 4, open_questions_count: 2, documents_count: 2, draft_version: 5,
  requirement_counts: { by_status: { EVIDENCE_FOUND: 29, CONDITIONAL: 8, SPEC_GAP: 6, INSUFFICIENT_EVIDENCE: 5 }, by_necessity: { MANDATORY: 31 }, needs_rereview: 2, open_questions: 2 },
};
type Call = { url: string; method: string; body: unknown; key: string | null; csrf: string | null; type: string | null };
let calls: Call[];
let project: Record<string, unknown>;
let documents: unknown[];
const jobSnapshot = (job_id: string, status: string) => ({ job_id, status, project_id: ID, owner: 'tft', kind: 'rfp.process', version: 1, last_event_sequence: 0 });
let job: (id: string) => Response;
let post: (call: Call) => Response;

beforeEach(() => {
  calls = []; project = base; documents = [doc('a', 1, '사양서'), doc('a', 2, '사양서'), doc('b', 1, '공문')];
  job = id => ok(jobSnapshot(id, 'RUNNING'));
  post = () => ok({ job_id: 'j1', status: 'QUEUED', status_url: '' }, 202);
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET'; const headers = new Headers(init?.headers);
    const call = { url, method, body: init?.body instanceof FormData ? init.body : JSON.parse((init?.body as string) || 'null'), key: headers.get('Idempotency-Key'), csrf: headers.get('X-CSRF-Token'), type: headers.get('Content-Type') };
    calls.push(call);
    if (method === 'POST' && url.endsWith('/tft/projects') && ((call.body as { review_departments: string[] }).review_departments).some(d => !SERVER_DEPARTMENTS.includes(d))) {
      return jsonResponse(422, errorBody('VALIDATION_ERROR', 'unknown department'));
    }
    if (method === 'POST') return post(call);
    if (url.includes('/platform/me')) return new Response(JSON.stringify({ data: { id: '00000000-0000-4000-8000-000000000003', display_name: '합성', dept: null, roles: ['sales'] }, meta: { request_id: 'x' } }), { status: 200, headers: { 'X-CSRF-Token': 'csrf-1' } });
    // These existing tests exercise polling fallback. SSE validation/recovery is covered separately.
    if (url.includes('/events?')) return jsonResponse(400, errorBody('INVALID_EVENT_CURSOR', 'fixture: polling fallback'));
    if (url.includes('/platform/jobs/')) return job(url.split('/').at(-1)!);
    if (url.endsWith('/documents')) return ok({ items: documents });
    if (url.includes('/requirements?')) return ok({ items: [], next_cursor: null });
    if (url.includes('/partner-candidates')) return ok({ items: [], manufacturer_list_basis: 'CURRENT_PROJECT_INPUT' });
    if (url.includes('/proposals')) return ok({ items: [] });
    if (url.includes('/questions')) return ok({ items: [{ question_id: 'q1', question_text: '값을 확인해 주세요' }] });
    if (/\/tft\/projects(?:\?|$)/.test(url)) return ok({ items: [], next_cursor: null });
    return ok(project);
  }));
  setApiMode('live');
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); setApiMode('mock'); vi.useRealTimers(); });
const showMetadata = async () => { for (const name of ['문서 관리','프로젝트 정보·설정','문서 판본 이력']) { const summary = await screen.findByText(name); if (!summary.closest('details')?.open) fireEvent.click(summary); } };
const open = async () => { renderApp(PATH); await showMetadata(); await screen.findByRole('table', { name: '문서와 판본' }); await screen.findByRole('cell', { name: '공문' }); };
const posts = (part: string) => calls.filter(c => c.method === 'POST' && c.url.includes(part));
const next = () => document.querySelector('.kv-next')?.textContent ?? '';

describe('프로젝트 화면', () => {
  it('프로젝트·요구 요약·문서·할 일을 보드 말로 보여 준다', async () => {
    await open();
    expect(screen.queryByText('확인할 업무')).toBeNull();
    expect(screen.queryByText('문서 판본 및 요구 집계')).toBeNull();
    expect(screen.getAllByText('조건 확인 필요').length).toBeGreaterThan(0);
    expect(screen.getByText('구매팀')).toBeTruthy();
    expect(next()).toContain('검토할 설비·업무를 고르고');
    expect(screen.getByRole('link', {name:'판정 변경 검토 4건'}).getAttribute('href')).toBe(`/tft/projects/${ID}/table/approvals`);
    fireEvent.click(screen.getByRole('button',{name:'전체 질문 보기'}));
    expect(await screen.findByText('값을 확인해 주세요')).toBeTruthy();
    expect(screen.getByRole('link', { name: '검토 대상 보기' }).getAttribute('href')).toBe('#rfp-document-review');
    expect(document.querySelector('.ls-legacy')).toBeNull();
    expect([...document.querySelectorAll('.sidebar a[aria-current="page"]')].map(a => a.textContent)).toEqual(['RFP 검토']);
  });

  it('새 프로젝트는 문서부터 안내하고 처리 실행을 막는다', async () => {
    project = { ...base, documents_count: 0, requirements_count: 0, output_status: 'INTAKE' }; documents = [];
    renderApp(PATH);
    await screen.findByText('문서가 없습니다. 문서 올리기로 시작하세요.');
    expect(next()).toContain('문서를 올린 뒤 처리 실행을 누르세요.');
    expect(screen.getByRole('img', { name: 'AI 대기 중' }).getAttribute('data-state')).toBe('idle');
    expect(screen.getByRole('status').textContent).toContain('문서를 올리면 분석을 시작할 수 있습니다.');
    expect(screen.queryByRole('list', { name: '분석 단계별 관측 상태' })).toBeNull();
    expect((screen.getByRole('button', { name: '처리 실행' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('프로젝트를 못 읽으면 오류를 보인다', async () => {
    project = {};
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, errorBody('TFT_X', '실패'))));
    renderApp(PATH);
    expect((await screen.findAllByRole('alert'))[0].textContent).toContain('실패');
  });

  it('진행이 분석 중이어도 작업이 없으면 잠그지 않는다(진행 칸은 산출물 단계다)', async () => {
    project = { ...base, output_status: 'ANALYZING' };
    await open();
    expect((screen.getByRole('button', { name: '문서 올리기' }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: '처리 실행' }) as HTMLButtonElement).disabled).toBe(false);
    expect(next()).not.toContain('30초마다');
  });

  it('latest_job이 도는 중이면 잠그고 다시 들어와도 이어 본다', async () => {
    project = { ...base, latest_job: { job_id: 'j9', status: null, step: null } };
    await open();
    await waitFor(() => expect(next()).toContain('30초마다'));
    expect(calls.some(c => c.url.endsWith('/platform/jobs/j9'))).toBe(true);
    expect((screen.getByRole('button', { name: '문서 올리기' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '처리 실행' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('이미 끝난 작업은 잠그지도 알리지도 않는다', async () => {
    project = { ...base, latest_job: { job_id: 'j9', status: null, step: null } };
    job = () => ok(jobSnapshot('j9', 'SUCCEEDED'));
    await open();
    await waitFor(() => expect(calls.some(c => c.url.endsWith('/platform/jobs/j9'))).toBe(true));
    await waitFor(() => expect((screen.getByRole('button', { name: '문서 올리기' }) as HTMLButtonElement).disabled).toBe(false));
    expect(next()).not.toContain('분석 완료');
  });

  it('돌던 작업이 끝나면 알린다', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    project = { ...base, latest_job: { job_id: 'j9', status: null, step: null } };
    await open();
    await waitFor(() => expect(next()).toContain('30초마다'));
    job = () => ok(jobSnapshot('j9', 'SUCCEEDED'));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    await waitFor(() => expect(next()).toContain('분석 완료, 요구 48건'));
    vi.useRealTimers();
  });

  it.each([[404], [403]])('남의 작업(%d)은 잠그지 않고 다시 읽지 않는다', async status => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    project = { ...base, latest_job: { job_id: 'j9', status: null, step: null } };
    job = () => jsonResponse(status, errorBody('X', '없음'));
    await open();
    await waitFor(() => expect(calls.some(c => c.url.endsWith('/platform/jobs/j9'))).toBe(true));
    await waitFor(() => expect((screen.getByRole('button', { name: '문서 올리기' }) as HTMLButtonElement).disabled).toBe(false));
    expect(next()).not.toContain('30초마다');
    const reads = () => calls.filter(c => c.url.endsWith('/platform/jobs/j9')).length;
    const before = reads();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(reads()).toBe(before);
    vi.useRealTimers();
  });

  it('작업 조회 장애는 잠그지 않되 계속 다시 본다', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    project = { ...base, latest_job: { job_id: 'j9', status: null, step: null } };
    job = () => jsonResponse(500, errorBody('X', '장애'));
    await open();
    await waitFor(() => expect((screen.getByRole('button', { name: '문서 올리기' }) as HTMLButtonElement).disabled).toBe(false));
    const reads = () => calls.filter(c => c.url.endsWith('/platform/jobs/j9')).length;
    const before = reads();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
    vi.useRealTimers();
  });

  it('업무 UI가 꺼졌으면 프로젝트를 부르지 않는다', async () => {
    vi.stubEnv('DEV', false);
    renderApp(PATH);
    await waitFor(() => expect(document.querySelector('.ls-root')).not.toBeNull());
    expect(calls.some(c => c.url.includes(`/tft/projects/${ID}`))).toBe(false);
  });
});

describe('목록과 고른 프로젝트 한 화면', () => {
  const summary = (id: string, name: string) => ({ ...base, id, name, created_at: '2026-10-20T09:00:00Z' });
  const listing = (detail: (id: string) => Record<string, unknown>) => {
    const inner = vi.mocked(fetch).getMockImplementation() as typeof fetch;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (/\/tft\/projects(?:\?|$)/.test(url)) return ok({ items: [summary(ID, '첫째'), summary('p2', '둘째')], next_cursor: null });
      if (url.endsWith('/tft/projects/p2')) return ok(detail('p2'));
      if (url.endsWith(`/tft/projects/${ID}`)) return ok(detail(ID));
      if (url.includes('/requirements?')) return ok({ items: [{ requirement_id: 'r1', code: 'abc:2.1:1', attribute: 'rating', attribute_key: 'rated_current', status: 'SPEC_GAP', open_questions: 2 }] });
      if (url.includes('/documents/')) return ok({ clauses_count: 212, deleted_values_count: 9 });
      return inner(input, init);
    }));
  };

  it('문서 표에 조항·삭제 값을 보이고 이전 판본은 흐리게 비운다', async () => {
    listing(id => ({ ...base, id }));
    renderApp(PATH);
    await showMetadata();
    const table = await screen.findByRole('table', { name: '문서와 판본' });
    await within(table).findAllByText('212');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows[0].classList.contains('old')).toBe(true);
    expect(rows[0].textContent).not.toContain('212');
    expect(rows[1].textContent).toContain('212');
    expect(rows[1].textContent).toContain('9');
    const documentCalls = vi.mocked(fetch).mock.calls.map(([u]) => String(u)).filter(u => /\/documents\/[^/?]+$/.test(u));
    expect(documentCalls.some(u => u.endsWith('/documents/a'))).toBe(true);
    expect(documentCalls.some(u => u.includes('-v'))).toBe(false);
  });

  it('필수 요구는 검토 목록에서 서버 기준으로 모두 찾는다', async () => {
    listing(id => ({ ...base, id })); renderApp(PATH);
    fireEvent.click(await screen.findByRole('button', { name: '확인 남은 필수 요구' }));
    await screen.findByText('필수·조건부 필수 요구 중 확인이 남은 1건입니다.');
    expect(screen.queryByRole('table',{name:'미해결 필수'})).toBeNull();
    expect(vi.mocked(fetch).mock.calls.some(([u])=>String(u).includes('unresolved_mandatory=true')&&!String(u).includes('limit=5'))).toBe(true);
  });

  it('사용자 ID를 모아 이름을 한 번에 읽고, 못 찾으면 지정으로 둔다', async () => {
    listing(id => ({ ...base, id, sales_user_id: 'u1', responsible_user_id: 'u2' }));
    const inner = vi.mocked(fetch).getMockImplementation() as typeof fetch;
    const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input).includes('/platform/users?') ? ok({ items: [{ user_id: 'u1', display_name: '합성 담당' }] }) : inner(input, init));
    vi.stubGlobal('fetch', f);
    renderApp(PATH);
    await waitFor(() => expect(screen.getAllByText('합성 담당').length).toBeGreaterThan(0));
    expect(screen.getAllByText('지정').length).toBeGreaterThan(0);
    const urls = f.mock.calls.map(([u]) => String(u)).filter(u => u.includes('/platform/users?'));
    expect(urls.every(u => !/ids=.*u1.*u1/.test(u))).toBe(true);
  });

  it('필수 요구를 읽지 못하면 빈 목록으로 안내하지 않는다', async () => {
    listing(id => ({ ...base, id }));
    const inner = vi.mocked(fetch).getMockImplementation() as typeof fetch;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input).includes('unresolved_mandatory') ? jsonResponse(400, errorBody('BAD_REQUEST', '필수 요구 조회 실패')) : inner(input, init)));
    renderApp(PATH);
    fireEvent.click(await screen.findByRole('button',{name:'확인 남은 필수 요구'}));
    await screen.findByText('필수 요구 조회 실패');
    expect(screen.queryByText('필수·조건부 필수 요구 중 확인이 남은 0건입니다.')).toBeNull();
  });

  it('전체 질문은 묶음 연결이 없는 질문도 보이고 원문 다운로드는 중복하지 않는다', async () => {
    await open(); fireEvent.click(screen.getByRole('button',{name:'전체 질문 보기'}));
    expect((await screen.findByRole('table',{name:'작성한 질문'})).textContent).toContain('값을 확인해 주세요');
    expect(screen.queryByRole('link',{name:'원문 내려받기'})).toBeNull();
  });

  it('F8은 목록을 다시 읽는다', async () => {
    await open();
    const lists = () => calls.filter(c => /\/tft\/projects(?:\?|$)/.test(c.url)).length;
    const before = lists();
    fireEvent.keyDown(window, { key: 'F8' });
    await waitFor(() => expect(lists()).toBeGreaterThan(before));
  });

});

describe('문서 올리기', () => {
  it('메모 해석용 문서는 선택에서 제외하고 일반 RFP PDF 업로드는 유지한다', async () => {
    await open();
    fireEvent.click(screen.getByRole('button', { name: '문서 올리기' }));
    expect(screen.queryByRole('option', { name: '메모 PDF' })).toBeNull();
    expect(screen.getByRole('option', { name: 'RFP 사양서' })).toBeTruthy();
    expect((screen.getByLabelText('파일') as HTMLInputElement).accept).toBe('.docx,.pdf');
    expect(fileProblem(new File(['pdf'], 'spec.pdf'))).toBeNull();
  });

  const file = (name: string, size = 10) => { const f = new File(['x'], name); Object.defineProperty(f, 'size', { value: size }); return f; };
  const form = async () => { await open(); fireEvent.click(screen.getByRole('button', { name: '문서 올리기' })); return screen.getByRole('button', { name: '올리기' }) as HTMLButtonElement; };

  it.each([['a.hwp', 10, '허용 형식'], ['a.pdf', 26 * 1024 * 1024, '25 MB'], ['a.hwp', 32 * 1024 * 1024, '허용 형식은 DOCX, PDF입니다. 32.0 MB']])('%s %d 를 막는다', (name, size, text) => {
    expect(fileProblem(file(name, size))).toContain(text);
  });
  it('허용 파일은 통과한다', () => {
    expect(fileProblem(file('a.DOCX'))).toBeNull();
    expect(fileProblem(file('a.pdf', 25 * 1024 * 1024))).toBeNull();
    expect(fileProblem(null)).toBeNull();
  });

  it('잘못된 파일은 올리기를 막고 파일을 바꾸면 열린다', async () => {
    const submit = await form();
    fireEvent.change(screen.getByLabelText('파일'), { target: { files: [file('a.hwp')] } });
    expect(screen.getByRole('alert').textContent).toContain('허용 형식');
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('파일'), { target: { files: [file('사양서.docx')] } });
    expect((screen.getByLabelText('제목') as HTMLInputElement).value).toBe('a');
    expect(submit.disabled).toBe(false);
  });

  it('멀티파트로 보내고 Content-Type은 브라우저에 맡긴다', async () => {
    post = () => ok(doc('c', 1, '새 문서'), 201);
    const submit = await form();
    await waitFor(() => expect(calls.some(c => c.url.includes('/platform/me'))).toBe(true));
    fireEvent.change(screen.getByLabelText('파일'), { target: { files: [file('새 문서.docx')] } });
    fireEvent.change(screen.getByLabelText('새 판본'), { target: { value: 'a' } });
    fireEvent.click(submit);
    await waitFor(() => expect(posts('/documents')).toHaveLength(1));
    const [call] = posts('/documents');
    const body = call.body as FormData;
    expect(body.get('title')).toBe('새 문서');
    expect(body.get('kind')).toBe('RFP_SPEC');
    expect(body.get('supersedes_document_id')).toBe('a');
    expect(body.get('note')).toBeNull();
    expect(call.type).toBeNull();
    expect(call.key).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole('button', { name: '올리기' })).toBeNull());
  });

  it('새 판본 목록은 문서마다 최신 판본이다', async () => {
    await form();
    expect(within(screen.getByLabelText('새 판본')).getByRole('option', { name: '사양서 (v2)의 새 판본' })).toBeTruthy();
    expect(within(screen.getByLabelText('새 판본')).queryByRole('option', { name: '사양서 (v1)의 새 판본' })).toBeNull();
  });

  it.each([[413, 'TFT_DOCUMENT_TOO_LARGE', '25 MB를 넘었습니다'], [503, 'TFT_DOCUMENT_UNAVAILABLE', '입력은 그대로']])('%d 는 입력을 지킨다', async (status, code, text) => {
    post = () => jsonResponse(status, errorBody(code, 'x'));
    const submit = await form();
    fireEvent.change(screen.getByLabelText('파일'), { target: { files: [file('a.docx')] } });
    fireEvent.change(screen.getByLabelText('메모'), { target: { value: '남는 메모' } });
    fireEvent.click(submit);
    expect((await screen.findByRole('alert')).textContent).toContain(text);
    expect((screen.getByLabelText('메모') as HTMLInputElement).value).toBe('남는 메모');
  });
});

describe('처리 실행', () => {
  const form = async () => { await open(); fireEvent.click(screen.getAllByRole('button', { name: '처리 실행' })[0]); return screen.getAllByRole('button', { name: '처리 실행' })[1] as HTMLButtonElement; };

  it('재실행은 사유가 필수이고 문서마다 최신 판본을 보낸다', async () => {
    const submit = await form();
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('재실행 사유'), { target: { value: ' 공문 반영 ' } });
    fireEvent.click(submit);
    await waitFor(() => expect(posts('/process')).toHaveLength(1));
    expect(posts('/process')[0].body).toEqual({ document_version_ids: ['a-v2', 'b-v1'], reason: '공문 반영' });
    expect(posts('/process')[0].key).toBeTruthy();
    await waitFor(() => expect(next()).toContain('분석 중'));
    expect((screen.getByRole('button', { name: '문서 올리기' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('재실행 안내는 없는 변경 제안 생성을 약속하지 않는다', async () => {
    await form();
    const notice = screen.getByText(/손댄 요구와 승인된 판단은 덮어쓰지 않습니다/).textContent ?? '';
    expect(notice).toContain('변경 제안으로 다시 올리지는 않');
    expect(screen.queryByText(/변경 제안으로 들어옵니다/)).toBeNull();
  });

  it('첫 실행은 사유 없이 된다', async () => {
    project = { ...base, requirements_count: 0 };
    const submit = await form();
    expect(screen.queryByLabelText('재실행 사유')).toBeNull();
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await waitFor(() => expect(posts('/process')).toHaveLength(1));
    expect(posts('/process')[0].body).toEqual({ document_version_ids: ['a-v2', 'b-v1'], reason: null });
  });

  it.each([
    [409, 'TFT_JOB_IN_PROGRESS', '프로젝트당 작업 하나'],
    [503, 'TFT_PROCESSOR_NOT_CONNECTED', '처리기가 연결되어 있지 않습니다'],
    [503, 'JOB_ADMISSION_UNAVAILABLE', '작업은 만들어지지 않았습니다'],
  ])('%d %s 는 알리고 입력을 지킨다', async (status, code, text) => {
    post = () => jsonResponse(status, errorBody(code, 'x'));
    const submit = await form();
    const reason = screen.getByLabelText('재실행 사유') as HTMLInputElement;
    fireEvent.change(reason, { target: { value: '사유' } });
    fireEvent.click(submit);
    expect((await screen.findByRole('alert')).textContent).toContain(text);
    expect(reason.value).toBe('사유');
  });

  it.each([
    ['SUCCEEDED', '분석 완료, 요구 48건'],
    ['PARTIAL', '일부만 처리됐습니다'],
    ['FAILED', '처리가 끝나지 못했습니다'],
    ['CANCELLED', '처리가 끝나지 못했습니다'],
  ])('작업이 %s 로 끝나면 알리고 다시 읽는다', async (status, text) => {
    job = () => ok(jobSnapshot('j1', status));
    const submit = await form();
    fireEvent.change(screen.getByLabelText('재실행 사유'), { target: { value: '사유' } });
    // 완료 시 현재 검토 요구가 다시 조회되는지 확인한다. 제안 목록은 이제 별도 승인 화면에서 읽는다.
    const reads = () => calls.filter(c => c.method === 'GET' && c.url.includes('/requirements?')).length;
    const before = reads();
    fireEvent.click(submit);
    await waitFor(() => expect(next()).toContain(text));
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
    expect((screen.getByRole('button', { name: '문서 올리기' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('요구 0건으로 끝나면 원문 확인을 안내한다', async () => {
    project = { ...base, requirements_count: 0 };
    job = () => ok(jobSnapshot('j1', 'SUCCEEDED'));
    const submit = await form();
    fireEvent.click(submit);
    await waitFor(() => expect(next()).toContain('원문을 확인하세요'));
  });

  it('문서마다 가장 높은 판본만 고른다', () => {
    expect(latestVersions([doc('a', 2, 'x'), doc('a', 1, 'x'), doc('b', 1, 'y')] as never).map(d => d.document_version_id)).toEqual(['a-v2', 'b-v1']);
  });
});

describe('프로젝트 접수', () => {
  it('검토 부서는 서버가 받는 코드 전부를 고를 수 있다', () => {
    expect(Object.keys(DEPARTMENT_LABEL).sort()).toEqual(SERVER_DEPARTMENTS);
  });

});

describe('경로 안에서 프로젝트 이동', () => {
  it('이 화면에서 시작한 작업이 다른 프로젝트로 따라오지 않는다', async () => {
    const other = '44444444-4444-4444-8444-444444444444';
    project = { ...base, requirements_count: 0 };
    renderAt(PATH, 'tft/projects/:projectId/settings', <><Link to={`/tft/projects/${other}/settings`}>다른 프로젝트</Link><RfpIntake /></>);
    await showMetadata();
    await screen.findByRole('cell', { name: '공문' });
    fireEvent.click(screen.getByRole('button', { name: '처리 실행' }));
    fireEvent.click(screen.getAllByRole('button', { name: '처리 실행' })[1]);
    await waitFor(() => expect(next()).toContain('30초마다'));
    fireEvent.click(screen.getByRole('link', { name: '다른 프로젝트' }));
    const upload = await screen.findByRole('button', { name: '문서 올리기' }) as HTMLButtonElement;
    await waitFor(() => expect(upload.disabled).toBe(false));
    expect(next()).not.toContain('30초마다');
  });
});

describe('표시 값', () => {
  it('요구 번호에서 원시 값을 지운다', () => {
    expect(requirementNo('738dbba4:1.1.A.1:1')).toBe('1.1.A.1-1');
    expect(requirementNo('R-01')).toBe('R-01');
  });
  it('일시를 표 칸에 맞게 줄인다', () => {
    expect(shortDateTime(new Date(2026, 9, 26, 14, 5).toISOString())).toBe('2026-10-26 14:05');
  });
});
