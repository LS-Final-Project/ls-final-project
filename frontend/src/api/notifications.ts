import type { components } from './generated/platform';
import { ApiError } from './client';

export type NotificationItem = components['schemas']['Notification'];
export type NotificationList = components['schemas']['NotificationList'];
export type SubscriptionInput = components['schemas']['Subscription'];
export class NotificationSessionChanged extends Error {
  constructor() { super('계정이 변경되어 요청을 보내지 않았습니다. 현재 계정을 다시 확인하세요.'); }
}
const uuid = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';
const safeLink = new RegExp(`^/(?:cs/tickets|tft/projects)/${uuid}$`);
export const notificationPath = (value: string): string | null => safeLink.test(value) ? value : null;
const signalFor = (signal?: AbortSignal) => signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000);
async function error(response: Response) {
  const body = await response.json().catch(() => null);
  return new ApiError(response.status, body?.error?.message || `HTTP ${response.status}`, body?.meta?.request_id, body?.error?.code);
}
async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', signal: signalFor(signal) });
  if (!response.ok) throw await error(response);
  return (await response.json()).data as T;
}
export async function unreadCount(signal?: AbortSignal) {
  const data = await read<components['schemas']['UnreadCount']>('/api/v1/platform/notifications/unread-count', signal);
  if (!data || !Number.isSafeInteger(data.count) || data.count < 0) throw new Error('안 읽은 알림 수를 확인하지 못했습니다.');
  return data;
}
export const listNotifications = (offset = 0, signal?: AbortSignal) => read<NotificationList>(`/api/v1/platform/notifications?limit=50&offset=${offset}`, signal);
export const pushPublicKey = (signal?: AbortSignal) => read<components['schemas']['PublicKey']>('/api/v1/platform/push/public-key', signal);

// 요청 소유자와 현재 세션을 비교한 뒤 변경한다.
export async function notificationMutation(actorId: string, path: string, method: 'POST' | 'PUT' | 'DELETE', body: unknown, signal?: AbortSignal): Promise<void> {
  const session = await fetch('/api/v1/platform/me', { credentials: 'same-origin', cache: 'no-store', signal: signalFor(signal) });
  if (!session.ok) throw await error(session);
  const csrf = session.headers.get('X-CSRF-Token');
  const current = await session.json();
  if (current?.data?.id !== actorId) throw new NotificationSessionChanged();
  signal?.throwIfAborted();
  if (!csrf) throw new ApiError(403, '세션 보안 정보를 확인할 수 없습니다. 다시 로그인하세요.');
  const response = await fetch(path, { method, credentials: 'same-origin', cache: 'no-store', signal: signalFor(signal), headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) });
  if (!response.ok) throw await error(response);
}
export const markNotificationRead = (actorId: string, id: string, signal?: AbortSignal) => notificationMutation(actorId, `/api/v1/platform/notifications/${encodeURIComponent(id)}/read`, 'POST', {}, signal);
export const savePushSubscription = (actorId: string, subscription: SubscriptionInput, signal?: AbortSignal) => notificationMutation(actorId, '/api/v1/platform/push/subscription', 'PUT', subscription, signal);
export const deletePushSubscription = (actorId: string, endpoint: string) => notificationMutation(actorId, '/api/v1/platform/push/subscription', 'DELETE', { endpoint });
