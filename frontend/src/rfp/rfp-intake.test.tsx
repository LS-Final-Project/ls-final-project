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

// 서버 tft.department_kinds와 대조되는 목록이다(tests/tft/test_rfp_schema.py).
const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf-8');
const SERVER_DEPARTMENTS: string[] = JSON.parse(read('../../../docs/openapi/tft-department-codes.json'));
const ID = '33333333-3333-4333-8333-333333333333';
const PATH = `/tft/projects/${ID}`;
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
let job: () => Response;
let post: (call: Call) => Response;

beforeEach(() => {
  calls = []; project = base; documents = [doc('a', 1, '사양서'), doc('a', 2, '사양서'), doc('b', 1, '공문')];
  job = () => ok({ job_id: 'j1', status: 'RUNNING' });
  post = () => ok({ job_id: 'j1', status: 'QUEUED', status_url: '' }, 202);
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET'; const headers = new Headers(init?.headers);
    const call = { url, method, body: init?.body instanceof FormData ? init.body : JSON.parse((init?.body as string) || 'null'), key: headers.get('Idempotency-Key'), csrf: headers.get('X-CSRF-Token'), type: headers.get('Content-Type') };
    calls.push(call);
    if (method === 'POST' && url.endsWith('/tft/projects') && ((call.body as { review_departments: string[] }).review_departments).some(d => !SERVER_DEPARTMENTS.includes(d))) {
      return jsonResponse(422, errorBody('VALIDATION_ERROR', 'unknown department'));
    }
    if (method === 'POST') return post(call);
    if (url.includes('/platform/me')) return new Response(JSON.stringify({ data: { display_name: '합성', dept: null, roles: ['sales'] }, meta: { request_id: 'x' } }), { status: 200, headers: { 'X-CSRF-Token': 'csrf-1' } });
    if (url.includes('/platform/jobs/')) return job();
    if (url.endsWith('/documents')) return ok({ items: documents });
    if (url.includes('/proposals')) return ok({ items: [] });
    if (url.includes('/questions')) return ok({ items: [{ question_id: 'q1', question_text: '값을 확인해 주세요' }] });
    if (url.endsWith('/tft/projects')) return ok({ items: [], next_cursor: null });
    return ok(project);
  }));
  setApiMode('live');
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); setApiMode('mock'); vi.useRealTimers(); });
const open = async () => { renderApp(PATH); await screen.findByRole('table', { name: '문서와 판본' }); await screen.findByRole('cell', { name: '공문' }); };
const posts = (part: string) => calls.filter(c => c.method === 'POST' && c.url.includes(part));
const next = () => document.querySelector('.kv-next')?.textContent ?? '';

