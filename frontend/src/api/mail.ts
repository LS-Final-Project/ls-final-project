import type { paths } from './generated/platform';
import { ApiError, getApiMode, request, sessionCsrfToken } from './client';

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

export type ReplyReview = paths['/api/v1/platform/mail/reply-reviews']['get']['responses'][200]['content']['application/json']['data']['items'][number];
export type MailTriage = paths['/api/v1/platform/mail/messages/{message_id}/triage']['get']['responses'][200]['content']['application/json']['data'];

const REASONS: ReplyReview['reason'][] = ['AMBIGUOUS', 'UNMATCHED_REPLY', 'CONFLICT', 'TRIAGE_DISAGREES'];
const isReview = (value: unknown): value is ReplyReview => {
  const item = value as Record<string, unknown> | null;
  return typeof item === 'object' && item !== null && typeof item.message_id === 'string' && typeof item.received_at === 'string'
    && REASONS.includes(item.reason as ReplyReview['reason']) && Array.isArray(item.candidates)
    && item.candidates.every(c => typeof c === 'object' && c !== null && typeof (c as Record<string, unknown>).business_ref === 'string');
};
const TYPES = ['NEW_RFP', 'EXISTING_CHANGE', 'CANDIDATE_REPLY', 'CS', 'OTHER'];
const isTriage = (value: unknown): value is MailTriage => {
  const item = value as Record<string, unknown> | null;
  return typeof item === 'object' && item !== null && typeof item.message_id === 'string' && typeof item.version === 'number'
    && TYPES.includes(item.mail_type as string) && typeof item.needs_review === 'boolean' && Array.isArray(item.evidence);
};

const reviewSample = (): { data: { items: ReplyReview[] } } => ({ data: { items: [
  { message_id: '44444444-4444-4444-8444-444444444444', received_at: '2026-10-29T08:41:00Z', created_at: '2026-10-29T08:42:00Z', reason: 'AMBIGUOUS',
    candidates: [{ owner_service: 'cs', business_ref: 'SYNTHETIC-CS-0001', method: 'HEADER' }, { owner_service: 'cs', business_ref: 'SYNTHETIC-CS-0002', method: 'THREAD' }] },
  { message_id: '55555555-5555-4555-8555-555555555555', received_at: '2026-10-28T08:50:00Z', created_at: '2026-10-28T08:51:00Z', reason: 'UNMATCHED_REPLY', candidates: [] },
] } });
const triageSample = (messageId: string): { data: MailTriage } => ({ data: {
  message_id: messageId, version: 2, latest_version: 2, is_latest: true, mail_type: 'CS', needs_review: true, attachments_pending: false,
  evidence: [{ source: 'subject', start: 0, end: 3 }, { source: 'reply_header', start: null, end: null }], link_suggestion: 'EXISTING_THREAD',
  provenance_kind: 'ai_draft', reason: null, created_at: '2026-10-29T08:42:00Z',
} });

/**
 * 사람이 연결 대상을 정해야 하는 고객 답장 목록을 읽는다(읽기 전용).
 * 이 API는 보낸 사람·제목·첨부를 주지 않는다(본문은 저장하지 않는다).
 */
export async function listReplyReviews(signal?: AbortSignal): Promise<ReplyReview[]> {
  const body = await request<unknown>('/api/v1/platform/mail/reply-reviews', { signal, mockFallback: reviewSample });
  const items = (body as { data?: { items?: unknown } } | null)?.data?.items;
  if (!Array.isArray(items) || !items.every(isReview)) throw new ApiError(502, '검토 대기 메일 목록을 확인할 수 없습니다.');
  return items;
}

/** 메일 한 통의 최신 자동 분류를 읽는다. */
export async function getMailTriage(messageId: string, signal?: AbortSignal): Promise<MailTriage> {
  const body = await request<unknown>(`/api/v1/platform/mail/messages/${encodeURIComponent(messageId)}/triage`, { signal, mockFallback: () => triageSample(messageId) });
  const data = (body as { data?: unknown } | null)?.data;
  if (!isTriage(data)) throw new ApiError(502, '자동 분류를 확인할 수 없습니다.');
  return data;
}

