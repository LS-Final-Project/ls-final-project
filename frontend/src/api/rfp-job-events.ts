import { ApiError } from './client';
import type { components } from './generated/platform';

export type RfpJob = components['schemas']['TftJobStatus'];
export type RfpTarget = { jobId: string; projectId: string; actorId: string };
export type RfpProgress = {
  sequence: number; occurredAt: string; type: string;
  step?: string; documentVersionId?: string;
  counts?: { requirements: number; proposals: number }; needsReview?: boolean;
  focus?: RfpFocus;
};
/** 진행 화면 한 줄의 재료(KAN-232). 처리 상태에서 만든 절 번호·제목·위치 이름과 건수만 온다. */
export type RfpFocus = { kind: 'section' | 'found' | 'ocr' | 'compare'; label: string; n?: number };
export type RfpWatch = {
  connection: 'connecting' | 'connected' | 'reconnecting' | 'fallback' | 'stopped' | 'ended';
  job?: RfpJob; progress?: RfpProgress; checkedAt?: number; message?: string;
  activity?: RfpProgress[]; startedAt?: string; restoredCount?: number;
};
const states = new Set(['QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED']);
const kinds = new Set(['rfp.process', 'rfp.export', 'rfp.annotate', 'rfp.draft_rewrite', 'rfp.context']);
const types = new Set(['job.queued', 'job.started', 'job.progress', 'job.completed', 'job.failed']);
const steps = new Set(['parse', 'extract', 'search', 'compare', 'supplement', 'candidates', 'draft', 'export', 'annotate', 'context_map', 'context_link', 'context_validate']);
const focusKinds = new Set(['section', 'found', 'ocr', 'compare']);
const focusLabel = /^[0-9A-Za-z가-힣 .,()·/~-]{1,40}$/;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown, min = 0): v is number => Number.isSafeInteger(v) && (v as number) >= min;
const invalid = () => new ApiError(422, '작업 응답을 확인하지 못했습니다. 상태 조회로 다시 확인합니다.');
export const activeRfpJob = (status: string) => status === 'QUEUED' || status === 'RUNNING';
export const deniedRfpJob = (error: unknown) => error instanceof ApiError && [401, 403, 404].includes(error.status);

async function json(path: string, signal: AbortSignal): Promise<unknown> {
  signal.throwIfAborted();
  const r = await fetch(path, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) });
  if (!r.ok) { await r.body?.cancel(); throw new ApiError(r.status, '작업 상태를 조회하지 못했습니다.'); }
  const body: unknown = await r.json();
  if (!object(body)) throw invalid();
  return body.data;
}
export async function readRfpJob(target: RfpTarget, signal: AbortSignal): Promise<RfpJob> {
  const actor = await json('/api/v1/platform/me', signal);
  if (!object(actor) || actor.id !== target.actorId) throw new ApiError(403, '계정이 변경되었습니다. 다시 로그인 상태를 확인하세요.');
  const v = await json(`/api/v1/platform/jobs/${encodeURIComponent(target.jobId)}`, signal);
  if (!object(v) || v.job_id !== target.jobId || v.project_id !== target.projectId || v.owner !== 'tft'
    || typeof v.kind !== 'string' || !kinds.has(v.kind) || typeof v.status !== 'string' || !states.has(v.status)
    || !integer(v.version, 1) || !integer(v.last_event_sequence)) throw invalid();
  return v as RfpJob;
}

