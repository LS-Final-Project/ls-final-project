// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { clearPush, enablePush, reconcilePush } from './push';
import { deletePushSubscription, savePushSubscription } from '../api/notifications';
vi.mock('../api/notifications', () => ({ deletePushSubscription: vi.fn(), savePushSubscription: vi.fn() }));
const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
// Synthetic structure for fake browser tests only; never a runtime fallback or real VAPID key.
const KEY = btoa(String.fromCharCode(4, ...new Array(64).fill(1))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
function browser() {
  const subscription = { endpoint: 'https://fcm.googleapis.com/fcm/send/test-only', unsubscribe: vi.fn(async () => true), toJSON: () => ({ endpoint: 'https://fcm.googleapis.com/fcm/send/test-only', keys: { p256dh: 'test-only', auth: 'test-only' } }) };
  const active = { scriptURL: new URL('/sw.js', location.origin).href, postMessage: vi.fn((_message, ports) => { ports[0].postMessage({ ok: true }); ports[0].close(); }) };
  const registration = { active, update: vi.fn(async () => {}), pushManager: { getSubscription: vi.fn(async () => subscription), subscribe: vi.fn(async () => subscription) } };
  const serviceWorker = { register: vi.fn(async () => registration), getRegistration: vi.fn(async () => registration), ready: Promise.resolve(registration) };
  vi.stubGlobal('navigator', { serviceWorker });
  return { subscription, registration, serviceWorker, active };
}
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); localStorage.clear(); });
it('계정 변경은 이전 구독을 로컬 해제하며 새 actor로 DELETE하지 않는다', async () => {
  const b = browser(); localStorage.setItem('team4-notification-push-owner', A);
  await reconcilePush(B);
  expect(b.subscription.unsubscribe).toHaveBeenCalledOnce(); expect(deletePushSubscription).not.toHaveBeenCalled();
  expect(localStorage.getItem('team4-notification-push-owner')).toBeNull();
  expect(b.active.postMessage.mock.calls[0][0]).toEqual({ type: 'NOTIFICATION_OWNER', id: null });
});
it('로그아웃 정리는 현재 소유자 DELETE와 로컬 해제를 모두 수행한다', async () => {
  const b = browser(); localStorage.setItem('team4-notification-push-owner', A);
  await clearPush(A);
  expect(deletePushSubscription).toHaveBeenCalledWith(A, b.subscription.endpoint);
  expect(b.subscription.unsubscribe).toHaveBeenCalledOnce();
});
it('DELETE 실패를 숨기지 않으며 로컬 구독/소유자는 먼저 해제한다', async () => {
  const b = browser(); localStorage.setItem('team4-notification-push-owner', A);
  vi.mocked(deletePushSubscription).mockRejectedValueOnce(new Error('API unavailable'));
  await expect(clearPush(A)).rejects.toThrow('API unavailable');
  expect(b.subscription.unsubscribe).toHaveBeenCalledOnce(); expect(localStorage.getItem('team4-notification-push-owner')).toBeNull();
});
it('명시 켜기는 실제 구독을 PUT한 후에만 소유자 표시를 저장한다', async () => {
  const b = browser(); await enablePush(A, KEY);
  expect(b.serviceWorker.register).toHaveBeenCalledWith('/sw.js', { scope: '/', updateViaCache: 'none' });
  expect(savePushSubscription).toHaveBeenCalledWith(A, b.subscription.toJSON(), undefined);
  expect(localStorage.getItem('team4-notification-push-owner')).toBe(A);
});
it('취소된 이전 화면의 켜기 작업은 구독하거나 등록하지 않는다', async () => {
  const b = browser(); const controller = new AbortController(); controller.abort();
  await expect(enablePush(A, KEY, controller.signal)).rejects.toThrow();
  expect(b.serviceWorker.register).not.toHaveBeenCalled(); expect(savePushSubscription).not.toHaveBeenCalled();
});

it('저장소 접근 차단도 로컬 해제와 SW 정리를 막지 않고 실패를 알린다', async () => {
  const b = browser();
  const read = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError'); });
  const remove = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError'); });
  try {
    await expect(clearPush(A)).rejects.toThrow(/브라우저 저장소/);
    expect(b.subscription.unsubscribe).toHaveBeenCalledOnce();
    expect(b.active.postMessage.mock.calls[0][0]).toEqual({ type: 'NOTIFICATION_OWNER', id: null });
    expect(deletePushSubscription).not.toHaveBeenCalled();
    await expect(enablePush(A, KEY)).rejects.toThrow(/브라우저 저장소/);
    expect(b.serviceWorker.register).not.toHaveBeenCalled();
  } finally { read.mockRestore(); remove.mockRestore(); }
});
it('소유자 표시가 없어도 알려지지 않은 기존 구독을 보수적으로 해제한다', async () => {
  const b = browser(); await reconcilePush(B);
  expect(b.subscription.unsubscribe).toHaveBeenCalledOnce();
  expect(deletePushSubscription).not.toHaveBeenCalled();
});
