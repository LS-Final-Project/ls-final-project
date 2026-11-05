// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { DevelopmentReviewFlow } from './DevelopmentReviewFlow';
import { addRfpReviewDepartment, createQuestion, listQuestions, type RequirementDetail, type Question } from '../api/rfp';
import { ApiError, request, setApiMode, clearSessionCsrfToken } from '../api/client';
import { me } from '../api/platform';
import { MOCK_DETAIL } from '../mocks/rfp-table';
import { requirementNo } from './labels';
vi.mock('../api/rfp', async original => ({ ...await original<object>(), listQuestions: vi.fn(), createQuestion: vi.fn(), addRfpReviewDepartment: vi.fn() }));
vi.mock('../api/platform', async original => ({ ...await original<object>(), me: vi.fn() }));
const question = { requirement_id: MOCK_DETAIL.requirement_id, target: 'INTERNAL', issue_type: 'RND_FEASIBILITY', question_id: 'question', question_text: '개발 일정 확인', target_dept: 'RND_FW', created_at: '2026-11-03T01:00:00Z', status: 'OPEN', rnd_feasibility: null } as Question;
beforeEach(() => { vi.mocked(listQuestions).mockResolvedValue({ data: { items: [] } }); vi.mocked(me).mockResolvedValue({ data: { id: 'actor' } } as never); });
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); clearSessionCsrfToken(); setApiMode('mock'); });
function show(status: RequirementDetail['judgement']['rnd_check'] = 'NOT_REQUIRED', partnerCount = 0, canRequest = true, reviewDepartments = ['RND_FW']) {
  const requirement = { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: status } } as RequirementDetail;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><DevelopmentReviewFlow projectId="project" actorId="actor" requirement={requirement} requirementId={requirement.requirement_id} requirementVersion={1} canRequest={canRequest} reviewDepartments={reviewDepartments} partnerCount={partnerCount} onSaved={vi.fn()} /></MemoryRouter></QueryClientProvider>);
  return client;
}
test('absence allows composing a real internal request without automatic POST', async () => {
  vi.mocked(createQuestion).mockResolvedValue({ data: question });
  show();
  await screen.findByText('이 요구의 개발 검토 요청 기록이 없습니다.');
  expect(createQuestion).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }));
  fireEvent.change(screen.getByLabelText('질문'), { target: { value: '이 요구 개발 일정 확인 부탁드립니다.' } });
  fireEvent.click(screen.getByRole('button', { name: '질문 저장' }));
  await waitFor(() => expect(createQuestion).toHaveBeenCalledOnce());
  expect(createQuestion).toHaveBeenCalledWith('project', expect.objectContaining({ requirement_id: MOCK_DETAIL.requirement_id, target: 'INTERNAL', target_dept: 'RND_FW', issue_type: 'RND_FEASIBILITY' }), expect.any(String), { useMockFallback: false });
});
test('pending and undetermined replies stay waiting and duplicate requests are blocked', async () => {
  vi.mocked(listQuestions).mockResolvedValue({ data: { items: [{ ...question, rnd_feasibility: 'UNDETERMINED' }] } });
  show('PENDING');
  await screen.findByText('판단 불가 회신으로 개발 확인이 대기 중입니다. 추가로 확인할 조건을 검토하세요.');
  expect(screen.getByText('회신 대기')).toBeTruthy();
  expect(screen.queryByLabelText('개발 검토 단계')).toBeNull();
  expect((screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }) as HTMLButtonElement).disabled).toBe(true);
});
test('feasible uses recorded server state without promising implementation', async () => {
  show('CONFIRMED_FEASIBLE');
  await screen.findByText('이 요구의 개발 검토 요청 기록이 없습니다.');
  expect(screen.getByText('현재 요구의 개발 확인:', { exact: false })).toBeTruthy();
  expect(screen.getByText(/실제 대응 경로는 담당자가/)).toBeTruthy();
});
test.each(['CONFIRMED_NOT_FEASIBLE', 'CONFIRMED_SCHEDULE_MISMATCH'] as const)('%s moves to review alternatives and shows absent evidence', async status => {
  show(status);
  await screen.findByText('이 요구의 개발 검토 요청 기록이 없습니다.');
  expect(screen.getByText(/협력사 근거와 남은 조건을 확인하세요/)).toBeTruthy();
  expect(screen.getByText('현 자료에 대응 근거 없음 · 추가 검토가 필요합니다.')).toBeTruthy();
});
test('query error blocks new requests and does not invent absence', async () => {
  vi.mocked(listQuestions).mockRejectedValue(new Error('조회 실패'));
  show();
  await screen.findByText('조회 실패');
  expect((screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByText('이 요구의 개발 검토 요청 기록이 없습니다.')).toBeNull();
  expect(createQuestion).not.toHaveBeenCalled();
});

test('historical undetermined question cannot override confirmed development state', async () => {
  vi.mocked(listQuestions).mockResolvedValue({ data: { items: [{ ...question, status: 'REPLIED', rnd_feasibility: 'UNDETERMINED' }] } });
  show('CONFIRMED_FEASIBLE');
  await screen.findByText(/요구의 개발 상태와 최근 질문의 판단 불가 회신이 다릅니다/);
  expect(screen.getByText('현재 요구의 개발 확인:', { exact: false })).toBeTruthy();
  expect(screen.queryByLabelText('개발 검토 단계')).toBeNull();
});
test('changed session blocks creating a request from the previously opened form', async () => {
  vi.mocked(me).mockResolvedValue({ data: { id: 'other-actor' } } as never);
  show();
  await screen.findByText('이 요구의 개발 검토 요청 기록이 없습니다.');
  fireEvent.click(screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }));
  fireEvent.change(screen.getByLabelText('질문'), { target: { value: '개발 검토 부탁드립니다.' } });
  fireEvent.click(screen.getByRole('button', { name: '질문 저장' }));
  await screen.findByText(/세션 사용자가 변경되었습니다/);
  expect(createQuestion).not.toHaveBeenCalled();
});