/** Validate the public event envelope; only explicit RFP fields reach the UI. */
export function parseRfpEvent(frame: string[], jobId: string): RfpProgress | undefined {
  const fields = frame.filter(line => !line.startsWith(':'));
  if (!fields.length) return;
  const single = (name: string) => { const found = fields.filter(line => line.startsWith(name + ':')); if (found.length !== 1) throw invalid(); return found[0].slice(name.length + 1).trim(); };
  const id = single('id'), type = single('event');
  let v: unknown;
  try { v = JSON.parse(fields.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')); } catch { throw invalid(); }
  if (!object(v) || v.job_id !== jobId || !integer(v.sequence, 1) || id !== String(v.sequence) || !types.has(type) || v.type !== type
    || typeof v.occurred_at !== 'string' || !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(v.occurred_at) || !Number.isFinite(Date.parse(v.occurred_at)) || !object(v.payload)) throw invalid();
  const p = v.payload;
  if (p.step != null && (typeof p.step !== 'string' || !steps.has(p.step))) throw invalid();
  if (p.document_version_id != null && (typeof p.document_version_id !== 'string' || !uuid.test(p.document_version_id))) throw invalid();
  if (p.needs_review != null && typeof p.needs_review !== 'boolean') throw invalid();
  if (p.state != null && (typeof p.state !== 'string' || !states.has(p.state))) throw invalid();
  if (p.counts != null && (!object(p.counts) || !integer(p.counts.requirements) || !integer(p.counts.proposals))) throw invalid();
  const f = p.focus;
  if (f != null && (!object(f) || typeof f.kind !== 'string' || !focusKinds.has(f.kind) || typeof f.label !== 'string' || !focusLabel.test(f.label) || (f.n != null && !integer(f.n)))) throw invalid();
  return { sequence: v.sequence, occurredAt: v.occurred_at, type,
    step: p.step == null ? undefined : p.step as string,
    documentVersionId: p.document_version_id == null ? undefined : p.document_version_id as string,
    counts: p.counts == null ? undefined : p.counts as RfpProgress['counts'], needsReview: p.needs_review == null ? undefined : p.needs_review as boolean,
    focus: f == null ? undefined : { kind: f.kind as RfpFocus['kind'], label: f.label as string, ...(f.n == null ? {} : { n: f.n as number }) } };
}

/** 단계는 최신 관측 하나씩, 진행 한 줄(focus)은 순서대로 쌓는다(최근 80건). */
export function addActivity(activity: RfpProgress[], p: RfpProgress): RfpProgress[] {
  if (p.focus) return [...activity, p].slice(-80);
  if (!p.step) return activity;
  return [...activity.filter(item => item.focus || item.step !== p.step), p];
}

class CursorExpired extends Error {}
class Gap extends Error {}
/** Read one connection, releasing it on terminal notification. Terminal state is always confirmed by GET. */
async function stream(target: RfpTarget, after: number, signal: AbortSignal, connected: () => void, event: (p: RfpProgress) => void): Promise<void> {
  const local = new AbortController();
  const combined = AbortSignal.any([signal, local.signal]);
  let timer = setTimeout(() => local.abort(), 15000);
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let cursor = after;
  try {
    const r = await fetch(`/api/v1/platform/jobs/${encodeURIComponent(target.jobId)}/events?after=${after}`, {
      credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: combined, headers: { Accept: 'text/event-stream' },
    });
    if (r.status === 204) return;
    if (r.status === 410) { await r.body?.cancel(); throw new CursorExpired(); }
    if (!r.ok) { await r.body?.cancel(); throw new ApiError(r.status, '실시간 상태 연결을 확인하지 못했습니다.'); }
    if (r.headers.get('Content-Type')?.split(';')[0].trim() !== 'text/event-stream' || !r.body) throw invalid();
    connected(); reader = r.body.getReader();
    const decoder = new TextDecoder('utf-8', { fatal: true });
    let buffer = '', lines: string[] = [], size = 0;
    while (!signal.aborted) {
      clearTimeout(timer); timer = setTimeout(() => local.abort(), 45000);
      const chunk = await reader.read();
      if (chunk.done) throw new TypeError('Stream interrupted');
      buffer += decoder.decode(chunk.value, { stream: true });
      let end: number;
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end).replace(/\r$/, ''); buffer = buffer.slice(end + 1);
        size += line.length;
        if (size > 65536) throw invalid();
        if (line) { lines.push(line); continue; }
        const p = parseRfpEvent(lines, target.jobId); lines = []; size = 0;
        if (!p || p.sequence <= cursor) continue; // replay/duplicate/out-of-order cannot overwrite newer state
        if (p.sequence !== cursor + 1) throw new Gap();
        signal.throwIfAborted(); cursor = p.sequence; event(p);
        if (p.type === 'job.completed' || p.type === 'job.failed') return;
      }
      if (buffer.length + size > 65536) throw invalid();
    }
  } finally {
    clearTimeout(timer); local.abort();
    await reader?.cancel().catch(() => undefined); reader?.releaseLock();
  }
}
function delay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    signal.addEventListener('abort', abort, { once: true });
  });
}

