// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { QuestionForm } from './QuestionForms';
import { GuidedRequirementReview } from './GuidedRequirementReview';
import { createQuestion, listQuestions, type Question, type RequirementDetail } from '../api/rfp';
import { me } from '../api/platform';
import { MOCK_DETAIL } from '../mocks/rfp-table';
vi.mock('../api/rfp', async original => ({ ...await original<object>(), createQuestion: vi.fn(), listQuestions: vi.fn() }));
vi.mock('../api/platform', async original => ({ ...await original<object>(), me: vi.fn() }));
const requirement = { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'NOT_REQUIRED' }, spec: { ...MOCK_DETAIL.spec, attribute: '보증 기간', value: '36', unit: '개월' } } as RequirementDetail;
const question = { question_id: 'question-record', requirement_id: requirement.requirement_id, question_text: '보증 연장 가능 여부 확인', target: 'INTERNAL', target_dept: 'SALES_OPS', issue_type: 'PURCHASE_TERMS', status: 'REPLIED', created_at: '2026-11-03T01:00:00Z', reply_received_at: '2026-11-03T02:00:00Z' } as Question;
beforeEach(() => { vi.mocked(listQuestions).mockResolvedValue({ data: { items: [] } }); vi.mocked(me).mockResolvedValue({ data: { id: 'actor' } } as never); vi.mocked(createQuestion).mockResolvedValue({ data: question }); });
afterEach(() => { cleanup(); vi.clearAllMocks(); });
function show({ departments = ['SALES_OPS', 'RND_FW'], canWrite = true, missing = false }: { departments?: string[]; canWrite?: boolean; missing?: boolean } = {}) {
  const saved = vi.fn();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><GuidedRequirementReview projectId="project" actorId="actor" requirement={requirement} departments={departments} canWrite={canWrite} customerValueMissing={missing} commercial={!missing} designated={false} onSaved={saved} /></MemoryRouter></QueryClientProvider>);
  return saved;
}
test('commercial review starts from actual records and saves an editable internal purchase-terms question only to a selected project department', async () => {
  vi.mocked(listQuestions).mockResolvedValueOnce({ data: { items: [question, { ...question, question_id: 'other', requirement_id: 'another', question_text: '다른 요구 기록' }] } });
  const saved = show();
  await screen.findByText(question.question_text);
  expect(screen.getByText(/회신 기록 일시/)).toBeTruthy();expect(screen.getByText(/회신이 기록되었습니다. 내용 확인/)).toBeTruthy();expect(screen.queryByText('다른 요구 기록')).toBeNull();
  expect(createQuestion).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '검토 질문 작성' }));
  expect((screen.getByLabelText('대상') as HTMLSelectElement).value).toBe('');
  expect(screen.queryByRole('option', { name: '고객' })).toBeNull();
  expect((screen.getByLabelText('유형') as HTMLSelectElement).value).toBe('PURCHASE_TERMS');
  expect((screen.getByLabelText('질문') as HTMLTextAreaElement).value).toContain('36 개월');
  expect((screen.getByRole('button', { name: '질문 저장' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('대상'), { target: { value: 'SALES_OPS' } });
  fireEvent.change(screen.getByLabelText('질문'), { target: { value: '보증 36개월 적용 비용과 승인 조건 확인 부탁드립니다.' } });
  fireEvent.click(screen.getByRole('button', { name: '질문 저장' }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(createQuestion).toHaveBeenCalledWith('project', expect.objectContaining({ requirement_id: requirement.requirement_id, target: 'INTERNAL', target_dept: 'SALES_OPS', issue_type: 'PURCHASE_TERMS', question_text: '보증 36개월 적용 비용과 승인 조건 확인 부탁드립니다.' }), expect.any(String), { useMockFallback: false });
  expect(screen.getByRole('status').textContent).toContain('질문을 앱에 기록했습니다.');
});
test('no project review departments never falls back to a customer question', async () => {
  show({ departments: [] });await screen.findByText('이 요구에 저장된 질문이 없습니다.');
  expect((screen.getByRole('button', { name: '검토 질문 작성' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByLabelText('질문')).toBeNull();expect(createQuestion).not.toHaveBeenCalled();
});
test('missing customer value uses customer EMPTY_VALUE without fabricating an internal department', async () => {
  show({ missing: true });await screen.findByText('이 요구에 저장된 질문이 없습니다.');
  fireEvent.click(screen.getByRole('button', { name: '고객 확인 질문 작성' }));
  expect((screen.getByLabelText('대상') as HTMLSelectElement).value).toBe('CUSTOMER');
  expect(screen.queryByRole('option', { name: '영업운영' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '질문 저장' }));
  await waitFor(() => expect(createQuestion).toHaveBeenCalledWith('project', expect.objectContaining({ target: 'CUSTOMER', target_dept: null, issue_type: 'EMPTY_VALUE' }), expect.any(String), { useMockFallback: false }));
});
test('question lookup failure remains visible and never claims there are no records', async () => {
  vi.mocked(listQuestions).mockRejectedValue(new Error('질문 조회 실패'));show();
  await screen.findByText('질문 조회 실패');expect(screen.queryByText('이 요구에 저장된 질문이 없습니다.')).toBeNull();expect(createQuestion).not.toHaveBeenCalled();
});
test('read only review preserves records and blocks writing', async () => {
  show({ canWrite: false });await screen.findByText('이 요구에 저장된 질문이 없습니다.');
  expect((screen.getByRole('button', { name: '검토 질문 작성' }) as HTMLButtonElement).disabled).toBe(true);expect(createQuestion).not.toHaveBeenCalled();
});
test('a changed actor blocks the preset question before the POST', async () => {
  vi.mocked(me).mockResolvedValue({ data: { id: 'someone-else' } } as never);show();
  await screen.findByText('이 요구에 저장된 질문이 없습니다.');fireEvent.click(screen.getByRole('button', { name: '검토 질문 작성' }));
  fireEvent.change(screen.getByLabelText('대상'), { target: { value: 'SALES_OPS' } });fireEvent.click(screen.getByRole('button', { name: '질문 저장' }));
  await screen.findByText(/세션 사용자가 변경/);expect(createQuestion).not.toHaveBeenCalled();
});

test('invalid internal presets require a manual valid department and never fall back to customer', async () => {
  render(<QueryClientProvider client={new QueryClient()}><QuestionForm projectId="project" actorId="actor" requirement={requirement} departments={['SALES_OPS']} initialTarget="NOT_REGISTERED" initialIssue="PURCHASE_TERMS" initialText="보증 조건을 확인해 주세요." useMockFallback={false} onCancel={vi.fn()} onSaved={vi.fn()} /></QueryClientProvider>);
  expect((screen.getByLabelText('대상') as HTMLSelectElement).value).toBe('');expect(screen.queryByRole('option', { name: '고객' })).toBeNull();
  expect((screen.getByRole('button', { name: '질문 저장' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('대상'), { target: { value: 'SALES_OPS' } });fireEvent.click(screen.getByRole('button', { name: '질문 저장' }));
  await waitFor(() => expect(createQuestion).toHaveBeenCalledWith('project', expect.objectContaining({ target: 'INTERNAL', target_dept: 'SALES_OPS', issue_type: 'PURCHASE_TERMS' }), expect.any(String), { useMockFallback: false }));
});
test('customer audience ignores an incompatible internal target preset', async () => {
  render(<QueryClientProvider client={new QueryClient()}><QuestionForm projectId="project" actorId="actor" requirement={requirement} departments={['RND_FW']} audience="CUSTOMER" initialTarget="RND_FW" initialIssue="EMPTY_VALUE" initialText="고객 요구값을 확인해 주세요." useMockFallback={false} onCancel={vi.fn()} onSaved={vi.fn()} /></QueryClientProvider>);
  expect((screen.getByLabelText('대상') as HTMLSelectElement).value).toBe('CUSTOMER');expect(screen.queryByRole('option', { name: 'R&D' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '질문 저장' }));
  await waitFor(() => expect(createQuestion).toHaveBeenCalledWith('project', expect.objectContaining({ target: 'CUSTOMER', target_dept: null, issue_type: 'EMPTY_VALUE' }), expect.any(String), { useMockFallback: false }));
});
