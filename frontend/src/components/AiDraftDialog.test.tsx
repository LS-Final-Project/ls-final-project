// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import AiDraftDialog, { pickDraft } from './AiDraftDialog';
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

test('고친 문안을 복사하고, 복사가 막히면 안내하며, 보내기는 잠겨 있다', async () => {
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
  expect((screen.getByRole('button', { name: '보내기' }) as HTMLButtonElement).disabled).toBe(true);
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