export type Reclassification = paths['/api/v1/platform/mail/messages/{message_id}/triage']['post']['requestBody']['content']['application/json'];
export const isTriageVersionConflict = (error: unknown) => error instanceof ApiError && error.status === 409;

/**
 * 사람이 메일 분류를 바꿔 새 판본으로 남긴다. 읽은 판본(version)이 최신이 아니면 서버가 409로 거부한다.
 * 서버 상태를 바꾸므로 개발 모드의 실제 API에서만 연다(모의 응답으로 성공한 척하지 않는다).
 */
export async function reclassifyMail(messageId: string, body: Reclassification, signal?: AbortSignal): Promise<MailTriage> {
  if (!import.meta.env.DEV || getApiMode() !== 'live') throw new ApiError(403, '실제 API 개발 모드에서만 실행할 수 있습니다.');
  const token = sessionCsrfToken();
  if (!token) throw new ApiError(403, '세션 확인 정보가 없습니다. 세션을 다시 확인하세요.');
  const reply = await request<unknown>(`/api/v1/platform/mail/messages/${encodeURIComponent(messageId)}/triage`, { method: 'POST', signal, headers: { 'X-CSRF-Token': token }, body: JSON.stringify(body) });
  const data = (reply as { data?: unknown } | null)?.data;
  if (!isTriage(data)) throw new ApiError(502, '저장 결과를 확인할 수 없습니다. 최신 분류를 다시 읽어 확인하세요.');
  return data;
}

export type SentCopyCheck = paths['/api/v1/platform/mail/send-intents/{intent_id}/check-sent-copy']['post']['responses'][200]['content']['application/json']['data'];
export type ResendResult = paths['/api/v1/platform/mail/send-intents/{intent_id}/resend']['post']['responses'][200]['content']['application/json']['data'];

// 메일을 외부로 내거나 메일 서버를 확인하는 호출이라 개발 모드의 실제 API와 세션 확인 정보가 있을 때만 연다.
// 허용 조건(결과 불명만, 유예 시간, 사본 없음 확인, 승인자만)은 서버가 판단하고 어기면 4xx로 이유를 돌려준다.
function sendGuard(): string {
  if (!import.meta.env.DEV || getApiMode() !== 'live') throw new ApiError(403, '실제 API 개발 모드에서만 실행할 수 있습니다.');
  const token = sessionCsrfToken();
  if (!token) throw new ApiError(403, '세션 확인 정보가 없습니다. 세션을 다시 확인하세요.');
  return token;
}
const isStatus = (value: unknown): value is SendStatus => STATUSES.includes(value as SendStatus);
const intentPath = (intentId: string, action: string) => `/api/v1/platform/mail/send-intents/${encodeURIComponent(intentId)}/${action}`;

/** 보낸 편지함 사본을 대조해 결과 불명 판단 근거를 이력에 남긴다. 사본이 확인되면 sent_copy_confirmed가 true다. */
export async function checkSentCopy(intentId: string, signal?: AbortSignal): Promise<SentCopyCheck> {
  const token = sendGuard();
  const reply = await request<unknown>(intentPath(intentId, 'check-sent-copy'), { method: 'POST', signal, headers: { 'X-CSRF-Token': token } });
  const data = (reply as { data?: Record<string, unknown> } | null)?.data;
  if (!data || typeof data.sent_copy_confirmed !== 'boolean' || !isStatus(data.status)) throw new ApiError(502, '보낸 편지함 확인 결과를 확인할 수 없습니다.');
  return data as SentCopyCheck;
}

/** 결과 불명 발송을 사유와 함께 다시 보낸다. 서버가 전송 작업을 등록한다. */
export async function resendIntent(intentId: string, reason: string, signal?: AbortSignal): Promise<ResendResult> {
  const token = sendGuard();
  const reply = await request<unknown>(intentPath(intentId, 'resend'), { method: 'POST', signal, headers: { 'X-CSRF-Token': token }, body: JSON.stringify({ reason }) });
  const data = (reply as { data?: Record<string, unknown> } | null)?.data;
  if (!data || typeof data.task_registered !== 'boolean' || !isStatus(data.status)) throw new ApiError(502, '다시 보내기 결과를 확인할 수 없습니다. 발송 상태를 다시 조회해 확인하세요.');
  return data as ResendResult;
}
