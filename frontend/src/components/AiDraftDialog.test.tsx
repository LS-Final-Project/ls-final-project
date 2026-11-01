// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import AiDraftDialog, { pickDraft, safetyWithoutDraft, type SendOptions } from './AiDraftDialog';
import type { TicketAnalysisResponse } from '../api/cs';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
type Analysis = TicketAnalysisResponse['data'];
const sentence = (text: string, kind = 'EVIDENCE', ids: string[] = ['c1']) => ({ text, kind, evidence_chunk_ids: ids });
const option = (label: string, sentences: ReturnType<typeof sentence>[], info: { info_code: string; question: string }[] = []) => ({ label, sentences, info_requests: info });
const draft = { options: [
  option('CONCISE', [sentence('Please send the label photo.')]),
  option('DETAILED', [sentence('Thank you for reaching out.'), sentence('Please send the label photo and serial number.', 'CONDITION', [])]),
  option('INFO_FIRST', [sentence('To help you, we need the rating label photo.', 'EVIDENCE', ['c1', 'c2'])], [{ info_code: 'label_photo', question: '정격 라벨 사진' }]),
] };
const run = (over: Record<string, unknown> = {}) => ({ id: 'd1', analysis_id: 'a1', created_at: '2026-10-28T00:02:00Z', draft, error_code: null, input_version: 'v', job_id: null, model_version: 'm', outcome: 'VALID', prompt_version: 'p', provenance_kind: 'ai_draft', seq: 1, verification_status: 'VERIFIED',
  verifications: [{ id: 'v1', attempt_number: 1, draft_id: 'd1', created_at: '2026-10-28T00:03:00Z', error_code: null, input_version: 'v', job_id: null, model_version: 'm', outcome: 'VALID', prompt_version: 'p', sentences: [
    { label: 'INFO_FIRST', sentence_index: 0, verdict: 'SUPPORTED', reason: '정책표에 근거가 있음', evidence_chunk_ids: ['c1'] },
    { label: 'DETAILED', sentence_index: 1, verdict: 'UNSUPPORTED', reason: '근거 문서를 찾지 못함', evidence_chunk_ids: [] },
  ] }], ...over });
const analysis = (drafts: unknown[], latest: string | null = 'd1') => ({ drafts, latest_valid_draft_id: latest }) as unknown as Analysis;

test('초안 3개와 직접 쓰기를 보이고 추천 문안을 미리보기에 넣고, 문장별 근거와 검증 결과를 보인다', () => {
  render(<AiDraftDialog analysis={analysis([run()])} ticketLabel="CUST-1, en" />);
  expect(screen.getAllByRole('radio')).toHaveLength(4);
  expect(screen.getByLabelText(/부족한 정보 먼저 \(추천\)/)).toBeTruthy();
  expect((screen.getByLabelText('미리보기') as HTMLTextAreaElement).value).toBe('To help you, we need the rating label photo.');
  const sentences = within(screen.getByRole('list', { name: '문장별 근거와 검증' }));
  expect(sentences.getByText(/근거 있음, 근거 2건, 근거 확인: 정책표에 근거가 있음/)).toBeTruthy();
  expect(screen.getByText(/정정 정보|정격 라벨 사진/)).toBeTruthy();
  expect(screen.getByText(/RMA·방문·보증 승인은 초안이 정하지 않습니다/)).toBeTruthy();
});

