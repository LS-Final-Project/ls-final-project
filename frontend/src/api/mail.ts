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

const REASONS: ReplyReview['reason'][] = ['AMBIGUOUS', 'UNMATCHED_REPLY', 'CONFLICT', 'TRIAGE_DISAGREES', 'ADDRESS_MATCH'];
const isReview = (value: unknown): value is ReplyReview => {
  const item = value as Record<string, unknown> | null;
  return typeof item === 'object' && item !== null && typeof item.message_id === 'string' && typeof item.received_at === 'string'
    && REASONS.includes(item.reason as ReplyReview['reason']) && Array.isArray(item.candidates)
    && (item.from_address == null || typeof item.from_address === 'string') && (item.subject == null || typeof item.subject === 'string') && (item.has_attachments == null || typeof item.has_attachments === 'boolean')
    && item.candidates.every(c => typeof c === 'object' && c !== null && typeof (c as Record<string, unknown>).business_ref === 'string');
};
const TYPES = ['NEW_RFP', 'EXISTING_CHANGE', 'CANDIDATE_REPLY', 'CS', 'OTHER'];
const isTriage = (value: unknown): value is MailTriage => {
  const item = value as Record<string, unknown> | null;
  return typeof item === 'object' && item !== null && typeof item.message_id === 'string' && typeof item.version === 'number'
    && TYPES.includes(item.mail_type as string) && typeof item.needs_review === 'boolean' && Array.isArray(item.evidence);
};

const reviewSample = (): { data: { items: ReplyReview[] } } => ({ data: { items: [
  { message_id: '44444444-4444-4444-8444-444444444444', received_at: '2026-10-29T08:41:00Z', created_at: '2026-10-29T08:42:00Z', reason: 'AMBIGUOUS', from_address: 'buyer@synthetic.example', subject: 'Re: 합성 정보 요청 예시', has_attachments: true,
    candidates: [{ owner_service: 'cs', business_ref: 'cs_ticket:11111111-1111-4111-8111-111111111101', method: 'HEADER' }, { owner_service: 'cs', business_ref: 'cs_ticket:11111111-1111-4111-8111-111111111102', method: 'THREAD' }] },
  { message_id: '55555555-5555-4555-8555-555555555555', received_at: '2026-10-28T08:50:00Z', created_at: '2026-10-28T08:51:00Z', reason: 'UNMATCHED_REPLY', from_address: null, subject: null, has_attachments: false, candidates: [] },
] } });
const triageSample = (messageId: string): { data: MailTriage } => ({ data: {
  message_id: messageId, version: 2, latest_version: 2, is_latest: true, mail_type: 'CS', needs_review: true, attachments_pending: false,
  evidence: [{ source: 'subject', start: 0, end: 3 }, { source: 'reply_header', start: null, end: null }], link_suggestion: 'EXISTING_THREAD',
  provenance_kind: 'ai_draft', reason: null, created_at: '2026-10-29T08:42:00Z',
} });

export type ReviewCandidates = paths['/api/v1/platform/mail/reply-reviews/{message_id}/candidates']['get']['responses'][200]['content']['application/json']['data'];
export type ReviewCandidate = ReviewCandidates['items'][number];
const candidateSample = (messageId: string): { data: ReviewCandidates } => ({ data: { message_id: messageId, enriched: true, items: messageId.startsWith('4444') ? [
  { owner_service: 'cs', business_ref: 'cs_ticket:11111111-1111-4111-8111-111111111101', method: 'HEADER', evidence: ['WAITING_CUSTOMER', 'PRODUCT_MATCH'],
    ticket: { external_id: 'CS-합성-0001', subject: '합성 차단기 트립 문의', state: 'WAITING_CUSTOMER', customer_external_id: 'CUST-0021', product_code: 'UPS-LSU-10K' } },
  { owner_service: 'cs', business_ref: 'cs_ticket:11111111-1111-4111-8111-111111111102', method: 'THREAD', evidence: [],
    ticket: { external_id: 'CS-합성-0002', subject: '합성 설치 문의', state: 'IN_REVIEW', customer_external_id: 'CUST-0021', product_code: 'ELCB-EBN-203c' } },
  { owner_service: 'cs', business_ref: 'cs_ticket:11111111-1111-4111-8111-111111111103', method: 'CONTACT', evidence: ['CUSTOMER_CONTACT'],
    ticket: { external_id: 'CS-합성-0003', subject: '합성 같은 고객의 다른 문의', state: 'IN_REVIEW', customer_external_id: 'CUST-0021', product_code: 'UPS-LSU-10K' } },
] : [] } });
const isCandidates = (value: unknown): value is ReviewCandidates => {
  const data = value as Record<string, unknown> | null;
  return typeof data === 'object' && data !== null && typeof data.enriched === 'boolean' && Array.isArray(data.items)
    && data.items.every(c => typeof c === 'object' && c !== null && typeof (c as Record<string, unknown>).business_ref === 'string' && Array.isArray((c as Record<string, unknown>).evidence));
};
/**
 * 검토 메일의 후보와 근거를 읽는다(KAN-235, 읽기 전용). 플랫폼 후보(헤더·대화 묶음·주고받은 주소)에 고객 연락처로 찾은
 * 같은 고객의 진행 중 문의가 더해지고, 후보마다 문의 정보와 근거가 붙는다. 업무 서버를 부르지 못하면 enriched=false다.
 */
