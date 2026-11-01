import type { paths } from './generated/cs';
import type { components } from './generated/platform';
import { ApiError, getApiMode } from './client';

export type AnalysisAccepted = paths['/api/v1/cs/tickets/{ticket_id}/analysis']['post']['responses'][202]['content']['application/json']['data'];
export type AnalysisJob = components['schemas']['CsJobStatus'];
export type AnalysisAttempt = { actorId: string; ticketId: string; key: string };
export class AnalysisUncertain extends Error {
  constructor() { super('분석 접수 결과를 확인하지 못했습니다. 새 요청을 만들지 말고 같은 요청을 다시 확인하세요.'); }
}
export class AnalysisSessionChanged extends Error {
  constructor() { super('계정이 변경되었습니다. 이전 계정의 분석 요청을 보내지 않았습니다. 현재 세션을 다시 확인하세요.'); }
}
const states = ['QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED'];
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const deadline = (signal: AbortSignal) => AbortSignal.any([signal, AbortSignal.timeout(15000)]);
async function failure(response: Response) {
  const body = await response.json().catch(() => null);
  return new ApiError(response.status, body?.error?.message || `HTTP ${response.status}`, body?.meta?.request_id, body?.error?.code);
}
async function session(actorId: string, signal: AbortSignal) {
  const response = await fetch('/api/v1/platform/me', { credentials: 'same-origin', cache: 'no-store', signal: deadline(signal) });
  if (!response.ok) throw await failure(response);
  const identity = await response.json();
  signal.throwIfAborted();
  if (identity?.data?.id !== actorId) throw new AnalysisSessionChanged();
  if (!identity.data.roles?.includes('cs_agent')) throw new ApiError(403, 'C/S 담당자만 분석을 요청할 수 있습니다.');
  return response.headers.get('X-CSRF-Token');
}
export async function startAnalysis(attempt: AnalysisAttempt, signal: AbortSignal): Promise<AnalysisAccepted> {
  if (getApiMode() !== 'live') throw new ApiError(403, '모의 응답에서는 분석을 요청하지 않습니다.');
  const token = await session(attempt.actorId, signal);
  if (!token) throw new ApiError(403, '세션 보안 정보를 확인할 수 없습니다. 다시 로그인하세요.');
  let response: Response;
  try {
    response = await fetch(`/api/v1/cs/tickets/${encodeURIComponent(attempt.ticketId)}/analysis`, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: deadline(signal),
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token, 'Idempotency-Key': attempt.key },
    });
  } catch { throw new AnalysisUncertain(); }
  if (response.status >= 500 || response.status === 408) { await response.body?.cancel(); throw new AnalysisUncertain(); }
  if (!response.ok) throw await failure(response);
  let data: AnalysisAccepted;
  try { data = (await response.json()).data; } catch { throw new AnalysisUncertain(); }
  if (!data || !uuid.test(data.job_id) || data.status_url !== `/api/v1/platform/jobs/${data.job_id}` || !states.includes(data.status) || typeof data.created !== 'boolean') throw new AnalysisUncertain();
  return data;
}
export async function getAnalysisJob(actorId: string, ticketId: string, jobId: string, signal: AbortSignal): Promise<AnalysisJob> {
  await session(actorId, signal);
  const response = await fetch(`/api/v1/platform/jobs/${encodeURIComponent(jobId)}`, { credentials: 'same-origin', cache: 'no-store', signal: deadline(signal) });
  if (!response.ok) throw await failure(response);
  const data = (await response.json()).data as AnalysisJob;
  if (!data || data.job_id !== jobId || data.ticket_id !== ticketId || data.owner !== 'cs' || data.kind !== 'cs.analyze' || !states.includes(data.status)) throw new Error('요청한 문의의 분석 작업인지 확인할 수 없습니다.');
  return data;
}
