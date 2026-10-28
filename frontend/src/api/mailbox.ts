import type { paths } from './generated/platform';
import { ApiError, request, sessionCsrfToken } from './client';

const BASE = '/api/v1/platform/auth/mailbox';
// 서버 사서함 어댑터의 고정 authority. 응답이 다른 곳을 가리키면 이동하지 않는다.
const AUTHORITY = 'https://login.microsoftonline.com/consumers/';

export type MailboxConnection = paths['/api/v1/platform/auth/mailbox']['get']['responses'][200]['content']['application/json']['data']['items'][number];

const isConnection = (value: unknown): value is MailboxConnection => {
  const item = value as Record<string, unknown> | null;
  return typeof item === 'object' && item !== null && typeof item.mailbox_id === 'string' && (item.state === 'connected' || item.state === 'disconnected') && typeof item.revision === 'number' && typeof item.updated_at === 'string';
};

function csrf(): string {
  const token = sessionCsrfToken();
  if (!token) throw new ApiError(403, '세션 확인 후 다시 시도하세요.');
  return token;
}

/**
 * 현재 세션 소유자의 사서함 연결 목록을 읽는다.
 * Returns: 연결 목록. 사서함 연결이 꺼진 배포(404)면 null. 예상 밖 본문은 ApiError로 낸다.
 */
export async function listMailboxes(): Promise<MailboxConnection[] | null> {
  let body: unknown;
  try { body = await request<unknown>(BASE); }
  catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
  const items = (body as { data?: { items?: unknown } } | null)?.data?.items;
  if (!Array.isArray(items) || !items.every(isConnection)) throw new ApiError(502, '사서함 연결 목록을 확인할 수 없습니다.');
  return items;
}

/**
 * 사서함 연결을 시작하고 Microsoft 동의 주소를 받는다.
 * Returns: 고정 authority 아래의 동의 URL.
 */
export async function startMailboxLink(): Promise<string> {
  const body = await request<unknown>(BASE + '/start', { method: 'POST', headers: { 'X-CSRF-Token': csrf() } });
  const url = (body as { data?: { authorization_url?: unknown } } | null)?.data?.authorization_url;
  if (typeof url !== 'string' || !url.startsWith(AUTHORITY)) throw new ApiError(502, '사서함 연결 주소를 확인할 수 없습니다.');
  return url;
}

/**
 * 현재 세션 소유자의 사서함 연결 하나를 해제한다.
 * Args: mailboxId는 목록에서 받은 연결 ID다.
 * Returns: 해제가 서버에 커밋되면 끝나는 Promise.
 */
export async function disconnectMailbox(mailboxId: string): Promise<void> {
  await request<void>(`${BASE}/${encodeURIComponent(mailboxId)}/disconnect`, { method: 'POST', headers: { 'X-CSRF-Token': csrf() } });
}