describe('프로젝트 화면', () => {
  it('프로젝트·요구 요약·문서·할 일을 보드 말로 보여 준다', async () => {
    await open();
    expect(screen.getByText('근거 있음 29, 조건부 검토 8, 사양 차이 6, 근거 부족 5')).toBeTruthy();
    expect(screen.getAllByText('조건 확인 필요').length).toBeGreaterThan(0);
    expect(screen.getByText('판본 5')).toBeTruthy();
    expect(screen.getByText('구매팀')).toBeTruthy();
    expect(next()).toContain('요구 48건, 미해결 필수 3, 승인 대기 4, 열린 질문 2');
    expect(await screen.findByText('값을 확인해 주세요')).toBeTruthy();
    expect(screen.getByRole('link', { name: '자사 대응표 (TF0102)' }).getAttribute('href')).toBe(`${PATH}/table`);
    expect(document.querySelector('.ls-legacy')).toBeNull();
    expect([...document.querySelectorAll('.sidebar a[aria-current="page"]')].map(a => a.textContent)).toEqual(['접수 및 요구 검토']);
  });

  it('새 프로젝트는 문서부터 안내하고 처리 실행을 막는다', async () => {
    project = { ...base, documents_count: 0, requirements_count: 0, output_status: 'INTAKE' }; documents = [];
    renderApp(PATH);
    await screen.findByText('문서가 없습니다. 문서 올리기로 시작하세요.');
    expect(next()).toContain('문서 0, 요구 0');
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
    job = () => ok({ job_id: 'j9', status: 'SUCCEEDED' });
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
    job = () => ok({ job_id: 'j9', status: 'SUCCEEDED' });
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
      if (url.endsWith('/tft/projects')) return ok({ items: [summary(ID, '첫째'), summary('p2', '둘째')], next_cursor: null });
      if (url.endsWith('/tft/projects/p2')) return ok(detail('p2'));
      if (url.endsWith(`/tft/projects/${ID}`)) return ok(detail(ID));
      if (url.includes('/requirements?')) return ok({ items: [{ requirement_id: 'r1', code: 'abc:2.1:1', attribute: 'rating', attribute_key: 'rated_current', status: 'SPEC_GAP', open_questions: 2 }] });
      if (url.includes('/documents/')) return ok({ clauses_count: 212, deleted_values_count: 9 });
      return inner(input, init);
    }));
  };

  it('경로에 프로젝트가 없으면 첫 프로젝트를 고른다', async () => {
    listing(id => ({ ...base, id, name: '첫째 상세' }));
    renderApp('/tft/projects');
    await screen.findByText('첫째 상세');
    expect(screen.getByRole('table', { name: '접수 및 요구 검토' }).querySelector('tr.sel')?.textContent).toContain('첫째');
  });

  it('행을 누르면 같은 화면에서 그 프로젝트로 바뀐다', async () => {
    listing(id => ({ ...base, id, name: id === 'p2' ? '둘째 상세' : '첫째 상세' }));
    renderApp('/tft/projects');
    await screen.findByText('첫째 상세');
    fireEvent.click(screen.getByText('둘째').closest('tr') as HTMLElement);
    await screen.findByText('둘째 상세');
    expect(screen.getByRole('table', { name: '접수 및 요구 검토' })).toBeTruthy();
  });

  it('문서 표에 조항·삭제 값을 보이고 이전 판본은 흐리게 비운다', async () => {
    listing(id => ({ ...base, id }));
    renderApp(PATH);
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

  it('미해결 필수 표는 서버 필터로 읽고 한글 속성·표시 번호로 잇는다', async () => {
    listing(id => ({ ...base, id }));
    renderApp(PATH);
    const table = await screen.findByRole('table', { name: '미해결 필수' });
    const link = await within(table).findByRole('link', { name: '2.1-1' });
    expect(link.getAttribute('href')).toBe(`${PATH}/requirements/r1`);
    expect(table.textContent).toContain('질문 2');
    expect(table.textContent).toContain('정격 전류');
    expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u).includes('unresolved_mandatory=true') && String(u).includes('limit=5'))).toBe(true);
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

  it('할 일 표를 못 읽으면 없다고 하지 않는다', async () => {
    listing(id => ({ ...base, id }));
    const inner = vi.mocked(fetch).getMockImplementation() as typeof fetch;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input).includes('unresolved_mandatory') ? jsonResponse(400, errorBody('BAD_REQUEST', 'x')) : inner(input, init)));
    renderApp(PATH);
    const table = await screen.findByRole('table', { name: '미해결 필수' });
    await waitFor(() => expect(table.textContent).toContain('읽지 못했습니다'));
    expect(table.textContent).not.toContain('없습니다');
  });

  it('회신 대기 질문과 원문 내려받기가 잇는다', async () => {
    await open();
    expect(screen.getByRole('table', { name: '회신 대기 질문' }).textContent).toContain('값을 확인해 주세요');
    expect(screen.getByRole('link', { name: '원문 내려받기' }).getAttribute('href')).toBe('/api/v1/tft/documents/a/file');
  });

  it('F8은 목록을 다시 읽는다', async () => {
    await open();
    const lists = () => calls.filter(c => c.url.endsWith('/tft/projects')).length;
    const before = lists();
    fireEvent.keyDown(window, { key: 'F8' });
    await waitFor(() => expect(lists()).toBeGreaterThan(before));
  });

  it('엑셀은 고른 프로젝트만, 고르지 않으면 표시한 행 전체를 CSV로 낸다', async () => {
    const created: Blob[] = [];
    URL.createObjectURL = vi.fn((blob: Blob) => { created.push(blob); return 'blob:x'; });
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    listing(id => ({ ...base, id }));
    renderApp('/tft/projects');
    await screen.findByLabelText('둘째 선택');
    fireEvent.click(screen.getByRole('button', { name: '엑셀' }));
    expect(await created[0].text()).toContain('둘째');
    fireEvent.click(screen.getByLabelText('첫째 선택'));
    fireEvent.click(screen.getByRole('button', { name: '엑셀' }));
    expect(await created[1].text()).not.toContain('둘째');
    expect(click).toHaveBeenCalledTimes(2);
    click.mockRestore();
  });
});

