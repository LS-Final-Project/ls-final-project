import type { components } from './generated/platform';
import { EvaluationReadError, evaluationHttpError, isJobId, statusLabels } from './evaluations.ts';

export type RunSummary = components['schemas']['EvaluationRunSummary'];
type AttemptSummary = RunSummary['first'];
const invalid = () => new EvaluationReadError('집계 응답을 확인할 수 없습니다. 다시 조회해 주세요.');
const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const measurement = (value: unknown): value is number | null => value === null || typeof value === 'number' && Number.isFinite(value) && value >= 0;

/** JSON 객체만 허용한다. Args: 검증할 값. Returns: 안전하게 필드를 조회할 객체. */
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}

/** 비용의 십진 문자열과 보고 범위를 보존한다. Args: 선택적 비용·시도 수. Returns: 검증한 비용 또는 구버전 미연결. */
function cost(value: unknown, size: number): AttemptSummary['cost_usd'] {
  if (value === undefined || value === null) return null;
  const data = object(value);
  const amount = (v: unknown): v is string | null => v === null || typeof v === 'string' && v.length <= 128 && v.trim() === v && /^(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(v);
  if (!count(data.reported_count) || !count(data.missing_count) || data.reported_count + data.missing_count !== size
      || !amount(data.known_sum) || !amount(data.total) || !count(data.pricing_version_count)
      || data.pricing_version_count > data.reported_count) throw invalid();
  if (data.reported_count === 0 ? data.known_sum !== null || data.pricing_version_count !== 0 : data.known_sum === null || data.pricing_version_count < 1) throw invalid();
  if (data.total !== (size > 0 && data.missing_count === 0 ? data.known_sum : null)) throw invalid();
  return { reported_count: data.reported_count, missing_count: data.missing_count,
    known_sum: data.known_sum, total: data.total, pricing_version_count: data.pricing_version_count };
}

type SyntheticQuality = NonNullable<RunSummary['synthetic_quality']>;
const ratio = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
const outcomeKeys = ['HELD', 'NOT_STARTED', 'SCHEMA_VIOLATION', 'TIMEOUT', 'TRANSPORT_FAILURE', 'VALID'] as const;
const subtypeLabels = ['DEFECT', 'NON_DEFECT'] as const;
/** 키 집합이 정확히 같은지 본다. Args: 객체·허용 키. Returns: 일치 여부. */
const exactKeys = (data: Record<string, unknown>, keys: readonly string[]) => Object.keys(data).sort().join() === [...keys].sort().join();

/** 저장된 합성 품질 집계를 검증해 투영한다. Args: 선택 필드 값. Returns: 검증한 집계 또는 미저장(null). */
function syntheticQuality(value: unknown): SyntheticQuality | null {
  if (value === undefined || value === null) return null;
  const data = object(value);
  const scored = data.scored_target_count;
  if (data.metric !== 'return_subtype' || data.attempt !== 'first' || data.data_origin !== 'synthetic' || data.quality_gate_evaluated !== false
      || typeof data.scoring_version !== 'string' || !data.scoring_version || data.scoring_version.length > 64
      || !count(data.stored_scoring_version_count) || data.stored_scoring_version_count < 1
      || typeof data.stored_at !== 'string' || !Number.isFinite(Date.parse(data.stored_at))
      || !(data.gold_version === null || typeof data.gold_version === 'string')
      || typeof data.gold_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(data.gold_sha256)
      || typeof data.run_status_at_scoring !== 'string' || !Object.hasOwn(statusLabels, data.run_status_at_scoring)
      || !count(data.target_count) || !count(scored) || scored > data.target_count
      || !count(data.correct) || data.correct > scored || !count(data.null_predictions) || data.null_predictions > scored
      || !(data.accuracy === null || ratio(data.accuracy)) || !(data.macro_f1 === null || ratio(data.macro_f1))) throw invalid();
  const outcomes = object(data.outcomes);
  if (!exactKeys(outcomes, outcomeKeys) || outcomeKeys.some(key => !count(outcomes[key]))
      || outcomeKeys.reduce((sum, key) => sum + (outcomes[key] as number), 0) !== scored) throw invalid();
  const rawClasses = object(data.per_class);
  if (!exactKeys(rawClasses, subtypeLabels)) throw invalid();
  const perClass = {} as SyntheticQuality['per_class'];
  for (const label of subtypeLabels) {
    const item = object(rawClasses[label]);
    if (!count(item.support) || !count(item.tp) || !count(item.fp) || !count(item.fn) || item.tp > item.support
        || !ratio(item.precision) || !ratio(item.recall) || !ratio(item.f1)) throw invalid();
    perClass[label] = { support: item.support, tp: item.tp, fp: item.fp, fn: item.fn, precision: item.precision, recall: item.recall, f1: item.f1 };
  }
  if (subtypeLabels.reduce((sum, label) => sum + perClass[label].support, 0) !== scored) throw invalid();
  return {
    metric: 'return_subtype', attempt: 'first', data_origin: 'synthetic', quality_gate_evaluated: false,
    scoring_version: data.scoring_version, stored_scoring_version_count: data.stored_scoring_version_count,
    stored_at: data.stored_at, gold_version: data.gold_version, gold_sha256: data.gold_sha256,
    run_status_at_scoring: data.run_status_at_scoring as SyntheticQuality['run_status_at_scoring'],
    target_count: data.target_count, scored_target_count: scored, correct: data.correct,
    accuracy: data.accuracy, macro_f1: data.macro_f1, null_predictions: data.null_predictions,
    outcomes: Object.fromEntries(outcomeKeys.map(key => [key, outcomes[key]])) as SyntheticQuality['outcomes'],
    per_class: perClass,
  };
}

/** 비교 키는 형식만 확인한다. Args: 선택 필드 값. Returns: 소문자 hex 키 또는 구버전 미제공(null). */
function comparisonKey(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) throw invalid();
  return value;
}

