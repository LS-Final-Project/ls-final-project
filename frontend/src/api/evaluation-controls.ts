import { evaluationHttpError, isJobId, statusLabels, type JobStatus } from './evaluations.ts';

export type RunAction = 'cancel' | 'resume';
export type RunCommand = { runId: string; jobId: string; action: RunAction; etag: string; key: string };

/** 현재 탭의 불명확 명령을 복원한다. Args: 작업 ID, 탭 저장소. Returns: 검증된 명령 또는 없음. */
export function loadRunCommand(jobId: string, storage: Pick<Storage, 'getItem'>): RunCommand | undefined {
  const value = storage.getItem(`evaluation-command:${jobId}`);
  if (!value) return undefined;
  const data = JSON.parse(value);
  if (data?.jobId !== jobId || !isJobId(data.runId) || !isJobId(data.key)
      || !['cancel', 'resume'].includes(data.action) || !/^"[1-9][0-9]{0,15}"$/.test(data.etag)) throw new Error('Invalid saved command');
  return { runId: data.runId, jobId: data.jobId, action: data.action, etag: data.etag, key: data.key };
}

/** POST 전에 재확인 정보만 보존한다. Args: 고정 명령, 탭 저장소. Returns: 저장 완료. */
export function saveRunCommand(command: RunCommand, storage: Pick<Storage, 'setItem'>): void {
  try { storage.setItem(`evaluation-command:${command.jobId}`, JSON.stringify({ runId: command.runId, jobId: command.jobId, action: command.action, etag: command.etag, key: command.key })); }
  catch { throw new RunControlError('재확인 정보를 보존할 수 없어 요청을 보내지 않았습니다. 브라우저 저장소 설정을 확인해 주세요.'); }
}

/** 결과 불명확 여부를 보존한다. Args: 안전한 메시지와 불명확 여부. Returns: 제어 오류. */
export class RunControlError extends Error {
  readonly uncertain: boolean;
  constructor(message: string, uncertain = false) { super(message); this.uncertain = uncertain; }
}

/** 응답의 식별자·판본·상태를 확인한다. Args: 응답, 요청 대상. Returns: 제한 필드 또는 null. */
async function readResult(response: Response, runId: string, jobId: string) {
  const body = await response.json().catch(() => null);
  const data = body?.data;
  if (!data || data.run_id !== runId || data.job_id !== jobId || !Number.isSafeInteger(data.version) || data.version < 1
      || !Object.hasOwn(statusLabels, data.status) || typeof data.needs_review !== 'boolean'
      || response.headers.get('ETag') !== `"${data.version}"`) return null;
  return { version: data.version, status: data.status as JobStatus['run_status'], needsReview: data.needs_review as boolean };
}

/** 최신 실행 판본으로 확인용 명령을 고정한다. Args: 표시된 작업, 동작, 취소 신호. Returns: 아직 전송하지 않은 명령. */
export async function prepareRunCommand(snapshot: JobStatus, action: RunAction, signal: AbortSignal): Promise<RunCommand> {
  const response = await fetch(`/api/v1/platform/evaluations/runs/${snapshot.run_id}`, {
    credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
  });
  if (!response.ok) throw evaluationHttpError(response.status);
  const data = await readResult(response, snapshot.run_id, snapshot.job_id);
  if (!data) throw new RunControlError('실행 판본을 확인하지 못했습니다. 다시 조회해 주세요.');
  if (data.version !== snapshot.version) throw new RunControlError('실행 상태가 변경되었습니다. 최신 상태를 확인한 뒤 다시 선택해 주세요.');
  if (!['QUEUED', 'RUNNING'].includes(data.status) || action === 'resume' && !data.needsReview) throw new RunControlError('현재 상태에서는 이 동작을 요청할 수 없습니다.');
  return { runId: snapshot.run_id, jobId: snapshot.job_id, action, etag: `"${data.version}"`, key: crypto.randomUUID() };
}

/** 같은 명령을 현재 세션의 CSRF로 전송한다. Args: 고정 명령, 취소 신호. Returns: 저장/재생 응답 확인 후 완료. */
export async function sendRunCommand(command: RunCommand, signal: AbortSignal): Promise<void> {
  const me = await fetch('/api/v1/platform/me', {
    credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
  });
  // 응답 본문·토큰을 전역 상태나 영구 저장소에 보관하지 않는다.
  await me.body?.cancel();
  if (!me.ok) throw evaluationHttpError(me.status);
  const csrf = me.headers.get('X-CSRF-Token');
  if (!csrf) throw new RunControlError('세션 보안 정보를 확인하지 못했습니다. 다시 로그인해 주세요.');
  let response: Response;
  try {
    response = await fetch(`/api/v1/platform/evaluations/runs/${command.runId}/${command.action}`, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
      headers: { 'X-CSRF-Token': csrf, 'If-Match': command.etag, 'Idempotency-Key': command.key },
    });
  } catch {
    throw new RunControlError('요청 결과를 확인하지 못했습니다. 같은 요청으로 결과를 다시 확인해 주세요.', true);
  }
  if (!response.ok) {
    await response.body?.cancel();
    if (response.status >= 500) throw new RunControlError('요청 결과가 불명확합니다. 같은 요청으로 다시 확인해 주세요.', true);
    if (response.status === 412 || response.status === 409) throw new RunControlError('실행 상태 또는 요청 조건이 변경되었습니다. 최신 상태를 확인해 주세요.');
    throw evaluationHttpError(response.status);
  }
  if (!await readResult(response, command.runId, command.jobId)) throw new RunControlError('처리 응답을 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.', true);
}
