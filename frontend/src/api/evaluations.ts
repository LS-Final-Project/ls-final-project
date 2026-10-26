import type { components } from './generated/platform';

export type JobStatus = components['schemas']['EvaluationJobStatus'];
export const statusLabels: Record<JobStatus['run_status'], string> = {
  QUEUED: '대기 중', RUNNING: '실행 중', SUCCEEDED: '실행 완료',
  PARTIAL: '일부 완료', FAILED: '실패', CANCELLED: '취소됨', TIMED_OUT: '시간 초과',
};
export const isJobId = (value: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

const messages: Record<number, string> = {
  401: '로그인이 만료되었습니다. 다시 로그인한 뒤 조회해 주세요.',
  403: '지정된 평가자만 조회할 수 있습니다. 평가자 권한을 확인해 주세요.',
  404: '조회 가능한 작업을 찾지 못했습니다. 작업 ID와 접근 권한을 확인해 주세요.',
  503: '평가 조회가 현재 비활성 상태이거나 서비스를 사용할 수 없습니다.',
};

/** 실제 서버에서 제한된 평가 상태를 조회한다. Args: 작업 ID, 취소 신호. Returns: 검증된 상태. */
export async function getEvaluationJob(jobId: string, signal: AbortSignal): Promise<JobStatus> {
  if (!isJobId(jobId)) throw new Error('올바른 작업 ID를 입력해 주세요.');
  const response = await fetch(`/api/v1/platform/jobs/${jobId}`, {
    credentials: 'same-origin', cache: 'no-store',
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(messages[response.status] || '상태를 확인하지 못했습니다. 잠시 후 다시 조회해 주세요.');
  const body = await response.json().catch(() => null);
  const data = body?.data;
  if (!data || typeof data.job_id !== 'string' || data.job_id.toLowerCase() !== jobId.toLowerCase() || typeof data.run_id !== 'string' || !isJobId(data.run_id)
      || data.owner !== 'platform' || data.kind !== 'evaluation.run'
      || !Object.hasOwn(statusLabels, data.run_status) || !Object.hasOwn(statusLabels, data.status)
      || data.status === 'TIMED_OUT' || typeof data.needs_review !== 'boolean'
      || !Number.isSafeInteger(data.version) || data.version < 1
      || !Number.isSafeInteger(data.last_event_sequence) || data.last_event_sequence < 0) {
    throw new Error('서버의 상태 응답을 확인할 수 없습니다. 다시 조회해 주세요.');
  }
  return {
    job_id: data.job_id, run_id: data.run_id, owner: data.owner, kind: data.kind,
    status: data.status, run_status: data.run_status, needs_review: data.needs_review,
    version: data.version, last_event_sequence: data.last_event_sequence,
  };
}
