import { evaluationHttpError, isJobId, statusLabels } from './evaluations';

const BASE = '/api/v1/platform/evaluations';
const BLOCKED = ['ADMISSION_UNAVAILABLE', 'FINAL_TEST_LOCKED', 'EXPERIMENT_NOT_APPROVED', 'LOCAL_SMOKE_ONLY', 'EXPERIMENT_HASH_MISMATCH'] as const;
export type BlockedReason = typeof BLOCKED[number];
export const blockedLabels: Record<BlockedReason, string> = {
  ADMISSION_UNAVAILABLE: '접수 비활성',
  FINAL_TEST_LOCKED: '최종 시험 잠금',
  EXPERIMENT_NOT_APPROVED: '승인되지 않은 구성',
  LOCAL_SMOKE_ONLY: '합성 smoke만 접수 가능',
  EXPERIMENT_HASH_MISMATCH: '구성 hash 불일치',
};

export interface Experiment {
  experimentId: string; name: string; purpose: string; dataSplit: string | null; dataOrigin: string;
  modelId: string; targetCount: number; createdAt: string; admissible: boolean; blockedReason: BlockedReason | null;
}
export interface RunItem {
  runId: string; jobId: string; experimentId: string; status: keyof typeof statusLabels;
  requestedCount: number; needsReview: boolean; failureCode: string | null; createdAt: string;
}
/** 한 번의 접수 시도. 응답을 받기 전 재전송은 같은 키를 다시 쓴다. */
export interface Admission { experimentId: string; key: string }

/** 접수 결과를 확인하지 못한 오류. retryable이면 같은 접수(같은 키)로 다시 확인한다. */
export class AdmissionError extends Error {
  readonly retryable: boolean;
  constructor(message: string, retryable = false) {
    super(message);
    this.retryable = retryable;
  }
}

/** 제한된 JSON 목록을 읽는다. Args: 경로·취소 신호. Returns: data 객체. */
async function read(path: string, signal: AbortSignal): Promise<Record<string, unknown>> {
  const response = await fetch(BASE + path, {
    credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw evaluationHttpError(response.status);
  const data = (await response.json().catch(() => null))?.data;
  if (!data || typeof data !== 'object') throw new AdmissionError('서버 응답을 확인할 수 없습니다. 다시 불러와 주세요.');
  return data;
}

const text = (value: unknown) => typeof value === 'string';
const time = (value: unknown) => text(value) && !Number.isNaN(Date.parse(value as string));

/** 서버 접수 가능 여부를 읽는다. Args: 취소 신호. Returns: 접수 가능 여부. */
export async function getAdmissionAvailable(signal: AbortSignal): Promise<boolean> {
  const data = await read('/policy', signal);
  if (typeof data.admission_available !== 'boolean') throw new AdmissionError('평가 정책을 확인할 수 없습니다.');
  return data.admission_available;
}

/** 본인 실험 목록을 읽고 형식을 확인한다. Args: 취소 신호. Returns: 실험 목록. */
export async function listExperiments(signal: AbortSignal): Promise<Experiment[]> {
  const items = (await read('/experiments', signal)).items;
  if (!Array.isArray(items)) throw new AdmissionError('실험 목록을 확인할 수 없습니다.');
  return items.map(item => {
    if (!item || !isJobId(item.experiment_id) || !text(item.name) || !text(item.purpose) || !(item.data_split === null || text(item.data_split))
        || !text(item.data_origin) || !text(item.model_id) || !Number.isSafeInteger(item.target_count) || !time(item.created_at)
        || typeof item.admissible !== 'boolean' || !(item.blocked_reason === null || BLOCKED.includes(item.blocked_reason))
        || item.admissible !== (item.blocked_reason === null)) throw new AdmissionError('실험 목록을 확인할 수 없습니다.');
    return {
      experimentId: item.experiment_id, name: item.name, purpose: item.purpose, dataSplit: item.data_split,
      dataOrigin: item.data_origin, modelId: item.model_id, targetCount: item.target_count, createdAt: item.created_at,
      admissible: item.admissible, blockedReason: item.blocked_reason,
    };
  });
}

/** 본인 접수 실행 목록을 읽고 형식을 확인한다. Args: 취소 신호. Returns: 실행 목록. */
export async function listRuns(signal: AbortSignal): Promise<RunItem[]> {
  const items = (await read('/runs', signal)).items;
  if (!Array.isArray(items)) throw new AdmissionError('실행 목록을 확인할 수 없습니다.');
  return items.map(item => {
    if (!item || !isJobId(item.run_id) || !isJobId(item.job_id) || !isJobId(item.experiment_id) || !Object.hasOwn(statusLabels, item.status)
        || !Number.isSafeInteger(item.requested_count) || typeof item.needs_review !== 'boolean'
        || !(item.failure_code === null || text(item.failure_code)) || !time(item.created_at)) throw new AdmissionError('실행 목록을 확인할 수 없습니다.');
    return {
      runId: item.run_id, jobId: item.job_id, experimentId: item.experiment_id, status: item.status,
      requestedCount: item.requested_count, needsReview: item.needs_review, failureCode: item.failure_code, createdAt: item.created_at,
    };
  });
}

/** 새 접수 시도를 만든다. Args: 실험 ID. Returns: 이 시도에서만 쓸 멱등 키를 가진 접수. */
export const prepareAdmission = (experimentId: string): Admission => ({ experimentId, key: crypto.randomUUID() });

/**
 * 접수를 현재 세션의 CSRF로 보낸다. 실패해도 같은 접수 객체로 다시 보내면 서버가 최초 응답을 재생한다.
 * Args: 접수, 취소 신호. Returns: 접수된 실행·작업 ID.
 */
export async function sendAdmission(admission: Admission, signal: AbortSignal): Promise<{ runId: string; jobId: string }> {
  const me = await fetch('/api/v1/platform/me', {
    credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
  });
  await me.body?.cancel();
  if (!me.ok) throw evaluationHttpError(me.status);
  const csrf = me.headers.get('X-CSRF-Token');
  if (!csrf) throw new AdmissionError('세션 보안 정보를 확인하지 못했습니다. 다시 로그인해 주세요.');
  let response: Response;
  try {
    response = await fetch(`${BASE}/runs`, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': admission.key },
      body: JSON.stringify({ experiment_id: admission.experimentId }),
    });
  } catch {
    throw new AdmissionError('접수 결과를 확인하지 못했습니다. 같은 접수로 다시 확인해 주세요.', true);
  }
  if (!response.ok) {
    if (response.status === 403) {
      const code = (await response.json().catch(() => null))?.error?.code;
      if (BLOCKED.includes(code)) throw new AdmissionError(`접수할 수 없는 실험입니다: ${blockedLabels[code as BlockedReason]}.`);
      throw evaluationHttpError(403);
    }
    await response.body?.cancel();
    if (response.status >= 500) throw new AdmissionError('접수 결과가 불명확합니다. 같은 접수로 다시 확인해 주세요.', true);
    if (response.status === 409) throw new AdmissionError('실험 구성이 바뀌었거나 같은 키로 다른 요청을 보냈습니다. 목록을 새로 불러온 뒤 다시 접수해 주세요.');
    throw evaluationHttpError(response.status);
  }
  const data = (await response.json().catch(() => null))?.data;
  if (!data || !isJobId(data.run_id) || !isJobId(data.job_id) || data.status !== 'QUEUED') {
    throw new AdmissionError('접수 응답을 확인하지 못했습니다. 같은 접수로 다시 확인해 주세요.', true);
  }
  return { runId: data.run_id, jobId: data.job_id };
}