/** 허용한 집계 수치만 검증해 반환한다. Args: 최초/재시도/전체 값. Returns: 명시적 집계 DTO. */
function attempt(value: unknown): AttemptSummary {
  const data = object(value);
  const fields = ['stored_attempt_count', 'unfinished_attempt_count', 'stored_raw_response_count', 'schema_violation_count', 'incomplete_evidence_attempt_count'] as const;
  for (const key of fields) if (!count(data[key])) throw invalid();
  const size = data.stored_attempt_count as number;
  for (const key of fields) if ((data[key] as number) > size) throw invalid();
  if (!measurement(data.schema_violation_rate) || data.schema_violation_rate !== null && data.schema_violation_rate > 1) throw invalid();
  const responses = data.stored_raw_response_count as number;
  const incomplete = data.incomplete_evidence_attempt_count as number;
  if (data.schema_violation_rate !== (responses > 0 && incomplete === 0 ? (data.schema_violation_count as number) / responses : null)) throw invalid();
  const rawTokens = object(data.tokens);
  const tokens = {} as AttemptSummary['tokens'];
  for (const key of ['input_tokens', 'output_tokens', 'thought_tokens', 'cached_input_tokens', 'total_tokens'] as const) {
    const token = object(rawTokens[key]);
    if (!count(token.reported_count) || !count(token.missing_count) || token.reported_count + token.missing_count !== size
        || !(token.known_sum === null || count(token.known_sum)) || !(token.total === null || count(token.total))) throw invalid();
    if (token.reported_count === 0 ? token.known_sum !== null : token.known_sum === null) throw invalid();
    if (token.total !== (size > 0 && token.missing_count === 0 ? token.known_sum : null)) throw invalid();
    tokens[key] = { reported_count: token.reported_count, missing_count: token.missing_count, known_sum: token.known_sum, total: token.total };
  }
  const latency = object(data.latency_ms);
  if (!count(latency.reported_count) || !count(latency.missing_count) || latency.reported_count + latency.missing_count !== size
      || !measurement(latency.p50) || !measurement(latency.p95)
      || (latency.reported_count === 0 ? latency.p50 !== null || latency.p95 !== null : latency.p50 === null || latency.p95 === null)) throw invalid();
  return {
    stored_attempt_count: size, unfinished_attempt_count: data.unfinished_attempt_count as number,
    stored_raw_response_count: data.stored_raw_response_count as number,
    schema_violation_count: data.schema_violation_count as number,
    incomplete_evidence_attempt_count: data.incomplete_evidence_attempt_count as number,
    schema_violation_rate: data.schema_violation_rate,
    cost_usd: cost(data.cost_usd, size),
    tokens, latency_ms: { reported_count: latency.reported_count, missing_count: latency.missing_count, p50: latency.p50, p95: latency.p95 },
  };
}

/** 모의 응답을 우회하고 현재 세션으로 집계를 읽는다. Args: 실행 ID·취소 신호. Returns: 검증·투영한 합성 집계. */
export async function getEvaluationSummary(runId: string, signal: AbortSignal): Promise<RunSummary> {
  if (!isJobId(runId)) throw invalid();
  const response = await fetch(`/api/v1/platform/evaluations/runs/${runId}/summary`, {
    credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
    headers: { Accept: 'application/json' },
  });
  if (response.status === 403) throw new EvaluationReadError('평가자 권한 또는 현재 구성 승인을 확인해 주세요.');
  if (response.status === 409) throw new EvaluationReadError('평가 구성이나 대상이 변경되어 집계를 조회할 수 없습니다.');
  if (!response.ok) throw evaluationHttpError(response.status);
  if (response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') throw invalid();
  const body = await response.json().catch(() => null);
  const data = object(body?.data);
  if (typeof data.run_id !== 'string' || data.run_id.toLowerCase() !== runId.toLowerCase()
      || data.report_version !== 'cs-run-summary-v1' || data.data_origin !== 'synthetic'
      || data.quality_available !== false || data.quality_unavailable_reason !== 'GOLD_NOT_CONNECTED'
      || typeof data.status !== 'string' || !Object.hasOwn(statusLabels, data.status)
      || typeof data.generated_at !== 'string' || !Number.isFinite(Date.parse(data.generated_at))
      || !count(data.target_count) || !count(data.targets_without_first_attempt) || data.targets_without_first_attempt > data.target_count) throw invalid();
  return {
    report_version: data.report_version, run_id: data.run_id, status: data.status as RunSummary['status'],
    generated_at: data.generated_at, data_origin: data.data_origin, quality_available: data.quality_available,
    quality_unavailable_reason: data.quality_unavailable_reason, target_count: data.target_count,
    targets_without_first_attempt: data.targets_without_first_attempt,
    first: attempt(data.first), retry: attempt(data.retry), all: attempt(data.all),
    synthetic_quality: syntheticQuality(data.synthetic_quality),
    comparison_key: comparisonKey(data.comparison_key),
  };
}
