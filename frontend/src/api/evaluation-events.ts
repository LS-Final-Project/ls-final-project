import { EvaluationReadError, evaluationHttpError, getEvaluationJob, type JobStatus } from './evaluations.ts';

export type EvaluationView = {
  connection: 'loading' | 'connecting' | 'connected' | 'reconnecting' | 'ended' | 'error';
  data?: JobStatus;
  checkedAt?: number;
  message?: string;
};
const eventTypes = new Set(['job.queued', 'job.started', 'job.progress', 'job.completed', 'job.failed']);
const interrupted = () => new EvaluationReadError('실시간 연결이 끊겼습니다.', true);
const invalidStream = () => new EvaluationReadError('실시간 응답을 확인할 수 없습니다. 다시 조회해 주세요.');

/** 다음 변경 신호까지 SSE를 읽는다. Args: 작업, 커서, 취소 신호, 연결 알림. Returns: 변경 시 완료. */
export async function nextEvaluationChange(jobId: string, after: number, signal: AbortSignal, onConnected: () => void): Promise<void> {
  const controller = new AbortController();
  const combined = AbortSignal.any([signal, controller.signal]);
  let timer = setTimeout(() => controller.abort(), 15000);
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const response = await fetch(`/api/v1/platform/jobs/${jobId}/events?after=${after}`, {
      credentials: 'same-origin', cache: 'no-store', signal: combined,
      headers: { Accept: 'text/event-stream' },
    });
    if (response.status === 204 || response.status === 410) throw interrupted();
    if (!response.ok) throw evaluationHttpError(response.status);
    if (response.headers.get('Content-Type')?.split(';')[0].trim() !== 'text/event-stream' || !response.body) throw invalidStream();
    onConnected();
    reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8', { fatal: true });
    let buffer = '';
    let lines: string[] = [];
    let size = 0;
    while (true) {
      clearTimeout(timer);
      timer = setTimeout(() => controller.abort(), 45000);
      const chunk = await reader.read();
      if (chunk.done) throw interrupted();
      try { buffer += decoder.decode(chunk.value, { stream: true }); }
      catch { throw invalidStream(); }
      let end: number;
      while ((end = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, end).replace(/\r$/, '');
        buffer = buffer.slice(end + 1);
        size += line.length;
        if (size > 65536) throw invalidStream();
        if (line) { lines.push(line); continue; }
        const fields = lines.filter(value => !value.startsWith(':'));
        lines = []; size = 0;
        if (!fields.length) continue;
        const id = fields.find(value => value.startsWith('id:'))?.slice(3).trim();
        const type = fields.find(value => value.startsWith('event:'))?.slice(6).trim();
        const body = fields.filter(value => value.startsWith('data:')).map(value => value.slice(5).trimStart()).join('\n');
        let event;
        try { event = JSON.parse(body); } catch { throw invalidStream(); }
        if (!event || typeof event.job_id !== 'string' || event.job_id.toLowerCase() !== jobId.toLowerCase() || !Number.isSafeInteger(event.sequence) || event.sequence < 1
            || id !== String(event.sequence) || !type || !eventTypes.has(type) || event.type !== type) throw invalidStream();
        // payload로 상태를 추측하지 않는다. 중복은 건너뛰고 순서 공백도 GET으로 복구한다.
        if (event.sequence > after) return;
      }
      if (size + buffer.length > 65536) throw invalidStream();
    }
  } catch (error) {
    if (signal.aborted) throw error;
    if (error instanceof EvaluationReadError) throw error;
    if (error instanceof TypeError && !controller.signal.aborted) throw interrupted();
    if (controller.signal.aborted) throw interrupted();
    throw invalidStream();
  } finally {
    clearTimeout(timer);
    controller.abort();
    await reader?.cancel().catch(() => undefined);
    reader?.releaseLock();
  }
}

/** 재접속 대기를 취소 가능하게 수행한다. Args: 대기 밀리초, 취소 신호. Returns: 대기 완료. */
async function reconnectDelay(ms: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  await new Promise<void>((resolve, reject) => {
    const finish = () => { signal.removeEventListener('abort', abort); resolve(); };
    const timer = setTimeout(finish, ms);
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(signal.reason); };
    signal.addEventListener('abort', abort, { once: true });
  });
}

/** 상태와 구독을 순차 복구한다. Args: 작업, 취소 신호, 화면 갱신 함수. Returns: 종료/중단 시 완료. */
export async function watchEvaluation(jobId: string, signal: AbortSignal, update: (view: EvaluationView) => void): Promise<void> {
  let failures = 0;
  while (!signal.aborted) {
    try {
      if (!failures) update({ connection: 'loading' });
      const data = await getEvaluationJob(jobId, signal);
      if (signal.aborted) return;
      const checkedAt = Date.now();
      if (!['QUEUED', 'RUNNING'].includes(data.run_status)) {
        update({ connection: 'ended', data, checkedAt });
        return;
      }
      update({ connection: 'connecting', data, checkedAt });
      await nextEvaluationChange(jobId, data.last_event_sequence, signal, () => {
        if (!signal.aborted) update({ connection: 'connected', data, checkedAt });
      });
      failures = 0;
    } catch (error) {
      if (signal.aborted) return;
      const retryable = error instanceof EvaluationReadError ? error.retryable
        : error instanceof TypeError || (error instanceof Error && error.name === 'TimeoutError');
      if (!retryable || failures >= 3) {
        update({ connection: 'error', message: error instanceof EvaluationReadError ? error.message : '서버에 연결하지 못했습니다. 다시 조회해 주세요.' });
        return;
      }
      failures++;
      update({ connection: 'reconnecting', message: `연결을 다시 확인하고 있습니다. (${failures}/3)` });
      try { await reconnectDelay(1000 * 2 ** (failures - 1), signal); } catch { return; }
    }
  }
}