test('다른 초안을 고르면 문안과 검증이 바뀌고, 근거 부족 문장은 그대로 드러난다. 직접 쓰기는 빈 칸으로 시작한다', () => {
  render(<AiDraftDialog analysis={analysis([run()])} ticketLabel="x" />);
  fireEvent.click(screen.getByLabelText('자세하게'));
  expect((screen.getByLabelText('미리보기') as HTMLTextAreaElement).value).toBe('Thank you for reaching out. Please send the label photo and serial number.');
  const sentences = within(screen.getByRole('list', { name: '문장별 근거와 검증' }));
  expect(sentences.getByText(/조건, 근거 0건, 근거 부족: 근거 문서를 찾지 못함/)).toBeTruthy();
  expect(sentences.getByText(/근거 있음, 근거 1건, 검증 결과 없음/)).toBeTruthy();
  fireEvent.click(screen.getByLabelText('직접 쓰기'));
  expect((screen.getByLabelText('미리보기') as HTMLTextAreaElement).value).toBe('');
  expect(screen.queryByRole('list', { name: '문장별 근거와 검증' })).toBeNull();
  expect((screen.getByRole('button', { name: '문안 복사' }) as HTMLButtonElement).disabled).toBe(true);
});

test('고친 문안을 복사하고, 복사가 막히면 안내하며, 보내기 옵션이 없으면 보내기 영역이 없다', async () => {
  const writeText = vi.fn(async () => undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText } });
  render(<AiDraftDialog analysis={analysis([run()])} ticketLabel="x" />);
  fireEvent.change(screen.getByLabelText('미리보기'), { target: { value: '고친 문안' } });
  fireEvent.click(screen.getByRole('button', { name: '문안 복사' }));
  expect(await screen.findByText('복사했습니다.')).toBeTruthy();
  expect(writeText).toHaveBeenCalledWith('고친 문안');
  writeText.mockRejectedValueOnce(new Error('denied'));
  fireEvent.click(screen.getByRole('button', { name: '문안 복사' }));
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain('복사하지 못했습니다'));
  expect(screen.queryByRole('button', { name: '승인하고 보내기' })).toBeNull();
});

test('초안이 없거나 쓸 수 없으면 안내하고 직접 쓰기만 남는다', () => {
  render(<AiDraftDialog analysis={analysis([], null)} ticketLabel="x" />);
  expect(screen.getByText(/아직 AI 초안이 없습니다/)).toBeTruthy();
  expect(screen.getAllByRole('radio')).toHaveLength(1);
  cleanup();
  render(<AiDraftDialog analysis={analysis([run({ draft: null, outcome: 'SCHEMA_VIOLATION' })], null)} ticketLabel="x" />);
  expect(screen.getByText(/AI 초안을 쓸 수 없습니다/)).toBeTruthy();
  cleanup();
  render(<AiDraftDialog analysis={undefined} ticketLabel="x" />);
  expect(screen.getAllByRole('radio')).toHaveLength(1);
});

test('최신 유효 초안을 우선하고, 없으면 내용이 있는 가장 최근 초안을 고른다', () => {
  expect(pickDraft(analysis([run({ id: 'old' }), run({ id: 'new' })], 'new'))?.id).toBe('new');
  expect(pickDraft(analysis([run({ id: 'a' }), run({ id: 'b', draft: null })], null))?.id).toBe('a');
  expect(pickDraft(analysis([run({ id: 'x', draft: null })], null))?.id).toBe('x');
  expect(pickDraft(undefined)).toBeUndefined();
});

const sendOpts = (over: Partial<SendOptions> = {}): SendOptions => ({
  originalSubject: 'Touch panel frozen', defaultTo: 'buyer@example.com', mailboxes: [{ id: 'mb-1', label: '사서함 mb-1' }], defaultMailboxId: 'mb-1', busy: false, onSend: vi.fn(), ...over,
});
const approve = () => fireEvent.click(screen.getByLabelText(/고객에게 메일이 나가는 것을 승인합니다/));