describe('문서 올리기', () => {
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
    job = () => ok({ job_id: 'j1', status });
    const submit = await form();
    fireEvent.change(screen.getByLabelText('재실행 사유'), { target: { value: '사유' } });
    // 프로젝트는 실행 접수 때도 다시 읽으니, 작업이 끝나야만 다시 읽는 제안 목록으로 확인한다.
    const reads = () => calls.filter(c => c.method === 'GET' && c.url.includes('/proposals')).length;
    const before = reads();
    fireEvent.click(submit);
    await waitFor(() => expect(next()).toContain(text));
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
    expect((screen.getByRole('button', { name: '문서 올리기' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('요구 0건으로 끝나면 원문 확인을 안내한다', async () => {
    project = { ...base, requirements_count: 0 };
    job = () => ok({ job_id: 'j1', status: 'SUCCEEDED' });
    const submit = await form();
    fireEvent.click(submit);
    await waitFor(() => expect(next()).toContain('원문을 확인하세요'));
  });

  it('문서마다 가장 높은 판본만 고른다', () => {
    expect(latestVersions([doc('a', 2, 'x'), doc('a', 1, 'x'), doc('b', 1, 'y')] as never).map(d => d.document_version_id)).toEqual(['a-v2', 'b-v1']);
  });
});

describe('프로젝트 접수', () => {
  it('필수 칸이 차야 접수되고 만든 프로젝트로 간다', async () => {
    post = () => ok({ id: ID }, 201);
    renderApp('/tft/projects');
    fireEvent.click(await screen.findByRole('button', { name: '접수' }));
    const submit = screen.getAllByRole('button', { name: '접수' })[1] as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('프로젝트명'), { target: { value: ' 합성 ' } });
    fireEvent.change(screen.getByLabelText('고객 코드'), { target: { value: 'HOST' } });
    fireEvent.click(screen.getByLabelText('구매팀'));
    fireEvent.click(submit);
    await waitFor(() => expect(posts('/tft/projects')).toHaveLength(1));
    expect(posts('/tft/projects')[0].body).toEqual({
      name: '합성', company_code: 'HOST', client_name: null, customer_goal: null, review_departments: ['SCM'], data_origin: 'synthetic', purpose: 'INITIAL_PROPOSAL',
    });
    await screen.findByRole('table', { name: '문서와 판본' });
  });

  it('검토 부서는 서버가 받는 코드 전부를 고를 수 있다', () => {
    expect(Object.keys(DEPARTMENT_LABEL).sort()).toEqual(SERVER_DEPARTMENTS);
  });

  it('실패하면 입력을 지킨다', async () => {
    post = () => jsonResponse(403, errorBody('X', '권한 없음'));
    renderApp('/tft/projects');
    fireEvent.click(await screen.findByRole('button', { name: '접수' }));
    fireEvent.change(screen.getByLabelText('프로젝트명'), { target: { value: '합성' } });
    fireEvent.change(screen.getByLabelText('고객 코드'), { target: { value: 'HOST' } });
    fireEvent.click(screen.getAllByRole('button', { name: '접수' })[1]);
    await screen.findByRole('alert');
    expect((screen.getByLabelText('프로젝트명') as HTMLInputElement).value).toBe('합성');
  });
});

describe('경로 안에서 프로젝트 이동', () => {
  it('이 화면에서 시작한 작업이 다른 프로젝트로 따라오지 않는다', async () => {
    const other = '44444444-4444-4444-8444-444444444444';
    project = { ...base, requirements_count: 0 };
    renderAt(PATH, 'tft/projects/:projectId', <><Link to={`/tft/projects/${other}`}>다른 프로젝트</Link><RfpIntake /></>);
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
