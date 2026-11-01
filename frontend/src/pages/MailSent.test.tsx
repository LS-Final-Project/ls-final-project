import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import MailSent from './MailSent';
import { clearSessionCsrfToken, setApiMode } from '../api/client';
import type { SendIntent } from '../api/mail';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const intent = (over: Partial<SendIntent>): SendIntent => ({
  intent_id: '11111111-1111-4111-8111-111111111111', business_ref: 'CS26-0001', business_version: 1, subject: '합성 제목', status: 'ACCEPTED', error_code: null,
  attempts: 1, created_at: '2026-10-29T00:20:00Z', submitted_at: '2026-10-29T00:20:05Z', accepted_at: '2026-10-29T00:20:06Z', sent_copy_confirmed_at: null, resend_ready: false, ...over,
});
const show = () => { setApiMode('live'); return renderAt('/mail/sent', '/mail/sent', <MailSent />); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); clearSessionCsrfToken(); });

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

const unknown = (over: Partial<SendIntent> = {}) => intent({ intent_id: '5555eeee-5555-4555-8555-555555555555', subject: '결과 모름 건', status: 'UNKNOWN', error_code: 'SUBMIT_TIMEOUT', ...over });
async function withSession() { const { request } = await import('../api/client'); await request('/api/v1/platform/me'); }
type Act = (path: string, init: RequestInit) => Response;
const serveActions = (items: SendIntent[], act: Act) => vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
  const path = String(url);
  if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: {} }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok' } });
  if (init?.method === 'POST') return act(path, init);
  return jsonResponse(200, { data: { items } });
});
const pick = async (subject: string) => { show(); fireEvent.click(await screen.findByLabelText(`${subject} 선택`)); };
const button = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement;

test('고른 발송에 따라 보낸 편지함 확인과 다시 보내기가 켜진다(다시 보내기는 resend_ready일 때만)', async () => {
  vi.stubGlobal('fetch', serveActions([unknown({ intent_id: 'a'.repeat(8) + '-aaaa-4aaa-8aaa-aaaaaaaaaaaa', subject: '아직 유예 중' }), unknown({ resend_ready: true }), intent({ intent_id: 'c'.repeat(8) + '-cccc-4ccc-8ccc-cccccccccccc', subject: '접수된 건' })], () => jsonResponse(200, {})));
  show();
  await screen.findByText('결과 모름 건');
  expect(button('보낸 편지함 확인').disabled).toBe(true);
  expect(button('다시 보내기').disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('아직 유예 중 선택'));
  expect(button('보낸 편지함 확인').disabled).toBe(false);
  expect(button('다시 보내기').disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('결과 모름 건 선택'));
  expect(button('다시 보내기').disabled).toBe(false);
  fireEvent.click(screen.getByLabelText('접수된 건 선택'));
  expect(button('보낸 편지함 확인').disabled).toBe(true);
  expect(button('다시 보내기').disabled).toBe(true);
});

test('보낸 편지함 확인은 세션 보호 정보와 함께 요청하고 사본 유무를 안내한 뒤 목록을 다시 읽는다', async () => {
  let confirmed = false;
  const fetch = serveActions([unknown()], () => jsonResponse(200, { data: { intent_id: 'x', sent_copy_confirmed: confirmed, status: 'UNKNOWN' } }));
  vi.stubGlobal('fetch', fetch);
  setApiMode('live');
  await withSession();
  await pick('결과 모름 건');
  fireEvent.click(button('보낸 편지함 확인'));
  expect(await screen.findByText(/보낸 편지함에 사본이 없습니다/)).toBeTruthy();
  const post = fetch.mock.calls.find(call => (call[1] as RequestInit | undefined)?.method === 'POST')!;
  expect(String(post[0])).toMatch(/\/send-intents\/5555eeee-5555-4555-8555-555555555555\/check-sent-copy$/);
  expect(((post[1] as RequestInit).headers as Record<string, string>)['X-CSRF-Token']).toBe('tok');
  confirmed = true;
  fireEvent.click(button('보낸 편지함 확인'));
  expect(await screen.findByText('보낸 편지함에서 사본을 확인했습니다.')).toBeTruthy();
  const lists = fetch.mock.calls.filter(call => !(call[1] as RequestInit | undefined)?.method && String(call[0]).includes('/send-intents')).length;
  expect(lists).toBeGreaterThanOrEqual(3);
});

test('다시 보내기는 사유를 받아 한 번만 요청하고 등록 안내를 보인다', async () => {
  const bodies: string[] = [];
  vi.stubGlobal('fetch', serveActions([unknown({ resend_ready: true })], (_path, init) => { bodies.push(String(init.body)); return jsonResponse(200, { data: { intent_id: 'x', status: 'QUEUED', task_registered: true } }); }));
  setApiMode('live');
  await withSession();
  await pick('결과 모름 건');
  fireEvent.click(button('다시 보내기'));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: '다시 보내기' }));
  expect(bodies).toHaveLength(0);
  fireEvent.change(within(dialog).getByLabelText('사유'), { target: { value: '  보낸 편지함에 사본이 없어 재전송  ' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '다시 보내기' }));
  expect(await screen.findByText('다시 보내기를 등록했습니다. 발송 결과는 목록에서 확인하세요.')).toBeTruthy();
  expect(bodies).toEqual([JSON.stringify({ reason: '보낸 편지함에 사본이 없어 재전송' })]);
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('서버가 거부하면(409) 그 이유를 보이고, 결과를 알 수 없는 오류는 다시 보내지 않고 확인하라고 안내한다', async () => {
  const posts: number[] = [];
  let reply: Response = jsonResponse(409, errorBody('MAIL_RESEND_NOT_ALLOWED', '유예 시간이 지나지 않았습니다.'));
  vi.stubGlobal('fetch', serveActions([unknown({ resend_ready: true })], () => { posts.push(1); return reply.clone(); }));
  setApiMode('live');
  await withSession();
  await pick('결과 모름 건');
  fireEvent.click(button('다시 보내기'));
  let dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('사유'), { target: { value: '사유' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '다시 보내기' }));
  expect((await screen.findByRole('alert')).textContent).toContain('유예 시간이 지나지 않았습니다.');
  reply = jsonResponse(503, errorBody('UNAVAILABLE', '일시 장애'));
  fireEvent.click(button('다시 보내기'));
  dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('사유'), { target: { value: '사유' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '다시 보내기' }));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('같은 요청을 다시 보내지 않고'));
  expect(posts).toHaveLength(2);
});

test('세션 확인 정보가 없으면 요청하지 않고 안내한다', async () => {
  const fetch = vi.fn(async (url: RequestInfo | URL, _init?: RequestInit) => jsonResponse(200, { data: { items: [unknown({ resend_ready: true })] } }));
  vi.stubGlobal('fetch', fetch);
  await pick('결과 모름 건');
  fireEvent.click(button('보낸 편지함 확인'));
  expect((await screen.findByRole('alert')).textContent).toContain('세션 확인 정보가 없습니다');
  expect(fetch.mock.calls.some(call => (call[1] as RequestInit | undefined)?.method === 'POST')).toBe(false);
});

test('운영 빌드(개발 모드 아님)에서도 발송 기록을 읽어 보인다', async () => {
  vi.stubEnv('DEV', false);
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [intent({ subject: '운영 빌드 건' })] } })));
  show();
  expect(await screen.findByText('운영 빌드 건')).toBeTruthy();
  expect(screen.queryByText(/운영 인증 중계와 권한 검증 후 제공됩니다/)).toBeNull();
  vi.unstubAllEnvs();
});
