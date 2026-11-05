// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { markNotificationRead, notificationPath, NotificationSessionChanged, savePushSubscription, unreadCount } from './notifications';
const A = '11111111-1111-4111-8111-111111111111';
afterEach(() => vi.unstubAllGlobals());
it.each(['https://evil.invalid/cs/tickets/x', '//evil.invalid', '/cs/tickets/../account', '/tft/projects/x', `/cs/tickets/${A}?next=evil`, `/tft/projects/${A}#x`, `/cs/tickets/${A}/extra`])('허용하지 않은 알림 경로를 거부한다: %s', path => expect(notificationPath(path)).toBeNull());
it('실제 두 내부 경로만 허용한다', () => { expect(notificationPath(`/cs/tickets/${A}`)).toBe(`/cs/tickets/${A}`); expect(notificationPath(`/tft/projects/${A}`)).toBe(`/tft/projects/${A}`); });
it('계정이 바뀌면 읽음 POST와 구독 PUT을 보내지 않는다', async () => {
  const f = vi.fn(async () => new Response(JSON.stringify({ data: { id: 'another-actor' } }), { headers: { 'X-CSRF-Token': 'new-session' } })); vi.stubGlobal('fetch', f);
  await expect(markNotificationRead(A, A)).rejects.toBeInstanceOf(NotificationSessionChanged);
  await expect(savePushSubscription(A, { endpoint: 'test-only', keys: { p256dh: 'test-only', auth: 'test-only' } })).rejects.toBeInstanceOf(NotificationSessionChanged);
  expect(f.mock.calls).toHaveLength(2);
});
it('현재 세션 CSRF로만 읽음을 요청하고 실패를 숨기지 않는다', async () => {
  const f = vi.fn(async (url: string) => url.endsWith('/me') ? new Response(JSON.stringify({ data: { id: A } }), { headers: { 'X-CSRF-Token': 'fresh-csrf' } }) : new Response('{}', { status: 503 })); vi.stubGlobal('fetch', f);
  await expect(markNotificationRead(A, A)).rejects.toMatchObject({ status: 503 });
  const init = (f.mock.calls as unknown as [string, RequestInit][])[1][1];
  expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('fresh-csrf');
});
it('안 읽음 조회 오류를 가짜 0으로 바꾸지 않는다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 404 })));
  await expect(unreadCount()).rejects.toMatchObject({ status: 404 });
});
