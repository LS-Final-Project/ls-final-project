// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { clearPush, enablePush, reconcilePush, pushSupported } from './push';
import { deletePushSubscription, savePushSubscription, notificationSession, NotificationSessionChanged } from '../api/notifications';
vi.mock('../api/notifications', async importOriginal => ({ ...await importOriginal<typeof import('../api/notifications')>(), deletePushSubscription: vi.fn(), savePushSubscription: vi.fn(), notificationSession: vi.fn(async () => 'csrf') }));
const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
// Synthetic structure for fake browser tests only; never a runtime fallback or real VAPID key.
const KEY = btoa(String.fromCharCode(4, ...new Array(64).fill(1))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
function browser() {
  let created = false;
  const subscription = { endpoint: 'https://fcm.googleapis.com/fcm/send/test-only', unsubscribe: vi.fn(async () => { created = false; return true; }), toJSON: () => ({ endpoint: 'https://fcm.googleapis.com/fcm/send/test-only', keys: { p256dh: 'test-only', auth: 'test-only' } }) };
  const active = { scriptURL: new URL('/sw.js', location.origin).href, postMessage: vi.fn((_message, ports) => { ports[0].postMessage({ ok: true }); ports[0].close(); }) };
  const registration = { active, update: vi.fn(async () => {}), pushManager: { getSubscription: vi.fn(async () => created || localStorage.getItem('team4-notification-push-owner') ? subscription : null), subscribe: vi.fn(async () => { created = true; return subscription; }) } };
  const serviceWorker = { register: vi.fn(async () => registration), getRegistration: vi.fn(async () => registration), ready: Promise.resolve(registration) };
  const locks = { request: vi.fn(async (_name, _options, callback) => callback()) };
  vi.stubGlobal('navigator', { serviceWorker, locks });
  return { subscription, registration, serviceWorker, active, locks };
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); localStorage.clear(); });
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

it('저장소 접근 차단은 소유자를 추정 삭제하지 않고 실패를 알린다', async () => {
  const b = browser();
  const read = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError'); });
  const remove = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError'); });
  try {
    await expect(clearPush(A)).rejects.toThrow(/브라우저 저장소/);
    expect(b.subscription.unsubscribe).not.toHaveBeenCalled();
    expect(b.active.postMessage).not.toHaveBeenCalled();
    expect(deletePushSubscription).not.toHaveBeenCalled();
    await expect(enablePush(A, KEY)).rejects.toThrow(/브라우저 저장소/);
    expect(b.serviceWorker.register).not.toHaveBeenCalled();
  } finally { read.mockRestore(); remove.mockRestore(); }
});
it('소유자 표시가 없는 기존 구독을 추정 삭제하지 않는다', async () => {
  const b = browser(); b.registration.pushManager.getSubscription.mockResolvedValue(b.subscription);
  await expect(reconcilePush(B)).rejects.toThrow(/소유자/);
  expect(b.subscription.unsubscribe).not.toHaveBeenCalled();
  expect(deletePushSubscription).not.toHaveBeenCalled();
});

it.each(['', 'not-a-valid-key', btoa(String.fromCharCode(1, ...new Array(64).fill(1)))])('잘못된 공개키는 브라우저 구독에 사용하지 않는다', async key => {
  const b = browser();
  await expect(enablePush(A, key)).rejects.toThrow(/공개키/);
  expect(b.serviceWorker.register).not.toHaveBeenCalled(); expect(savePushSubscription).not.toHaveBeenCalled();
});

