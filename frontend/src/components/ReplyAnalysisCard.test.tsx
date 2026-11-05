// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse } from '../test/render';
import ReplyAnalysisCard from './ReplyAnalysisCard';

const T = '22222222-2222-4222-8222-222222222222';
const M = '33333333-3333-4333-8333-333333333333';
const reply = { message_id: M, received_at: '2026-11-05T01:20:00Z', body: 'photos attached' };
const fit = { same_request: 'APPLIES', same_request_reason: '요청에 대한 답장', same_unit: 'NOT_MENTIONED', same_unit_reason: '시리얼 없음', same_version: 'NOT_MENTIONED', same_version_reason: '판본 언급 없음' };
const view = (next: string, provided: string[], missing: string[], answered = ['photos']) => ({ data: {
  ticket_id: T, provenance: 'ai_draft', message_id: M,
  analysis: { id: 'a1', message_id: M, created_at: '2026-11-05T01:25:00Z', requested_model: 'm', model_version: null, prompt_version: 'v2', input_version: 'cs-reply-input-v2', stale: false,
    interpretation: { applicability: fit, answered_codes: answered, new_facts: [{ text: '어제 다시 고장', kind: 'STATED' }], remaining_questions: missing.map(code => ({ info_code: code, question: `${code}?` })) },
    update: { applicability: fit, new_facts: [], remaining_questions: [], provided_codes: provided, still_missing: missing, next_action: next } },
  latest_attempt: { id: 'a1', message_id: M, outcome: 'VALID', error_code: null, created_at: '2026-11-05T01:25:00Z' } } });

beforeEach(() => setApiMode('live'));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function show(props: Partial<Parameters<typeof ReplyAnalysisCard>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const apply = vi.fn(async () => undefined);
  render(<QueryClientProvider client={client}><ReplyAnalysisCard ticketId={T} replies={[reply]} canCreate provided={['error_code']} apply={apply} onNote={() => undefined} {...props} /></QueryClientProvider>);
  return apply;
}

describe('회신 반영 분석 카드', () => {
  test('해석 결과의 다음 행동·답한 정보·부족한 정보를 보이고, 확인한 사유로 답한 정보를 반영한다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, view('REQUEST_AGAIN', ['error_code', 'photos'], ['serial_no']))));
    const apply = show();
    expect(await screen.findByText('부족한 정보 다시 요청')).toBeTruthy();
    expect(screen.getByText('✓ 제품·명판 사진')).toBeTruthy();
    expect(screen.getAllByText('시리얼 번호').length).toBeGreaterThan(0);
    expect(screen.getByText('어제 다시 고장')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '답한 정보 반영 1' }));
    const reason = screen.getByLabelText('정정 사유') as HTMLInputElement;
    expect(reason.value).toContain('제품·명판 사진');
    fireEvent.click(screen.getByRole('button', { name: '반영' }));
    await waitFor(() => expect(apply).toHaveBeenCalledWith(['error_code', 'photos'], reason.value));
  });

  test('다른 장비 회신(담당자 판단)은 답한 정보를 반영하는 단추를 두지 않는다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, view('ESCALATE_TO_AGENT', ['error_code'], ['photos']))));
    show();
    expect(await screen.findByText('담당자 판단 필요')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /답한 정보 반영/ })).toBeNull();
  });

  test('이미 반영된 정보뿐이면 반영 단추를 끈다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, view('DRAFT_ANSWER', ['error_code', 'photos'], []))));
    show({ provided: ['error_code', 'photos'] });
    expect(await screen.findByText('답변 작성')).toBeTruthy();
    expect((screen.getByRole('button', { name: '답한 정보 반영' }) as HTMLButtonElement).disabled).toBe(true);
  });

  test('본문 있는 회신이 없으면 카드를 그리지 않고, 기능이 꺼진 배포(404)도 숨긴다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(404, errorBody('NOT_FOUND', '없음'))));
    show({ replies: [{ ...reply, body: null }] });
    expect(screen.queryByLabelText('회신 반영 분석')).toBeNull();
    cleanup();
    show();
    await waitFor(() => expect((fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.queryByLabelText('회신 반영 분석')).toBeNull());
  });

  test('해석이 없으면 실행 단추를 보이고, C/S 담당자가 아니면 단추가 없다', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { ticket_id: T, provenance: 'ai_draft', message_id: M, analysis: null, latest_attempt: null } })));
    show();
    expect(await screen.findByRole('button', { name: '회신 반영 분석' })).toBeTruthy();
    cleanup();
    show({ canCreate: false });
    expect(await screen.findByText(/C\/S 담당자만 실행/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: '회신 반영 분석' })).toBeNull();
  });
});
