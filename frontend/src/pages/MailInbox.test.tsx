import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import MailInbox from './MailInbox';
import { setApiMode } from '../api/client';
import type { MailTriage, ReplyReview } from '../api/mail';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const review = (over: Partial<ReplyReview>): ReplyReview => ({
  message_id: '44444444-4444-4444-8444-444444444444', received_at: '2026-10-29T08:41:00Z', created_at: '2026-10-29T08:42:00Z', reason: 'AMBIGUOUS',
  candidates: [{ owner_service: 'cs', business_ref: '66666666-6666-4666-8666-666666666666', method: 'HEADER' }, { owner_service: 'platform', business_ref: 'THREAD-9', method: 'THREAD' }], ...over,
});
const triage = (over: Partial<MailTriage> = {}): MailTriage => ({
  message_id: '44444444-4444-4444-8444-444444444444', version: 2, latest_version: 3, is_latest: false, mail_type: 'CS', needs_review: true, attachments_pending: true,
  evidence: [{ source: 'subject', start: 0, end: 3 }, { source: 'reply_header', start: null, end: null }], link_suggestion: 'EXISTING_THREAD', provenance_kind: 'ai_draft', reason: null,
  created_at: '2026-10-29T08:42:00Z', ...over,
});
const serve = (items: ReplyReview[], tri: MailTriage | Response = triage()) => vi.fn(async (url: RequestInfo | URL) => String(url).includes('/triage')
  ? (tri instanceof Response ? tri : jsonResponse(200, { data: tri })) : jsonResponse(200, { data: { items } }));
const show = () => { setApiMode('live'); return renderAt('/mail/inbox', '/mail/inbox', <MailInbox />); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

test('검토 대기 답장을 사유·후보 수와 함께 최신순으로 보이고 선택한 메일의 분류를 읽는다', async () => {
  const older = review({ message_id: '55555555-5555-4555-8555-555555555555', received_at: '2026-10-28T08:00:00Z', reason: 'UNMATCHED_REPLY', candidates: [] });
  const fetch = serve([older, review({})]);
  vi.stubGlobal('fetch', fetch);
  show();
  const table = await screen.findByRole('table', { name: '검토 대기 메일' });
  const rows = within(table).getAllByRole('row').slice(1);
  expect(rows[0].textContent).toContain('후보가 여럿');
  expect(rows[0].textContent).toContain('2건');
  expect(rows[1].textContent).toContain('맞는 후보 없음');
  expect(await screen.findByText('C/S 문의')).toBeTruthy();
  expect(screen.getByText('기존 건에 이어 붙이기')).toBeTruthy();
  expect(screen.getByText(/제목 0~3자, 답장 헤더/)).toBeTruthy();
  expect(screen.getByText(/\(최신 3\)/)).toBeTruthy();
  expect(screen.getByText('첨부 확인이 끝나지 않았습니다')).toBeTruthy();
  expect(fetch.mock.calls.some(call => String(call[0]).endsWith('/mail/messages/44444444-4444-4444-8444-444444444444/triage'))).toBe(true);
});

test('다른 메일을 고르면 그 메일의 분류를 다시 읽고 후보 표를 바꾼다', async () => {
  const other = review({ message_id: '77777777-7777-4777-8777-777777777777', received_at: '2026-10-28T08:00:00Z', candidates: [] });
  const fetch = serve([review({}), other]);
  vi.stubGlobal('fetch', fetch);
  show();
  await screen.findByRole('table', { name: '연결 후보' });
  fireEvent.click(screen.getByRole('button', { name: '77777777' }));
  await screen.findByText('시스템이 찾은 후보가 없습니다');
  expect(screen.queryByRole('table', { name: '연결 후보' })).toBeNull();
  expect(fetch.mock.calls.some(call => String(call[0]).includes('/77777777-7777-4777-8777-777777777777/triage'))).toBe(true);
});

test('검토 사유로 거르고 일자 범위가 거꾸로면 표를 비운다', async () => {
  vi.stubGlobal('fetch', serve([review({}), review({ message_id: '88888888-8888-4888-8888-888888888888', reason: 'CONFLICT', received_at: '2026-10-27T08:00:00Z' })]));
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  fireEvent.change(screen.getByLabelText('검토 사유'), { target: { value: 'CONFLICT' } });
  await waitFor(() => expect(within(screen.getByRole('table', { name: '검토 대기 메일' })).getAllByRole('row').length).toBe(2));
  fireEvent.change(screen.getByLabelText('시작일'), { target: { value: '2026-11-02' } });
  fireEvent.change(screen.getByLabelText('종료일'), { target: { value: '2026-11-01' } });
  expect(screen.getByRole('alert').textContent).toContain('종료일은 시작일보다 빠를 수 없습니다');
  expect(screen.getByText('검토할 메일이 없습니다.')).toBeTruthy();
});

test('서버 상태를 바꾸는 단추는 눌러지지 않는다', async () => {
  vi.stubGlobal('fetch', serve([review({})]));
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  for (const name of ['연결', '무시', '다시 분류']) expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true);
});

test('목록 오류와 예상 밖 본문, 분류 오류를 각각 보인다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(403, errorBody('FORBIDDEN', '권한이 없습니다.'))));
  show();
  expect(await screen.findByText(/권한이 없습니다/)).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [{ message_id: 'x' }] } })));
  show();
  expect(await screen.findByText(/검토 대기 메일 목록을 확인할 수 없습니다/)).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', serve([review({})], jsonResponse(404, errorBody('NOT_FOUND', '분류가 없습니다.'))));
  show();
  await screen.findByRole('table', { name: '검토 대기 메일' });
  expect(await screen.findByText('찾을 수 없습니다')).toBeTruthy();
});

test('모의 응답 모드는 합성 예시임을 알린다', async () => {
  setApiMode('mock');
  renderAt('/mail/inbox', '/mail/inbox', <MailInbox />);
  expect(await screen.findByText(/합성 예시이며 실제 메일이 아닙니다/)).toBeTruthy();
  expect(await screen.findByText('C/S 문의')).toBeTruthy();
});