export async function getReplyCandidates(messageId: string, signal?: AbortSignal): Promise<ReviewCandidates> {
  const body = await request<unknown>(`/api/v1/platform/mail/reply-reviews/${encodeURIComponent(messageId)}/candidates`, { signal, mockFallback: () => candidateSample(messageId) });
  const data = (body as { data?: unknown } | null)?.data;
  if (!isCandidates(data)) throw new ApiError(502, '연결 후보를 확인할 수 없습니다.');
  return data;
}

/**
 * 사람이 연결 대상을 정해야 하는 고객 답장 목록을 읽는다(읽기 전용).
 * 보낸 사람·제목·첨부 여부는 서버가 줄 때만 있다(없으면 null). 본문은 저장하지 않으므로 첨부는 있음 여부만이다.
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
 * 서버 상태를 바꾸므로 실제 API 모드에서만 연다(모의 응답으로 성공한 척하지 않는다. 운영 빌드는 항상 실제 API다).
 */
export async function reclassifyMail(messageId: string, body: Reclassification, signal?: AbortSignal): Promise<MailTriage> {
  if (getApiMode() !== 'live') throw new ApiError(403, '실제 API 모드에서만 실행할 수 있습니다. 모의 응답 모드에서는 실행하지 않습니다.');
  const token = sessionCsrfToken();
  if (!token) throw new ApiError(403, '세션 확인 정보가 없습니다. 세션을 다시 확인하세요.');
  const reply = await request<unknown>(`/api/v1/platform/mail/messages/${encodeURIComponent(messageId)}/triage`, { method: 'POST', signal, headers: { 'X-CSRF-Token': token }, body: JSON.stringify(body) });
  const data = (reply as { data?: unknown } | null)?.data;
  if (!isTriage(data)) throw new ApiError(502, '저장 결과를 확인할 수 없습니다. 최신 분류를 다시 읽어 확인하세요.');
  return data;
}

export type SentCopyCheck = paths['/api/v1/platform/mail/send-intents/{intent_id}/check-sent-copy']['post']['responses'][200]['content']['application/json']['data'];
export type ResendResult = paths['/api/v1/platform/mail/send-intents/{intent_id}/resend']['post']['responses'][200]['content']['application/json']['data'];

