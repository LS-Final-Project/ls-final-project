import type { components, paths } from './generated/tft';
import type { components as Platform } from './generated/platform';
import type { ProjectDetail } from './tft';
import { ApiError } from './client';

export type Draft = components['schemas']['Draft'];
export type Sentence = components['schemas']['Sentence'];
export type DraftEdit = components['schemas']['DraftEdit'];
export type Review = components['schemas']['ReviewVersion'];
export type Accepted = components['schemas']['ExportAccepted'];
export type RewriteAccepted = components['schemas']['JobAccepted'];
export type Job = Platform['schemas']['TftJobStatus'];
type ReviewInput = components['schemas']['ReviewVersionCreate'];
type ExportInput = components['schemas']['ExportRequest'];
type DraftResponse = paths['/api/v1/tft/projects/{project_id}/draft']['get']['responses'][200]['content']['application/json'];
export type Command = { actorId: string; kind: 'review'; key: string; body: ReviewInput } | { actorId: string; kind: 'export'; key: string; body: ExportInput } | { actorId: string; kind: 'rewrite'; key: string; body: components['schemas']['DraftRewrite'] };
export const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const deadline = (signal?: AbortSignal) => signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000);
const base = (id: string) => `/api/v1/tft/projects/${encodeURIComponent(id)}/draft`;

export class SessionChanged extends Error {
  constructor() { super('계정이 변경되었습니다. 이전 입력을 전송하지 않았습니다. 현재 계정을 다시 확인하세요.'); }
}
export class UncertainWrite extends Error {
  constructor() { super('요청 결과를 확인하지 못했습니다. 같은 요청으로 결과를 다시 확인하세요.'); }
}
async function failure(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => null);
  return new ApiError(response.status, body?.error?.message || `HTTP ${response.status}`, body?.meta?.request_id, body?.error?.code);
}
async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', signal: deadline(signal) });
  if (!response.ok) throw await failure(response);
  return (await response.json()).data as T;
}
export const getDraft = (id: string, version?: number, signal?: AbortSignal) =>
  read<DraftResponse['data']>(`${base(id)}${version ? `?version=${version}` : ''}`, signal);
export const getDraftProject = (id: string, signal?: AbortSignal) =>
  read<ProjectDetail>(`/api/v1/tft/projects/${encodeURIComponent(id)}`, signal);

// 변경 직전에 현재 세션의 CSRF를 받는다. 이 단계가 실패하면 쓰기는 전송하지 않는다.
async function write<T>(path: string, method: string, body: unknown, actorId: string, key?: string, signal?: AbortSignal): Promise<T> {
  let session: Response;
  try { session = await fetch('/api/v1/platform/me', { credentials: 'same-origin', cache: 'no-store', signal: deadline(signal) }); }
  catch { throw new UncertainWrite(); }
  if (!session.ok) throw await failure(session);
  const csrf = session.headers.get('X-CSRF-Token');
  let identity: { data?: { id?: string } };
  try { identity = await session.json(); } catch { throw new UncertainWrite(); }
  if (identity?.data?.id !== actorId) throw new SessionChanged();
  signal?.throwIfAborted();
  if (!csrf) throw new ApiError(403, '세션 보안 정보를 확인할 수 없습니다. 다시 로그인하세요.');
  let response: Response;
  try {
    response = await fetch(path, {
      method, credentials: 'same-origin', cache: 'no-store', signal: deadline(signal),
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...(key ? { 'Idempotency-Key': key } : {}) },
      body: JSON.stringify(body),
    });
  } catch { throw new UncertainWrite(); }
  if (response.status >= 500) { await response.body?.cancel(); throw new UncertainWrite(); }
  if (!response.ok) throw await failure(response);
  try { return (await response.json()).data as T; }
  catch { throw new UncertainWrite(); }
}
export const editDraft = (id: string, actorId: string, body: DraftEdit, signal?: AbortSignal) => write<Draft>(base(id), 'PATCH', body, actorId, undefined, signal);
export async function sendCommand(id: string, command: Command, signal?: AbortSignal): Promise<Review | Accepted | RewriteAccepted> {
  const result = command.kind === 'review'
    ? await write<Review>(`${base(id)}/review-versions`, 'POST', command.body, command.actorId, command.key, signal)
    : command.kind === 'rewrite'
      ? await write<RewriteAccepted>(`${base(id)}/rewrite`, 'POST', command.body, command.actorId, command.key, signal)
      : await write<Accepted>(`${base(id)}/exports`, 'POST', command.body, command.actorId, command.key, signal);
  if (!result || typeof result !== 'object') throw new UncertainWrite();
  if (command.kind === 'review') {
    const review = result as Review;
    if (review.project_id !== id || review.draft_version !== command.body.version || !Number.isSafeInteger(review.review_version) || review.review_version < 1) throw new UncertainWrite();
  } else {
    const accepted = result as Accepted;
    if ((command.kind === 'export' && !uuid.test(accepted.export_id)) || !uuid.test(accepted.job_id) || accepted.status_url !== `/api/v1/platform/jobs/${accepted.job_id}` || !['QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED'].includes(accepted.status)) throw new UncertainWrite();
  }
  return result;
}
async function getDraftJob(project: string, jobId: string, kind: 'rfp.export' | 'rfp.draft_rewrite', signal?: AbortSignal): Promise<Job> {
  const job = await read<Job>(`/api/v1/platform/jobs/${encodeURIComponent(jobId)}`, signal);
  if (job.job_id !== jobId || job.project_id !== project || job.owner !== 'tft' || job.kind !== kind
    || !['QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED'].includes(job.status)) throw new Error('요청한 작업의 대상을 확인하지 못했습니다.');
  return job;
}
export const getExportJob = (project: string, jobId: string, signal?: AbortSignal) => getDraftJob(project, jobId, 'rfp.export', signal);
export const getRewriteJob = (project: string, jobId: string, signal?: AbortSignal) => getDraftJob(project, jobId, 'rfp.draft_rewrite', signal);
export async function downloadExport(project: string, exportId: string): Promise<Blob> {
  const response = await fetch(`${base(project)}/exports/${encodeURIComponent(exportId)}/file`, { credentials: 'same-origin', cache: 'no-store', signal: deadline() });
  if (!response.ok) throw await failure(response);
  if (response.headers.get('Content-Type')?.split(';')[0].trim() !== DOCX_MIME || !/^attachment(?:;|$)/i.test(response.headers.get('Content-Disposition') || '')) {
    await response.body?.cancel(); throw new Error('DOCX 첨부파일 응답을 확인하지 못했습니다.');
  }
  const blob = await response.blob();
  if (!blob.size || blob.size > 25 * 1024 * 1024) throw new Error('파일 크기를 확인할 수 없습니다.');
  return blob;
}
