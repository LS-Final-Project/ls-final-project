import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import MailSent from './MailSent';
import { setApiMode } from '../api/client';
import type { SendIntent } from '../api/mail';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const intent = (over: Partial<SendIntent>): SendIntent => ({
  intent_id: '11111111-1111-4111-8111-111111111111', business_ref: 'CS26-0001', business_version: 1, subject: '합성 제목', status: 'ACCEPTED', error_code: null,
  attempts: 1, created_at: '2026-10-29T00:20:00Z', submitted_at: '2026-10-29T00:20:05Z', accepted_at: '2026-10-29T00:20:06Z', sent_copy_confirmed_at: null, resend_ready: false, ...over,
});
const show = () => { setApiMode('live'); return renderAt('/mail/sent', '/mail/sent', <MailSent />); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

test('발송 기록을 상태 이름과 함께 최신순으로 보이고 건수는 표시된 행에서 센다', async () => {
  const items = [intent({ intent_id: 'a', subject: '오래된 건', created_at: '2026-10-28T01:00:00Z', status: 'FAILED', error_code: 'RECIPIENT_REJECTED', submitted_at: null, accepted_at: null }),
    intent({ intent_id: 'b', subject: '최신 건', status: 'UNKNOWN', error_code: 'SUBMIT_TIMEOUT' })];
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items } })));
  show();
  const table = await screen.findByRole('table', { name: '발송 상태' });
  const rows = within(table).getAllByRole('row').slice(1);
  expect(rows[0].textContent).toContain('최신 건');
  expect(rows[0].textContent).toContain('결과 모름');
  expect(rows[1].textContent).toContain('실패');
  expect(rows[1].textContent).toContain('RECIPIENT_REJECTED');
  expect(screen.getAllByText('확인 전').length).toBe(2);
  expect(screen.getByText(/내 발송/).textContent).toMatch(/2건, 결과 모름 1, 실패 1, 대기 0/);
});

test('상태를 고르면 서버에 그 상태만 요청한다', async () => {
  const fetch = vi.fn(async (_url: RequestInfo | URL) => jsonResponse(200, { data: { items: [] } }));
  vi.stubGlobal('fetch', fetch);
  show();
  await screen.findByText('조건에 맞는 발송 기록이 없습니다.');
  fireEvent.change(screen.getByLabelText('상태'), { target: { value: 'FAILED' } });
  await waitFor(() => expect(fetch.mock.calls.some(call => String(call[0]).endsWith('/api/v1/platform/mail/send-intents?status=FAILED'))).toBe(true));
});

test('만든 일자 범위가 거꾸로면 표를 비우고 안내한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [intent({})] } })));
  show();
  await screen.findByText('합성 제목');
  fireEvent.change(screen.getByLabelText('시작일'), { target: { value: '2026-11-02' } });
  fireEvent.change(screen.getByLabelText('종료일'), { target: { value: '2026-11-01' } });
  expect(screen.getByRole('alert').textContent).toContain('종료일은 시작일보다 빠를 수 없습니다');
  expect(screen.queryByText('합성 제목')).toBeNull();
});

test('메일을 내보내거나 서버 상태를 바꾸는 단추는 눌러지지 않는다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [intent({ status: 'FAILED', resend_ready: true })] } })));
  show();
  await screen.findByText('합성 제목');
  expect((screen.getByRole('button', { name: '다시 보내기' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: '보낸 편지함 확인' }) as HTMLButtonElement).disabled).toBe(true);
});

test('오류 응답은 오류 화면으로 보이고 예상 밖 본문은 거부한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(403, errorBody('FORBIDDEN', '권한이 없습니다.'))));
  show();
  expect(await screen.findByText(/권한이 없습니다/)).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [{ intent_id: 'x' }] } })));
  show();
  expect(await screen.findByText(/발송 상태 목록을 확인할 수 없습니다/)).toBeTruthy();
});

test('모의 응답 모드는 합성 예시임을 알린다', async () => {
  setApiMode('mock');
  renderAt('/mail/sent', '/mail/sent', <MailSent />);
  expect(await screen.findByText(/합성 예시이며 실제 발송 기록이 아닙니다/)).toBeTruthy();
});
