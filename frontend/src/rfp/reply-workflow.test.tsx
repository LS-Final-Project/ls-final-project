// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { MOCK_DETAIL, MOCK_PROPOSALS } from '../mocks/rfp-table';
import { type Proposal, type Question, type RequirementDetail, createProposal } from '../api/rfp';
import ProposalForm from './ProposalForm';
import { ReplyRecord } from './QuestionForms';
vi.mock('../api/rfp', async importOriginal => ({ ...await importOriginal<typeof import('../api/rfp')>(), createProposal: vi.fn(async () => ({ data: {} })) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const detail: RequirementDetail = { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'NOT_REQUIRED', unresolved: ['시험 시간 확인'] } };
const question = {
  question_id: 'q1', requirement_id: MOCK_DETAIL.requirement_id, question_text: '시험 조건 확인', status: 'REPLIED', target: 'CUSTOMER', issue_type: 'CONDITION_MISSING',
  reply: { reply_id: 'reply1', reply_text: '요구 시험전압은 36 kV입니다. 시험시간은 아직 확인 중입니다.', source_ref: '고객 회신 11월 4일', received_at: '2026-11-04T00:00:00Z', recorded_at: '2026-11-04T00:01:00Z', recorded_by: 'user1' },
} as Question;
function show(node: React.ReactNode) { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>{node}</QueryClientProvider>); }
it('회신 원문과 출처를 읽어도 요구나 판정을 저장하지 않는다', () => {
  show(<ReplyRecord question={question} onReview={vi.fn()} />);
  expect(screen.getByText(question.reply!.reply_text)).toBeTruthy();
  expect(screen.getByText('고객 회신 11월 4일')).toBeTruthy();
  expect(createProposal).not.toHaveBeenCalled();
});
it('회신에서 사람이 선택한 값만 CAS와 회신 연결을 넣어 제안하고 판정을 자동 변경하지 않는다', async () => {
  show(<ProposalForm projectId="project" requirement={detail} sourceQuestion={question} onCancel={vi.fn()} onReload={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByLabelText('고객 요구값·단위 변경'));
  fireEvent.change(screen.getByLabelText('요구값'), { target: { value: '36' } });
  fireEvent.change(screen.getByLabelText('단위'), { target: { value: 'kV' } });
  fireEvent.change(screen.getByLabelText('제안 사유'), { target: { value: '고객 회신에서 시험전압 확인' } });
  expect((screen.getByRole('button', { name: '제안 저장' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('회신과 근거를 확인하고 바꿀 항목을 직접 선택했습니다.'));
  fireEvent.click(screen.getByRole('button', { name: '제안 저장' }));
  await waitFor(() => expect(createProposal).toHaveBeenCalledOnce());
  const body = vi.mocked(createProposal).mock.calls[0][1];
  expect(body).toMatchObject({ requirement_version: MOCK_DETAIL.version, source_question_id: 'q1', proposed: { spec: { value: '36', unit: 'kV' } } });
  expect(body.proposed.status).toBeUndefined();
  expect(body.proposed.unresolved).toBeUndefined();
});
it('확인 중 회신을 읽어도 남은 확인 사항을 자동으로 지우지 않는다', () => {
  show(<ProposalForm projectId="project" requirement={detail} sourceQuestion={question} onCancel={vi.fn()} onReload={vi.fn()} onSaved={vi.fn()} />);
  expect((screen.getByLabelText('미확인') as HTMLInputElement).value).toBe(detail.judgement.unresolved.join(', '));
  expect((screen.getByLabelText('상태', { selector: '#proposal-status' }) as HTMLSelectElement).value).toBe('');
  expect(createProposal).not.toHaveBeenCalled();
});

it('승인된 회신 변경과 수신만 한 회신을 구분하고 승인대기에는 중복 제안 버튼을 내지 않는다', () => {
  const proposal = { ...MOCK_PROPOSALS[0], source_ref: { question_id: question.question_id }, status: 'PENDING' } as Proposal;
  show(<ReplyRecord question={question} proposals={[proposal]} onReview={vi.fn()} />);
  expect(screen.getByText('변경 제안 승인 대기')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '요구에 반영할 내용 검토' })).toBeNull();
  cleanup();
  show(<ReplyRecord question={question} proposals={[{ ...proposal, status: 'APPROVED' }]} />);
  expect(screen.getByText('승인된 변경 있음 · 회신 전체가 반영됐다는 뜻은 아닙니다.')).toBeTruthy();
});

it('해결한 항목을 고르면 그 항목만 남은 확인에서 제외한다', async () => {
  const two = { ...detail, judgement: { ...detail.judgement, unresolved: ['시험전압 확인', '시험시간 확인'] } };
  show(<ProposalForm projectId="project" requirement={two} sourceQuestion={question} onCancel={vi.fn()} onReload={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByLabelText('회신으로 확인: 시험전압 확인'));
  expect((screen.getByLabelText('미확인') as HTMLInputElement).value).toBe('시험시간 확인');
  fireEvent.change(screen.getByLabelText('제안 사유'), { target: { value: '회신에서 전압만 확인' } });
  fireEvent.click(screen.getByLabelText('회신과 근거를 확인하고 바꿀 항목을 직접 선택했습니다.'));
  fireEvent.click(screen.getByRole('button', { name: '제안 저장' }));
  await waitFor(() => expect(createProposal).toHaveBeenCalledOnce());
  expect(vi.mocked(createProposal).mock.calls[0][1].proposed).toEqual({ unresolved: ['시험시간 확인'] });
});
