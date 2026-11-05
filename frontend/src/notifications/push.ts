import { deletePushSubscription, savePushSubscription, notificationSession, NotificationSessionChanged } from '../api/notifications';

const OWNER = 'team4-notification-push-owner';
const LOCK = 'team4-notification-push';
// No permission prompt is awaited under this lock. Acquisition is bounded; an
// already-started, non-abortable browser operation keeps its lock until settled.
async function exclusive(action: () => Promise<void>, signal?: AbortSignal, cleanup = false): Promise<void> {
  signal?.throwIfAborted();
  let locks: LockManager | undefined;
  try { locks = navigator.locks; } catch { /* unavailable storage context */ }
  if (!locks) {
    const state = ownerState();
    if (cleanup && state.available && !state.id && !await existingPushWorker()) return;
    throw new Error('안전한 OS 알림 조정을 지원하지 않습니다. 브라우저 사이트 알림 설정을 확인하세요.');
  }
  const wait = new AbortController();
  const timer = setTimeout(() => wait.abort(), 5000);
  let acquired = false;
  try {
    await locks.request(LOCK, { signal: signal ? AbortSignal.any([signal, wait.signal]) : wait.signal }, async () => {
      acquired = true; clearTimeout(timer); signal?.throwIfAborted();
      await action();
    });
  } catch (error) {
    if (!acquired && !signal?.aborted) throw new Error('다른 탭의 OS 알림 작업을 확인하지 못했습니다. 잠시 후 다시 시도하세요.');
    throw error;
  } finally { clearTimeout(timer); }
}
export const pushSupported = () => {
  try { return !!window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && !!navigator.locks; }
  catch { return false; }
};
function ownerState(): { id: string | null; available: boolean } {
  try { return { id: localStorage.getItem(OWNER), available: true }; }
  catch { return { id: null, available: false }; }
}
export const pushOwner = () => ownerState().id;
function forgetOwner(): boolean {
  try { localStorage.removeItem(OWNER); return true; } catch { return false; }
}
const storageFailure = () => new Error('브라우저 저장소를 확인하지 못했습니다. OS 알림은 사이트 알림 설정에서 확인하세요.');
const own = (registration: ServiceWorkerRegistration) => [registration.active, registration.waiting, registration.installing].some(worker => worker?.scriptURL === new URL('/sw.js', location.origin).href);
export async function existingPushWorker() {
  if (!('serviceWorker' in navigator)) return undefined;
  const registration = await navigator.serviceWorker.getRegistration('/');
  return registration && own(registration) ? registration : undefined;
}
async function ownerMessage(registration: ServiceWorkerRegistration, id: string | null) {
  const worker = registration.active;
  if (!worker) throw new Error('알림 서비스워커가 아직 준비되지 않았습니다.');
  await new Promise<void>((resolve, reject) => {
    const channel = new MessageChannel();
    const timer = setTimeout(() => { channel.port1.close(); reject(new Error('알림 계정 확인 시간이 초과했습니다.')); }, 5000);
    channel.port1.onmessage = event => { clearTimeout(timer); channel.port1.close(); event.data?.ok ? resolve() : reject(new Error('알림 계정을 확인하지 못했습니다.')); };
    worker.postMessage({ type: 'NOTIFICATION_OWNER', id }, [channel.port2]);
  });
}
export function publicKeyBytes(value: string): Uint8Array<ArrayBuffer> {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error('OS 알림 공개키가 올바르지 않습니다.');
  const raw = atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4));
  const bytes = Uint8Array.from(raw, letter => letter.charCodeAt(0));
  if (bytes.length !== 65 || bytes[0] !== 4) throw new Error('OS 알림 공개키가 올바르지 않습니다.');
  return bytes;
}
// Permission is supplied by the button handler; this module never requests it on mount.
export function enablePush(actorId: string, key: string, signal?: AbortSignal): Promise<void> {
  return exclusive(async () => {
    signal?.throwIfAborted();
    const bytes = publicKeyBytes(key);
    await notificationSession(actorId, signal);
    signal?.throwIfAborted();
    if (!ownerState().available) throw storageFailure();
    const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' });
    await registration.update();
    signal?.throwIfAborted();
    let timer: ReturnType<typeof setTimeout>;
    const ready = await Promise.race([navigator.serviceWorker.ready, new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error('서비스워커 준비 시간이 초과했습니다.')), 10000); })]).finally(() => clearTimeout(timer));
    signal?.throwIfAborted();
    if (!own(ready)) throw new Error('알림 서비스워커의 범위를 확인하지 못했습니다.');
    const priorOwner = pushOwner();
    let subscription = await ready.pushManager.getSubscription();
    signal?.throwIfAborted();
    if (subscription && !priorOwner) throw new Error('기존 구독의 소유자를 확인하지 못했습니다. 사이트 알림 설정에서 해제 후 다시 시도하세요.');
    if (subscription && priorOwner !== actorId) {
      await sameSubscription(ready, subscription, priorOwner);
      await ownerMessage(ready, null);
      await sameSubscription(ready, subscription, priorOwner);
      if (!await subscription.unsubscribe()) throw new Error('이전 계정의 브라우저 구독을 해제하지 못했습니다.');
      subscription = null;
    }
    subscription ??= await ready.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes });
    try {
      const data = subscription.toJSON();
      if (!data.endpoint || !data.keys?.p256dh || !data.keys?.auth) throw new Error('브라우저 구독을 확인하지 못했습니다.');
      signal?.throwIfAborted();
      await savePushSubscription(actorId, { endpoint: data.endpoint, keys: { p256dh: data.keys.p256dh, auth: data.keys.auth } }, signal);
      signal?.throwIfAborted();
      await ownerMessage(ready, actorId);
      signal?.throwIfAborted();
      try { localStorage.setItem(OWNER, actorId); } catch { throw storageFailure(); }
    } catch (error) {
      // A failed request must never clean up a different tab's new generation.
      try {
        await sameSubscription(ready, subscription, priorOwner);
        await ownerMessage(ready, null).catch(() => {});
        await sameSubscription(ready, subscription, priorOwner);
        await subscription.unsubscribe();
        forgetOwner();
      } catch { /* ownership is uncertain: report original failure, do not guess */ }
      throw error;
    }
  }, signal);
}
async function sameSubscription(registration: ServiceWorkerRegistration, subscription: PushSubscription | null, expectedOwner: string | null) {
  const current = await registration.pushManager.getSubscription();
  const state = ownerState();
  if (!state.available || state.id !== expectedOwner || (current?.endpoint ?? null) !== (subscription?.endpoint ?? null)) {
    throw new Error('알림 구독이 변경되어 정리를 중단했습니다. 현재 계정을 다시 확인하세요.');
  }
}
// Local unsubscribe/owner clear happen even when the server is OFF or the delete is uncertain.
export function clearPush(actorId?: string, expectedSession: string | null | undefined = actorId): Promise<void> {
  return exclusive(async () => {
    await clearPushLocked(actorId, expectedSession);
  }, undefined, true);
}
// Caller holds the shared Web Lock; reconciliation must not acquire it twice.
async function clearPushLocked(actorId?: string, expectedSession: string | null | undefined = actorId): Promise<void> {
  if (expectedSession !== undefined) await notificationSession(expectedSession);
  const state = ownerState();
  if (!state.available) throw storageFailure();
  const registration = await existingPushWorker();
  let warning: unknown;
  if (!registration) {
    if (!forgetOwner() || warning) throw storageFailure();
    return;
  }
  const subscription = await registration.pushManager.getSubscription();
  if (subscription && !state.id) throw new Error('기존 구독의 소유자를 확인하지 못했습니다. 사이트 알림 설정에서 확인하세요.');
  if (actorId && state.id && state.id !== actorId) throw new NotificationSessionChanged();
  if (state.id) {
    await sameSubscription(registration, subscription, state.id);
    try { await ownerMessage(registration, null); } catch (error) { warning ??= error; }
  }
  await sameSubscription(registration, subscription, state.id);
  if (subscription) {
    const endpoint = subscription.endpoint;
    const removed = await subscription.unsubscribe();
    if (!removed) throw new Error('브라우저 구독 해제를 확인하지 못했습니다. 사이트 알림 설정을 확인하세요.');
    if (!forgetOwner()) warning ??= storageFailure();
    if (actorId && state.id === actorId) await deletePushSubscription(actorId, endpoint);
  } else if (!forgetOwner()) warning ??= storageFailure();
  if (warning) throw warning;
}
export function reconcilePush(actorId: string | null): Promise<void> {
  return exclusive(async () => {
    const previous = ownerState();
    if (!previous.available || !previous.id || previous.id !== actorId) await clearPushLocked(undefined, actorId);
  }, undefined, true);
}
