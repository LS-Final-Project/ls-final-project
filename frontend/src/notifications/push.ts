import { deletePushSubscription, savePushSubscription } from '../api/notifications';

const OWNER = 'team4-notification-push-owner';
let pending: Promise<unknown> = Promise.resolve();
function exclusive<T>(action: () => Promise<T>): Promise<T> { const result = pending.then(action); pending = result.catch(() => {}); return result; }
export const pushSupported = () => window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
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
  if (subscription && priorOwner !== actorId) { await ownerMessage(ready, null); if (!await subscription.unsubscribe()) throw new Error('이전 계정의 브라우저 구독을 해제하지 못했습니다.'); subscription = null; }
  subscription ??= await ready.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes });
  const data = subscription.toJSON();
  if (!data.endpoint || !data.keys?.p256dh || !data.keys?.auth) throw new Error('브라우저 구독을 확인하지 못했습니다.');
  try {
    signal?.throwIfAborted();
    await savePushSubscription(actorId, { endpoint: data.endpoint, keys: { p256dh: data.keys.p256dh, auth: data.keys.auth } }, signal);
    signal?.throwIfAborted();
    await ownerMessage(ready, actorId);
    signal?.throwIfAborted();
    try { localStorage.setItem(OWNER, actorId); } catch { throw storageFailure(); }
  } catch (error) {
    await ownerMessage(ready, null).catch(() => {});
    await subscription.unsubscribe().catch(() => false);
    forgetOwner();
    throw error;
  }
  });
}
// Local unsubscribe/owner clear happen even when the server is OFF or the delete is uncertain.
export function clearPush(actorId?: string): Promise<void> {
  return exclusive(async () => {
  const state = ownerState();
  const registration = await existingPushWorker();
  let warning: unknown = state.available ? undefined : storageFailure();
  if (!registration) {
    if (!forgetOwner() || warning) throw storageFailure();
    return;
  }
  try { await ownerMessage(registration, null); } catch (error) { warning ??= error; }
  const subscription = await registration.pushManager.getSubscription();
  if (subscription) {
    const endpoint = subscription.endpoint;
    const removed = await subscription.unsubscribe();
    if (!removed) throw new Error('브라우저 구독 해제를 확인하지 못했습니다. 사이트 알림 설정을 확인하세요.');
    if (!forgetOwner()) warning ??= storageFailure();
    if (actorId && state.id === actorId) await deletePushSubscription(actorId, endpoint);
  } else if (!forgetOwner()) warning ??= storageFailure();
  if (warning) throw warning;
  });
}
export async function reconcilePush(actorId: string | null): Promise<void> {
  const previous = ownerState();
  if (!previous.available || !previous.id || previous.id !== actorId) await clearPush();
}