// 메일을 외부로 내거나 메일 서버를 확인하는 호출이라 실제 API 모드와 세션 확인 정보가 있을 때만 연다(운영 빌드는 항상 실제 API).
// 허용 조건(결과 불명만, 유예 시간, 사본 없음 확인, 승인자만)은 서버가 판단하고 어기면 4xx로 이유를 돌려준다.
function sendGuard(): string {
  if (getApiMode() !== 'live') throw new ApiError(403, '실제 API 모드에서만 실행할 수 있습니다. 모의 응답 모드에서는 실행하지 않습니다.');
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

export type LinkBody = paths['/api/v1/platform/mail/reply-reviews/{message_id}/link']['post']['requestBody']['content']['application/json'];
export type RegisterBody = paths['/api/v1/platform/mail/messages/{message_id}/register-ticket']['post']['requestBody']['content']['application/json'];
export type ReplyResult = paths['/api/v1/platform/mail/reply-reviews/{message_id}/link']['post']['responses'][200]['content']['application/json']['data'];
export type RegisteredTicket = paths['/api/v1/platform/mail/messages/{message_id}/register-ticket']['post']['responses'][201]['content']['application/json']['data'];

const OUTCOMES = ['LINKED', 'REVIEW', 'NONE'];
async function decide(messageId: string, action: 'link' | 'dismiss', body: object, signal?: AbortSignal): Promise<ReplyResult> {
  const token = sendGuard();
  const reply = await request<unknown>(`/api/v1/platform/mail/reply-reviews/${encodeURIComponent(messageId)}/${action}`, { method: 'POST', signal, headers: { 'X-CSRF-Token': token }, body: JSON.stringify(body) });
  const data = (reply as { data?: Record<string, unknown> } | null)?.data;
  if (!data || typeof data.message_id !== 'string' || !OUTCOMES.includes(data.outcome as string)) throw new ApiError(502, '처리 결과를 확인할 수 없습니다. 검토 목록을 다시 조회해 확인하세요.');
  return data as ReplyResult;
}
/** 고객 답장을 업무에 연결한다(사람이 정함, 근거 HUMAN). 후보 밖 C/S 문의도 번호로 찾아 연결할 수 있고, 서버가 문의가 있는지와 근거를 확인해 남긴다(KAN-235). */
export const linkReply = (messageId: string, body: LinkBody, signal?: AbortSignal) => decide(messageId, 'link', body, signal);
/** 업무 회신이 아니라고 닫는다. 사유(note)가 기록에 남는다. */
export const dismissReply = (messageId: string, note: string, signal?: AbortSignal) => decide(messageId, 'dismiss', { note }, signal);

/** 받은 메일을 C/S 문의로 접수한다(내 사서함의 받은편지함 메일만). 같은 메일을 다시 접수하면 created=false로 기존 문의를 돌려준다. */
export async function registerTicket(messageId: string, body: RegisterBody, signal?: AbortSignal): Promise<RegisteredTicket> {
  const token = sendGuard();
  const reply = await request<unknown>(`/api/v1/platform/mail/messages/${encodeURIComponent(messageId)}/register-ticket`, { method: 'POST', signal, headers: { 'X-CSRF-Token': token }, body: JSON.stringify(body) });
  const data = (reply as { data?: Record<string, unknown> } | null)?.data;
  if (!data || typeof data.ticket_id !== 'string' || typeof data.created !== 'boolean') throw new ApiError(502, '문의 접수 결과를 확인할 수 없습니다. 문의 조회에서 확인하세요.');
  return data as RegisteredTicket;
}

// KAN-73 RFP로 보이는 메일: 후보 목록, 첨부 받기, 확인 후 프로젝트 연결.
export type RfpCandidate = paths['/api/v1/platform/mail/rfp-candidates']['get']['responses'][200]['content']['application/json']['data']['items'][number];
export type LinkedRfpProject = paths['/api/v1/platform/mail/messages/{message_id}/rfp-project']['post']['responses'][201]['content']['application/json']['data'];
const RFP_FILE_TYPES = new Set(['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']);

/** 내 메일함에서 아직 프로젝트로 등록하지 않은 RFP 후보 메일. 모의 모드에서는 빈 목록이다. */
export async function listRfpCandidates(signal?: AbortSignal): Promise<RfpCandidate[]> {
  const body = await request<unknown>('/api/v1/platform/mail/rfp-candidates', { signal, mockFallback: () => ({ data: { items: [] } }) });
  const items = (body as { data?: { items?: unknown } } | null)?.data?.items;
  if (!Array.isArray(items)) throw new ApiError(502, 'RFP 후보 메일 목록을 확인할 수 없습니다.');
  return items as RfpCandidate[];
}

/** 후보 메일의 DOCX·PDF 첨부를 File로 받는다(프로젝트 문서로 올릴 때 쓴다). */
export async function downloadMailAttachment(messageId: string, attachment: RfpCandidate['attachments'][number]): Promise<File> {
  const response = await fetch(`/api/v1/platform/mail/messages/${encodeURIComponent(messageId)}/attachments/${encodeURIComponent(attachment.id)}`, { credentials: 'same-origin', cache: 'no-store' });
  const type = response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() ?? '';
  if (!response.ok || !RFP_FILE_TYPES.has(type)) { await response.body?.cancel(); throw new ApiError(response.ok ? 502 : response.status, '첨부를 받지 못했습니다. DOCX·PDF 첨부인지 확인하세요.'); }
  const blob = await response.blob();
  if (!blob.size || blob.size > 25 * 1024 * 1024) throw new ApiError(422, '첨부 크기를 확인할 수 없습니다(25MB 이하).');
  return new File([blob], attachment.name || 'rfp', { type });
}

/** 방금 만든 RFP 프로젝트를 메일에 연결한다. 같은 프로젝트로 다시 불러도 한 번만 기록된다. */
export async function linkMailRfpProject(messageId: string, projectId: string): Promise<LinkedRfpProject> {
  if (getApiMode() !== 'live') throw new ApiError(403, '실제 API 모드에서만 실행할 수 있습니다. 모의 응답 모드에서는 실행하지 않습니다.');
  const token = sessionCsrfToken();
  if (!token) throw new ApiError(403, '세션 확인 정보가 없습니다. 세션을 다시 확인하세요.');
  const reply = await request<unknown>(`/api/v1/platform/mail/messages/${encodeURIComponent(messageId)}/rfp-project`, { method: 'POST', headers: { 'X-CSRF-Token': token }, body: JSON.stringify({ project_id: projectId }) });
  const data = (reply as { data?: LinkedRfpProject } | null)?.data;
  if (!data?.project_id) throw new ApiError(502, '연결 결과를 확인할 수 없습니다.');
  return data;
}

// KAN-237 문의로 보이는 새 메일: 고객 연락처 주소로 온, 아직 업무에 연결하지 않은 받은 메일과 본문 확인.
export type CsCandidate = paths['/api/v1/platform/mail/cs-candidates']['get']['responses'][200]['content']['application/json']['data']['items'][number];
export type MailContent = paths['/api/v1/platform/mail/messages/{message_id}/content']['get']['responses'][200]['content']['application/json']['data'];

/** 내 메일함에서 문의로 보이는 새 메일(cs_agent). 모의 모드에서는 빈 목록이다. */
export async function listCsCandidates(signal?: AbortSignal): Promise<CsCandidate[]> {
  const body = await request<unknown>('/api/v1/platform/mail/cs-candidates', { signal, mockFallback: () => ({ data: { items: [] } }) });
  const items = (body as { data?: { items?: unknown } } | null)?.data?.items;
  const valid = (c: unknown) => typeof (c as CsCandidate | null)?.message_id === 'string' && Array.isArray((c as CsCandidate).customer_external_ids);
  if (!Array.isArray(items) || !items.every(valid)) throw new ApiError(502, '문의로 보이는 메일 목록을 확인할 수 없습니다.');
  return items as CsCandidate[];
}

/** 받은 메일 한 통의 제목·본문(등록 전 확인용, 서버가 원문에서 꺼낸다). */
export async function getMailContent(messageId: string, signal?: AbortSignal): Promise<MailContent> {
  const body = await request<unknown>(`/api/v1/platform/mail/messages/${encodeURIComponent(messageId)}/content`, { signal });
  const data = (body as { data?: MailContent } | null)?.data;
  if (typeof data?.body !== 'string') throw new ApiError(502, '메일 본문을 확인할 수 없습니다.');
  return data;
}

// KAN-238 메일함의 받은 메일 전체 목록과 연결 상태(본문 없음). 본문은 getMailContent로 연다.
export type InboxMessage = paths['/api/v1/platform/mail/messages']['get']['responses'][200]['content']['application/json']['data']['items'][number];

/** 내 메일함의 받은 메일(최근 순 최대 100통). 모의 모드에서는 빈 목록이다. */
export async function listMailMessages(signal?: AbortSignal): Promise<InboxMessage[]> {
  const body = await request<unknown>('/api/v1/platform/mail/messages', { signal, mockFallback: () => ({ data: { items: [] } }) });
  const items = (body as { data?: { items?: unknown } } | null)?.data?.items;
  const valid = (m: unknown) => typeof (m as InboxMessage | null)?.message_id === 'string' && typeof (m as InboxMessage).received_at === 'string';
  if (!Array.isArray(items) || !items.every(valid)) throw new ApiError(502, '받은 메일 목록을 확인할 수 없습니다.');
  return items as InboxMessage[];
}