/** 끝난 작업의 저장된 진행 이벤트를 처음부터 재생한다(분석 기록). 진행 중인 작업은 watchRfpJob이 맡는다. */
export async function replayRfpJob(target: RfpTarget, signal: AbortSignal): Promise<{ job: RfpJob; activity: RfpProgress[]; startedAt?: string }> {
  const job = await readRfpJob(target, signal);
  let activity: RfpProgress[] = [], startedAt: string | undefined;
  if (job.last_event_sequence > 0) {
    try {
      await stream(target, 0, signal, () => undefined, p => {
        if (p.type === 'job.started') startedAt = p.occurredAt;
        activity = addActivity(activity, p);
      });
    } catch (error) {
      // 서버가 재생 뒤 종료 이벤트 없이 연결을 닫아도 받은 단계까지는 보인다.
      if (signal.aborted || !activity.length) throw error;
    }
  }
  return { job, activity, startedAt };
}

export async function watchRfpJob(target: RfpTarget, signal: AbortSignal, update: (view: RfpWatch) => void): Promise<void> {
  let cursor: number | undefined, progress: RfpProgress | undefined, failures = 0, resetCursor = false;
  let job: RfpJob | undefined, checkedAt: number | undefined;
  let activity: RfpProgress[] = [], startedAt: string | undefined;
  let restoreThrough = 0, restoredCount = 0;
  const publish = (connection: RfpWatch['connection'], message?: string) => { if (!signal.aborted) update({ connection, job, progress: (cursor ?? 0) < restoreThrough ? undefined : progress, checkedAt, message, activity: (cursor ?? 0) < restoreThrough ? [] : activity, startedAt, restoredCount }); };
  while (!signal.aborted) {
    try {
      publish(failures ? 'reconnecting' : 'connecting');
      job = await readRfpJob(target, signal); signal.throwIfAborted(); checkedAt = Date.now();
      if (!activeRfpJob(job.status)) { publish('ended'); return; }
      if (cursor === undefined) cursor = 0;
      // Restore retained observations through the GET snapshot in one view. Refresh or
      // reconnect must not animate historical parse/extract steps as new activity.
      restoreThrough = job.last_event_sequence;
      if (resetCursor) { cursor = job.last_event_sequence; resetCursor = false; }
      await stream(target, cursor, signal, () => publish('connected'), p => {
        cursor = p.sequence; progress = p; failures = 0;
        if (p.type === 'job.started') startedAt = p.occurredAt;
        // One latest observation per public stage; neither order nor presence means completion.
        activity = addActivity(activity, p);
        if (p.sequence === restoreThrough) restoredCount = activity.length;
        if (p.sequence >= restoreThrough) publish('connected');
      });
      // 204 or terminal event: re-read status; do not treat the event itself as committed completion.
      job = await readRfpJob(target, signal); signal.throwIfAborted(); checkedAt = Date.now();
      if (!activeRfpJob(job.status)) { publish('ended'); return; }
      throw new TypeError('Terminal not confirmed');
    } catch (error) {
      if (signal.aborted) return;
      if (deniedRfpJob(error)) { job = undefined; progress = undefined; activity = []; startedAt = undefined; restoredCount = 0; publish('stopped', '이 작업의 조회 권한을 확인할 수 없습니다. 계정과 작업을 확인하세요.'); return; }
      if (error instanceof CursorExpired || error instanceof Gap) { resetCursor = true; }
      failures++;
      if (!job || failures > 3 || error instanceof ApiError && error.status < 500 && !(error instanceof CursorExpired)) {
        publish('fallback', '실시간 연결을 확인하지 못했습니다. 30초 간격 상태 조회로 확인합니다.'); return;
      }
      publish('reconnecting', '실시간 연결이 끊겼습니다. 작업 실패로 판정하지 않고 상태를 다시 확인합니다.');
      try { await delay(1000 * 2 ** (failures - 1), signal); } catch { return; }
    }
  }
}
