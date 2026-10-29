import type { components } from './generated/platform';
import { EvaluationReadError, evaluationHttpError, isJobId } from './evaluations.ts';

export type RunCases = components['schemas']['EvaluationRunCases'];
type Attempt = RunCases['items'][number]['attempts'][number];
const invalid = () => new EvaluationReadError('사례 응답을 확인할 수 없습니다. 다시 조회해 주세요.');
const caseStatuses = new Set(['PENDING', 'VALID', 'INVALID_OUTPUT', 'HELD', 'FAILED', 'EXCLUDED']);
const outcomes = new Set(['VALID', 'SCHEMA_VIOLATION', 'TRANSPORT_FAILURE', 'TIMEOUT', 'HELD']);
const uuid = (value: unknown): value is string => typeof value === 'string' && isJobId(value);
const code = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_]{1,64}$/.test(value);
const text = (value: unknown, max = 128): value is string | null => value === null || typeof value === 'string' && value.length <= max;
const whole = (value: unknown, min = 0): value is number | null => value === null || Number.isSafeInteger(value) && (value as number) >= min;

/** 시도 하나를 허용 키만 골라 확인한다. Args: 응답 값. Returns: 투영한 시도. */
function attempt(value: unknown): Attempt {
  const item = value as Record<string, unknown> | null;
  if (!item || typeof item !== 'object' || !code(item.step) || !whole(item.attempt, 1) || item.attempt === null
      || !outcomes.has(item.outcome as string) || !text(item.error_code, 64) || !whole(item.http_status, 100)
      || !Array.isArray(item.finish_reasons) || !item.finish_reasons.every(reason => code(reason))
      || !whole(item.call_attempts, 1) || !text(item.model_version) || !whole(item.latency_ms)) throw invalid();
  let parsed: Attempt['parsed_output'] = null;
  if (item.parsed_output !== null) {
    const out = item.parsed_output as Record<string, unknown>;
    const nullable = (key: string) => out[key] === null || code(out[key]);
    if (!out || typeof out !== 'object' || !['category_code', 'secondary_category', 'urgency_flag', 'return_subtype'].every(nullable)
        || !Array.isArray(out.provided_info) || !out.provided_info.every(code)) throw invalid();
    parsed = { category_code: out.category_code as string | null, secondary_category: out.secondary_category as string | null,
      urgency_flag: out.urgency_flag as string | null, return_subtype: out.return_subtype as string | null, provided_info: out.provided_info as string[] };
  }
  return { step: item.step, attempt: item.attempt, outcome: item.outcome as Attempt['outcome'], error_code: item.error_code,
    http_status: item.http_status, finish_reasons: item.finish_reasons as string[], call_attempts: item.call_attempts,
    model_version: item.model_version, latency_ms: item.latency_ms, parsed_output: parsed };
}

/**
 * 실행의 사례별 시도 한 페이지를 읽는다(KAN-139 4차). 사례 ID는 해시 앞 12자뿐이고 원문·정답·원시 응답은 없다.
 * Args: 실행 ID, 앞 페이지의 next_cursor(없으면 처음), 취소 신호.
 * Returns: 검증·투영한 사례와 다음 커서.
 */
export async function getRunCases(runId: string, cursor: string | null, signal: AbortSignal): Promise<RunCases> {
  if (!isJobId(runId) || (cursor !== null && !isJobId(cursor))) throw invalid();
  const query = new URLSearchParams({ limit: '20', ...(cursor ? { cursor } : {}) });
  const response = await fetch(`/api/v1/platform/evaluations/runs/${runId}/cases?${query}`, {
    credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
    headers: { Accept: 'application/json' },
  });
  if (response.status === 403) throw new EvaluationReadError('평가자 권한 또는 현재 구성 승인을 확인해 주세요.');
  if (!response.ok) throw evaluationHttpError(response.status);
  if (response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') throw invalid();
  const data = (await response.json().catch(() => null))?.data;
  if (!data || !Array.isArray(data.items) || data.items.length > 20 || !(data.next_cursor === null || uuid(data.next_cursor))) throw invalid();
  return {
    items: data.items.map((value: Record<string, unknown> | null) => {
      if (!value || !uuid(value.case_id) || typeof value.target_ref_sha !== 'string' || !/^[0-9a-f]{12}$/.test(value.target_ref_sha)
          || !caseStatuses.has(value.status as string) || !Array.isArray(value.attempts)) throw invalid();
      return { case_id: value.case_id, target_ref_sha: value.target_ref_sha, status: value.status, attempts: value.attempts.map(attempt) };
    }),
    next_cursor: data.next_cursor,
  };
}