it('켜기·끄기·계정 정리는 동일 탭간 락과 획득 후 actor 검증을 사용한다', async () => {
  const b = browser(); await enablePush(A, KEY); await clearPush(A); await reconcilePush(B);
  expect(b.locks.request.mock.calls.map(call => call[0])).toEqual(new Array(3).fill('team4-notification-push'));
  expect(notificationSession).toHaveBeenCalledWith(A, undefined); expect(notificationSession).toHaveBeenCalledWith(B);
});
it('Web Locks 미지원이면 켜기 차단, 기존 구독 추정 삭제도 하지 않는다', async () => {
  const b = browser(); vi.stubGlobal('navigator', { serviceWorker: b.serviceWorker });
  expect(pushSupported()).toBe(false); await expect(enablePush(A, KEY)).rejects.toThrow(/지원/);
  localStorage.setItem('team4-notification-push-owner', A); await expect(clearPush(A)).rejects.toThrow(/지원/);
  expect(b.subscription.unsubscribe).not.toHaveBeenCalled(); expect(b.serviceWorker.register).not.toHaveBeenCalled();
});
it('락 대기 5초 초과는 구독을 건드리지 않고 정리 실패를 알린다', async () => {
  vi.useFakeTimers(); const b = browser(); localStorage.setItem('team4-notification-push-owner', A);
  b.locks.request.mockImplementation(async (_name, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
  }));
  const result = expect(clearPush(A)).rejects.toThrow(/다른 탭/); await vi.advanceTimersByTimeAsync(5000); await result;
  expect(b.subscription.unsubscribe).not.toHaveBeenCalled(); expect(notificationSession).not.toHaveBeenCalled();
});
it('락을 기다리는 화면 취소는 뒤늦은 구독 변경을 시작하지 않는다', async () => {
  const b = browser(); const controller = new AbortController();
  b.locks.request.mockImplementation(async (_name, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
  }));
  const result = expect(enablePush(A, KEY, controller.signal)).rejects.toThrow(); controller.abort(); await result;
  expect(b.serviceWorker.register).not.toHaveBeenCalled();
});
it('락 획득 후 actor가 바뀌면 구독 변경 전에 중단한다', async () => {
  const b = browser(); vi.mocked(notificationSession).mockRejectedValueOnce(new NotificationSessionChanged());
  await expect(enablePush(A, KEY)).rejects.toThrow(NotificationSessionChanged);
  expect(b.serviceWorker.register).not.toHaveBeenCalled(); expect(b.subscription.unsubscribe).not.toHaveBeenCalled();
});
it('SW 응답 중 소유자/세대가 바뀌면 신규 구독을 삭제하지 않는다', async () => {
  const b = browser(); localStorage.setItem('team4-notification-push-owner', A);
  b.active.postMessage.mockImplementationOnce((_message, ports) => {
    localStorage.setItem('team4-notification-push-owner', B);
    b.registration.pushManager.getSubscription.mockResolvedValue({ ...b.subscription, endpoint: 'https://fcm.googleapis.com/fcm/send/new-generation' });
    ports[0].postMessage({ ok: true }); ports[0].close();
  });
  await expect(clearPush(A)).rejects.toThrow(/변경/);
  expect(b.subscription.unsubscribe).not.toHaveBeenCalled(); expect(deletePushSubscription).not.toHaveBeenCalled();
  expect(localStorage.getItem('team4-notification-push-owner')).toBe(B);
});

it('브라우저 작업은 대기 timeout으로 중도 해제하지 않고 완료까지 락을 보유한다', async () => {
  vi.useFakeTimers(); const b = browser(); let release!: () => void; let locked = false;
  const registration = new Promise<void>(resolve => { release = resolve; });
  b.serviceWorker.register.mockImplementationOnce(async () => { await registration; return b.registration; });
  b.locks.request.mockImplementation(async (_name, _options, action) => { locked = true; try { await action(); } finally { locked = false; } });
  const result = enablePush(A, KEY); await vi.advanceTimersByTimeAsync(6000);
  expect(locked).toBe(true); expect(savePushSubscription).not.toHaveBeenCalled();
  vi.useRealTimers(); release(); await result; expect(locked).toBe(false);
});
it('켜기 실패 정리도 변경된 소유자/세대를 추정 삭제하지 않는다', async () => {
  const b = browser();
  vi.mocked(savePushSubscription).mockImplementationOnce(async () => {
    localStorage.setItem('team4-notification-push-owner', B);
    b.registration.pushManager.getSubscription.mockResolvedValue({ ...b.subscription, endpoint: 'https://fcm.googleapis.com/fcm/send/new-generation' });
    throw new NotificationSessionChanged();
  });
  await expect(enablePush(A, KEY)).rejects.toThrow(NotificationSessionChanged);
  expect(b.subscription.unsubscribe).not.toHaveBeenCalled(); expect(b.active.postMessage).not.toHaveBeenCalled();
  expect(localStorage.getItem('team4-notification-push-owner')).toBe(B);
});