test('안전 문의인데 쓸 초안이 없으면 일반 안내 대신 강조 경고와 첫 응답 기한을 보인다', () => {
  const safety = { drafts: [], latest_valid_draft_id: null, fields: { urgency_flag: { effective_value: 'SAFETY' } }, rules: { first_response_due: { effective: '2026-10-28T03:00:00Z' }, ai: { first_response_due: '2026-10-28T04:00:00Z' } } } as unknown as Analysis;
  expect(safetyWithoutDraft(safety)).toEqual({ due: '2026-10-28T03:00:00Z' });
  render(<AiDraftDialog analysis={safety} ticketLabel="x" />);
  const alert = screen.getByRole('alert');
  expect(alert.textContent).toContain('안전 문의입니다. AI 초안이 없어 담당자가 직접 응답해야 합니다.');
  expect(alert.textContent).toMatch(/첫 응답 기한 2026-10-28/);
  expect(screen.queryByText(/아직 AI 초안이 없습니다/)).toBeNull();
  // 쓸 초안이 있거나 안전 문의가 아니면 경고가 없다.
  expect(safetyWithoutDraft({ ...safety, latest_valid_draft_id: 'd1' } as Analysis)).toBeNull();
  expect(safetyWithoutDraft({ ...safety, fields: { urgency_flag: { effective_value: 'NONE' } } } as unknown as Analysis)).toBeNull();
  expect(safetyWithoutDraft(undefined)).toBeNull();
});

test('안전 문의의 기한이 없으면 기한 없이 경고만 보인다', () => {
  const safety = { drafts: [], latest_valid_draft_id: null, fields: { urgency_flag: { effective_value: 'SAFETY' } }, rules: { first_response_due: { effective: null }, ai: null } } as unknown as Analysis;
  render(<AiDraftDialog analysis={safety} ticketLabel="x" />);
  expect(screen.getByRole('alert').textContent).not.toContain('첫 응답 기한');
});

