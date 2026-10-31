import type { paths } from './generated/platform';
import { ApiError, request } from './client';

export type SendIntent = paths['/api/v1/platform/mail/send-intents']['get']['responses'][200]['content']['application/json']['data']['items'][number];
export type SendStatus = SendIntent['status'];

const STATUSES: SendStatus[] = ['QUEUED', 'SUBMITTING', 'ACCEPTED', 'UNKNOWN', 'FAILED'];
const isIntent = (value: unknown): value is SendIntent => {
  const item = value as Record<string, unknown> | null;
  return typeof item === 'object' && item !== null && typeof item.intent_id === 'string' && typeof item.subject === 'string'
    && typeof item.business_ref === 'string' && STATUSES.includes(item.status as SendStatus) && typeof item.attempts === 'number' && typeof item.created_at === 'string';
};

// 모의 응답 모드용 합성 예시. 실제 발송 기록이 아니다.
const sample = (): { data: { items: SendIntent[] } } => ({ data: { items: [
  { intent_id: '11111111-1111-4111-8111-111111111111', business_ref: 'SYNTHETIC-0001', business_version: 1, subject: '합성 정보 요청 예시', status: 'ACCEPTED', error_code: null, attempts: 1, created_at: '2026-10-29T00:20:00Z', submitted_at: '2026-10-29T00:20:05Z', accepted_at: '2026-10-29T00:20:06Z', sent_copy_confirmed_at: null, resend_ready: false },
  { intent_id: '22222222-2222-4222-8222-222222222222', business_ref: 'SYNTHETIC-0002', business_version: 2, subject: '합성 답변 예시', status: 'UNKNOWN', error_code: 'SUBMIT_TIMEOUT', attempts: 2, created_at: '2026-10-29T00:10:00Z', submitted_at: '2026-10-29T00:12:00Z', accepted_at: null, sent_copy_confirmed_at: null, resend_ready: false },
  { intent_id: '33333333-3333-4333-8333-333333333333', business_ref: 'SYNTHETIC-0003', business_version: 1, subject: '합성 실패 예시', status: 'FAILED', error_code: 'RECIPIENT_REJECTED', attempts: 1, created_at: '2026-10-28T05:00:00Z', submitted_at: null, accepted_at: null, sent_copy_confirmed_at: null, resend_ready: true },
] } });

/**
 * 내가 만든 발송 의도 목록을 읽는다(읽기 전용).
 * Args: status를 주면 서버가 그 상태만 돌려준다.
 * Returns: 발송 의도 목록. 예상 밖 본문은 ApiError로 낸다.
 */
export async function listSendIntents(status: SendStatus | '', signal?: AbortSignal): Promise<SendIntent[]> {
  const query = status ? `?status=${status}` : '';
  const body = await request<unknown>(`/api/v1/platform/mail/send-intents${query}`, { signal, mockFallback: sample });
  const items = (body as { data?: { items?: unknown } } | null)?.data?.items;
  if (!Array.isArray(items) || !items.every(isIntent)) throw new ApiError(502, '발송 상태 목록을 확인할 수 없습니다.');
  return items;
}