test.each([false, true])('read only or absent review department disables request (%s)', async canRequest => {
  show('NOT_REQUIRED', 0, canRequest, canRequest ? [] : ['RND_FW']);
  await screen.findByText('이 요구의 개발 검토 요청 기록이 없습니다.');
  expect((screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }) as HTMLButtonElement).disabled).toBe(true);
  expect(createQuestion).not.toHaveBeenCalled();
});

function showDepartmentSetup(canEdit = true, version: number | undefined = 4, departments: string[] | undefined = ['TECH']) {
  const refreshed = vi.fn();
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><DevelopmentReviewFlow projectId="project" actorId="actor" requirementId={MOCK_DETAIL.requirement_id} requirementVersion={1} canRequest={false} reviewDepartments={departments} projectVersion={version} canEditProject={canEdit} onProjectSaved={refreshed} onSaved={vi.fn()} /></MemoryRouter></QueryClientProvider>);
  return refreshed;
}
test('department access change requires a separate explicit confirmation and preserves existing departments', async () => {
  vi.mocked(addRfpReviewDepartment).mockResolvedValue({ data: { id: 'project', version: 5, review_departments: ['TECH', 'RND_FW'] } } as never);
  const refreshed = showDepartmentSetup();
  expect(addRfpReviewDepartment).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'R&D 검토 부서 추가' }));
  expect(screen.getByText(/이 부서 소속 담당자에게 이 프로젝트와 RFP 기록의 조회 권한/)).toBeTruthy();
  expect((screen.getByRole('button', { name: '확인하고 검토 부서 추가' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: '프로젝트 조회 권한 부여를 확인했습니다.' }));
  fireEvent.click(screen.getByRole('button', { name: '확인하고 검토 부서 추가' }));
  await waitFor(() => expect(refreshed).toHaveBeenCalledOnce());
  expect(addRfpReviewDepartment).toHaveBeenCalledWith('project', 4, ['TECH'], 'actor');
});
test('department conflict never retries automatically and requires project refresh', async () => {
  vi.mocked(addRfpReviewDepartment).mockRejectedValue(new ApiError(409, '프로젝트 판본 충돌', undefined, 'TFT_VERSION_CONFLICT'));
  const refreshed = showDepartmentSetup();
  fireEvent.click(screen.getByRole('button', { name: 'R&D 검토 부서 추가' }));
  fireEvent.click(screen.getByRole('checkbox', { name: '프로젝트 조회 권한 부여를 확인했습니다.' }));
  fireEvent.click(screen.getByRole('button', { name: '확인하고 검토 부서 추가' }));
  await screen.findByText('자동으로 다시 저장하지 않습니다. 최신 프로젝트를 조회한 뒤 다시 확인하세요.');
  expect(addRfpReviewDepartment).toHaveBeenCalledOnce();
  expect((screen.getByRole('button', { name: '확인하고 검토 부서 추가' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '프로젝트 최신 조회' }));
  expect(refreshed).toHaveBeenCalledOnce();
  expect(addRfpReviewDepartment).toHaveBeenCalledOnce();
});
test('read only department setup cannot open the access confirmation', () => {
  showDepartmentSetup(false);
  expect((screen.getByRole('button', { name: 'R&D 검토 부서 추가' }) as HTMLButtonElement).disabled).toBe(true);
  expect(addRfpReviewDepartment).not.toHaveBeenCalled();
});

test('real department API preserves departments and sends CSRF and project version', async () => {
  setApiMode('live');
  const fetcher = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => new Response(JSON.stringify({ data: init?.method === 'PATCH' ? { id: 'project', version: 5, review_departments: ['TECH', 'RND_FW'] } : {} }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf-test' } }));
  vi.stubGlobal('fetch', fetcher);
  await request('/api/v1/platform/me');
  const api = await vi.importActual<typeof import('../api/rfp')>('../api/rfp');
  await api.addRfpReviewDepartment('project', 4, ['TECH', 'TECH'], 'actor');
  const patch = fetcher.mock.calls.find(([, init]) => init?.method === 'PATCH');
  expect(patch).toBeTruthy();
  expect(JSON.parse(String(patch![1]?.body))).toEqual({ version: 4, review_departments: ['TECH', 'RND_FW'] });
  expect(new Headers(patch![1]?.headers).get('X-CSRF-Token')).toBe('csrf-test');
});
test('real department API blocks changed session before any PATCH', async () => {
  vi.mocked(me).mockResolvedValue({ data: { id: 'other-actor' } } as never);
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  const api = await vi.importActual<typeof import('../api/rfp')>('../api/rfp');
  await expect(api.addRfpReviewDepartment('project', 4, ['TECH'], 'actor')).rejects.toMatchObject({ code: 'SESSION_CHANGED' });
  expect(fetcher).not.toHaveBeenCalled();
});


test('an unrequested check starts with one action rather than an empty form or generic warnings', async () => {
  show();
  await waitFor(() => expect((screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.getByText('자사 개발 가능 여부와 가능한 일정을 개발팀에 확인하세요.')).toBeTruthy();
  expect(screen.queryByLabelText('질문')).toBeNull();
  expect(screen.getByText('협력사 검토 후보가 없습니다. 자사 개발 여부를 단정하지 않습니다.').closest('details')?.open).toBe(false);
  expect(screen.getByText('이 요구의 개발 검토 요청 기록이 없습니다.').closest('details')?.open).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }));
  expect((screen.getByLabelText('질문') as HTMLTextAreaElement).value).toBe(`요구 ${requirementNo(MOCK_DETAIL.code)}에 대해 자사 개발 가능 여부와 가능한 일정, 필요한 조건을 확인해 주세요.`);
  expect(createQuestion).not.toHaveBeenCalled();
});

test('recorded replies lead with the actual question and result without inventing a reply body', async () => {
  vi.mocked(listQuestions).mockResolvedValue({ data: { items: [{ ...question, status: 'REPLIED', rnd_feasibility: 'FEASIBLE', reply_received_at: '2026-11-03T02:00:00Z' }] } });
  show('CONFIRMED_FEASIBLE');
  await screen.findByText('회신 기록됨');
  expect(screen.getByText(question.question_text)).toBeTruthy();
  expect(screen.getByText('회신 개발 가능성:', { exact: false })).toBeTruthy();
  expect(screen.getByText(/이 기록만으로 대응 경로나 제안이 확정되지 않습니다/)).toBeTruthy();
  expect(screen.getByRole('link', { name: '요구 상세·회신 기록' }).getAttribute('href')).toBe(`/tft/projects/project/requirements/${MOCK_DETAIL.requirement_id}`);
  expect(screen.queryByLabelText('질문')).toBeNull();
});

test('background question refresh preserves the draft and blocks saving until current records return', async () => {
  const client = show();
  await waitFor(() => expect((screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '개발팀에 확인할 질문 작성' }));
  fireEvent.change(screen.getByLabelText('질문'), { target: { value: '작성 중인 실제 개발 질문' } });
  let finish!: (value: Awaited<ReturnType<typeof listQuestions>>) => void;
  vi.mocked(listQuestions).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  void client.invalidateQueries({ queryKey: ['rfp-development-questions'] });
  await waitFor(() => expect((screen.getByRole('button', { name: '질문 저장' }) as HTMLButtonElement).disabled).toBe(true));
  expect((screen.getByLabelText('질문') as HTMLTextAreaElement).value).toBe('작성 중인 실제 개발 질문');
  await act(async () => { finish({ data: { items: [] } }); });
  await waitFor(() => expect((screen.getByRole('button', { name: '질문 저장' }) as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByLabelText('질문') as HTMLTextAreaElement).value).toBe('작성 중인 실제 개발 질문');
  expect(createQuestion).not.toHaveBeenCalled();
});