test('고른 초안을 검토해 보낼 때 초안·선택지·요청 정보 코드를 담고, 확인 체크 전에는 보내지 않는다', () => {
  const onSend = vi.fn();
  render(<AiDraftDialog analysis={analysis([run()])} ticketLabel="x" send={sendOpts({ onSend })} />);
  const button = screen.getByRole('button', { name: '승인하고 보내기' }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  expect(screen.getByText('내용과 받는 사람을 확인했다고 표시하세요')).toBeTruthy();
  approve();
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  expect(onSend).toHaveBeenCalledWith({
    kind: 'INFO_REQUEST', mailbox_id: 'mb-1', subject: 'Re: Touch panel frozen', body: 'To help you, we need the rating label photo.', body_type: 'text',
    to: ['buyer@example.com'], requested_info_codes: ['label_photo'], draft_id: 'd1', draft_option: 'INFO_FIRST',
  });
});

test('문안을 고쳐 더는 묻지 않는 정보는 체크를 풀어 요청 코드에서 뺀다. 답변으로 바꾸면 코드를 비운다', () => {
  const onSend = vi.fn();
  render(<AiDraftDialog analysis={analysis([run()])} ticketLabel="x" send={sendOpts({ onSend })} />);
  fireEvent.click(screen.getByLabelText('정격 라벨 사진'));
  approve(); fireEvent.click(screen.getByRole('button', { name: '승인하고 보내기' }));
  expect(onSend.mock.calls[0][0]).toMatchObject({ kind: 'INFO_REQUEST', requested_info_codes: [] });
  onSend.mockClear();
  fireEvent.click(screen.getByLabelText('답변'));
  approve(); fireEvent.click(screen.getByRole('button', { name: '승인하고 보내기' }));
  expect(onSend.mock.calls[0][0]).toMatchObject({ kind: 'ANSWER', requested_info_codes: [] });
});

test('직접 쓴 문안은 초안 연결 없이 보내고, 받는 사람 여러 명을 나눠 담는다', () => {
  const onSend = vi.fn();
  render(<AiDraftDialog analysis={analysis([run()])} ticketLabel="x" send={sendOpts({ onSend })} />);
  fireEvent.click(screen.getByLabelText('직접 쓰기'));
  fireEvent.change(screen.getByLabelText('미리보기'), { target: { value: '  직접 쓴 답변  ' } });
  fireEvent.change(screen.getByLabelText('받는 사람'), { target: { value: 'a@example.com, b@example.com' } });
  fireEvent.change(screen.getByLabelText('제목'), { target: { value: ' 답변드립니다 ' } });
  approve(); fireEvent.click(screen.getByRole('button', { name: '승인하고 보내기' }));
  const sent = onSend.mock.calls[0][0];
  expect(sent).toMatchObject({ kind: 'ANSWER', body: '직접 쓴 답변', subject: '답변드립니다', to: ['a@example.com', 'b@example.com'], requested_info_codes: [] });
  expect(sent).not.toHaveProperty('draft_id');
  expect(sent).not.toHaveProperty('draft_option');
});

test('최신 유효 초안이 아닌 초안을 보고 있으면 초안 연결을 남기지 않는다', () => {
  const onSend = vi.fn();
  render(<AiDraftDialog analysis={analysis([run({ id: 'old' })], null)} ticketLabel="x" send={sendOpts({ onSend })} />);
  approve(); fireEvent.click(screen.getByRole('button', { name: '승인하고 보내기' }));
  expect(onSend.mock.calls[0][0]).not.toHaveProperty('draft_id');
});

test('보낼 수 없는 이유를 단추 옆에 적는다: 이유 제공, 사서함 없음·읽는 중, 주소 오류, 제목·문안 없음', () => {
  const view = (opts: Partial<SendOptions>) => { cleanup(); render(<AiDraftDialog analysis={analysis([run()])} ticketLabel="x" send={sendOpts(opts)} />); };
  const button = () => screen.getByRole('button', { name: '승인하고 보내기' }) as HTMLButtonElement;
  view({ disabled: '검토중인 문의만 보낼 수 있습니다' });
  approve(); expect(button().disabled).toBe(true); expect(screen.getByText('검토중인 문의만 보낼 수 있습니다')).toBeTruthy();
  view({ mailboxes: undefined }); expect(screen.getByText('사서함 연결을 읽는 중입니다')).toBeTruthy();
  view({ mailboxes: [] }); expect(screen.getByText(/연결된 사서함이 없습니다/)).toBeTruthy();
  view({ mailboxes: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }], defaultMailboxId: null });
  expect(screen.getByText('보낼 사서함을 고르세요')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('보낼 사서함'), { target: { value: 'b' } });
  fireEvent.change(screen.getByLabelText('받는 사람'), { target: { value: 'not-an-address' } });
  expect(screen.getByText('받는 사람 주소 형식을 확인하세요')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('받는 사람'), { target: { value: '' } });
  expect(screen.getByText('받는 사람을 입력하세요')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('받는 사람'), { target: { value: 'x@example.com' } });
  fireEvent.change(screen.getByLabelText('제목'), { target: { value: ' ' } });
  expect(screen.getByText('제목을 입력하세요')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('제목'), { target: { value: '제목' } });
  fireEvent.click(screen.getByLabelText('직접 쓰기'));
  expect(screen.getByText('보낼 문안이 비어 있습니다')).toBeTruthy();
});

test('내용을 고치면 확인 체크가 풀려 다시 확인해야 한다', () => {
  render(<AiDraftDialog analysis={analysis([run()])} ticketLabel="x" send={sendOpts()} />);
  approve();
  expect((screen.getByRole('button', { name: '승인하고 보내기' }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.change(screen.getByLabelText('받는 사람'), { target: { value: 'other@example.com' } });
  expect((screen.getByRole('button', { name: '승인하고 보내기' }) as HTMLButtonElement).disabled).toBe(true);
  approve();
  fireEvent.change(screen.getByLabelText('미리보기'), { target: { value: '문안을 고쳤습니다.' } });
  expect((screen.getByRole('button', { name: '승인하고 보내기' }) as HTMLButtonElement).disabled).toBe(true);
});
