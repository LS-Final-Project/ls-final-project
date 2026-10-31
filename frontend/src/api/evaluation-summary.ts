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

type ClassQuality = SyntheticQuality['per_class']['DEFECT'];
/** 클래스 하나의 저장 수치를 검증한다. Args: 클래스 값. Returns: 투영한 수치. */
function classQuality(value: unknown): ClassQuality {
  const item = object(value);
  if (!count(item.support) || !count(item.tp) || !count(item.fp) || !count(item.fn) || item.tp > item.support
      || !ratio(item.precision) || !ratio(item.recall) || !ratio(item.f1)) throw invalid();
  return { support: item.support, tp: item.tp, fp: item.fp, fn: item.fn, precision: item.precision, recall: item.recall, f1: item.f1 };
}

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
  for (const label of subtypeLabels) perClass[label] = classQuality(rawClasses[label]);
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

type LockedQuality = NonNullable<RunSummary['locked_quality']>;
type LockedMetric = NonNullable<LockedQuality['metrics']['category']>;
type LockedSegment = LockedMetric['segments'][string];
// 서버 summary.LOCKED_ROWS와 같은 지표별 segment·집계 키.
const lockedRows = {
  category: { all: ['accuracy', 'macro_f1'] },
  urgency: { all: ['accuracy', 'macro_f1', 'non_none_recall', 'critical_missed'] },
  secondary: { all: ['accuracy', 'composite_macro_f1', 'invented'] },
  return_subtype: { all: ['accuracy', 'macro_f1', 'null_predictions'] },
  provided_info: { 'scope=FULL': ['precision', 'recall', 'f1', 'exact_match'], 'scope=PRECISION_ONLY': ['precision'] },
} as const satisfies Record<keyof LockedQuality['metrics'], Record<string, readonly string[]>>;
const lockedMetricKeys = Object.keys(lockedRows) as (keyof typeof lockedRows)[];
const itemKeys: Record<string, readonly string[]> = { 'scope=FULL': ['fn', 'fp', 'tp'], 'scope=PRECISION_ONLY': ['fp', 'tp'] };
const optionalCount = (value: unknown): value is number | null => value === null || count(value);

/** 한 채점 범위의 분모·분포·집계를 검증한다. Args: 범위 값·집계 키·항목 키. Returns: 투영한 범위. */
function lockedSegment(value: unknown, keys: readonly string[], items: readonly string[] | undefined): LockedSegment {
  const data = object(value);
  const total = data.target_count;
  if (!count(data.locked_denominator) || total !== data.locked_denominator) throw invalid();
  const outcomes = object(data.outcomes);
  if (!exactKeys(outcomes, outcomeKeys) || outcomeKeys.some(key => !count(outcomes[key]))
      || outcomeKeys.reduce((sum, key) => sum + (outcomes[key] as number), 0) !== total) throw invalid();
  const excluded = object(data.excluded_by_reason);
  if (Object.values(excluded).some(v => !count(v))) throw invalid();
  const rawValues = object(data.values);
  if (!exactKeys(rawValues, keys)) throw invalid();
  const values: LockedSegment['values'] = {};
  for (const key of keys) {
    const item = object(rawValues[key]);
    if (!measurement(item.value) || !optionalCount(item.numerator) || !optionalCount(item.denominator) || !optionalCount(item.support)
        || item.numerator !== null && item.denominator !== null && item.numerator > item.denominator) throw invalid();
    values[key] = { value: item.value, numerator: item.numerator, denominator: item.denominator, support: item.support };
  }
  const rawClasses = object(data.per_class);
  const perClass = Object.fromEntries(Object.entries(rawClasses).map(([label, item]) => [label, classQuality(item)]));
  let projected: LockedSegment['items'] = null;
  if (items) {
    const raw = object(data.items);
    if (!exactKeys(raw, items) || items.some(key => !count(raw[key])) || Object.keys(perClass).length) throw invalid();
    projected = Object.fromEntries(items.map(key => [key, raw[key]])) as LockedSegment['items'];
  } else if (data.items !== null) throw invalid();
  return { locked_denominator: data.locked_denominator, target_count: total, outcomes: Object.fromEntries(outcomeKeys.map(key => [key, outcomes[key]])) as LockedSegment['outcomes'],
    excluded_by_reason: { ...excluded } as Record<string, number>, values, per_class: perClass, items: projected };
}

/** 지표 하나의 잠금 저장 판본을 검증한다. Args: 지표 값·segment 사양. Returns: 투영한 지표 또는 미저장(null). */
function lockedMetric(value: unknown, spec: Record<string, readonly string[]>): LockedMetric | null {
  if (value === null) return null;
  const data = object(value);
  const scored = data.scored_target_count;
  if (typeof data.scoring_version !== 'string' || !data.scoring_version || data.scoring_version.length > 64
      || !count(data.stored_scoring_version_count) || data.stored_scoring_version_count < 1
      || typeof data.stored_at !== 'string' || !Number.isFinite(Date.parse(data.stored_at))
      || typeof data.gold_version !== 'string' || !data.gold_version
      || typeof data.gold_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(data.gold_sha256)
      || typeof data.run_status_at_scoring !== 'string' || !Object.hasOwn(statusLabels, data.run_status_at_scoring)
      || !count(data.target_count) || !count(scored) || scored > data.target_count) throw invalid();
  const raw = object(data.segments);
  if (!exactKeys(raw, Object.keys(spec))) throw invalid();
  const segments: LockedMetric['segments'] = {};
  for (const [name, keys] of Object.entries(spec)) segments[name] = lockedSegment(raw[name], keys, itemKeys[name]);
  // provided_info 두 범위는 합해 보고하지 않지만 채점 대상 수는 두 범위의 합이다.
  if (Object.values(segments).reduce((sum, segment) => sum + segment.target_count, 0) !== scored) throw invalid();
  return {
    scoring_version: data.scoring_version, stored_scoring_version_count: data.stored_scoring_version_count,
    stored_at: data.stored_at, gold_version: data.gold_version, gold_sha256: data.gold_sha256,
    run_status_at_scoring: data.run_status_at_scoring as LockedMetric['run_status_at_scoring'],
    target_count: data.target_count, scored_target_count: scored, segments,
  };
}

type OutcomeQuality = NonNullable<LockedQuality['outcomes']>;

/** 형식 위반율·결과별 건수(KAN-148 2차)를 검증한다. 율은 SCHEMA_VIOLATION / (VALID + SCHEMA_VIOLATION)과 같아야 한다. Args: 선택 필드 값. Returns: 투영한 값 또는 미저장(null). */
function outcomeQuality(value: unknown): OutcomeQuality | null {
  if (value === undefined || value === null) return null;
  const data = object(value);
  const outcomes = object(data.outcomes);
  if (typeof data.scoring_version !== 'string' || !data.scoring_version || data.scoring_version.length > 64
      || !count(data.stored_scoring_version_count) || data.stored_scoring_version_count < 1
      || typeof data.stored_at !== 'string' || !Number.isFinite(Date.parse(data.stored_at))
      || !exactKeys(outcomes, outcomeKeys) || outcomeKeys.some(key => !count(outcomes[key]))) throw invalid();
  const violations = outcomes.SCHEMA_VIOLATION as number;
  const received = (outcomes.VALID as number) + violations;
  if (data.schema_violations !== violations || data.received !== received
      || data.schema_violation_rate !== (received ? violations / received : null)) throw invalid();
  return {
    scoring_version: data.scoring_version, stored_scoring_version_count: data.stored_scoring_version_count,
    stored_at: data.stored_at, schema_violation_rate: data.schema_violation_rate as number | null,
    schema_violations: violations, received,
    outcomes: Object.fromEntries(outcomeKeys.map(key => [key, outcomes[key]])) as OutcomeQuality['outcomes'],
  };
}

type StageMetricBlock = NonNullable<RunSummary['stage_metrics']>[number];
const stageIdentifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_.=:/-]{1,128}$/.test(value);
const stages = ['search', 'draft', 'rfp'] as const;

/** 단계별 채점 행(search·draft·rfp)을 검증해 키·값 그대로 투영한다. Args: 선택 필드 값. Returns: 블록 목록(없으면 빈 목록). */
function stageMetrics(value: unknown): StageMetricBlock[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw invalid();
  const seen = new Set<string>();
  return value.map(raw => {
    const block = object(raw);
    if (!stages.includes(block.stage as typeof stages[number]) || seen.has(block.stage as string)
        || typeof block.scoring_version !== 'string' || !block.scoring_version
        || !count(block.stored_scoring_version_count) || block.stored_scoring_version_count < 1
        || typeof block.stored_at !== 'string' || !Number.isFinite(Date.parse(block.stored_at))
        || !Array.isArray(block.items)) throw invalid();
    seen.add(block.stage as string);
    const items = block.items.map(rawItem => {
      const item = object(rawItem);
      const details = object(item.details);
      if (!stageIdentifier(item.metric_key) || !stageIdentifier(item.segment)
          || !(item.class_label === null || stageIdentifier(item.class_label))
          || !(item.value === null || typeof item.value === 'number' && Number.isFinite(item.value))
          || !optionalCount(item.support)
          || !(item.threshold === null || typeof item.threshold === 'number' && Number.isFinite(item.threshold))
          || !(item.comparison === null || item.comparison === '>=' || item.comparison === '<=' || item.comparison === '=')
          || !(item.passed === null || typeof item.passed === 'boolean') || item.quality_gate_evaluated !== false
          || Object.keys(details).some(key => !['tp', 'fp', 'fn'].includes(key) || !count(details[key]))) throw invalid();
      return {
        metric_key: item.metric_key, segment: item.segment, class_label: item.class_label as string | null,
        value: item.value as number | null, support: item.support, threshold: item.threshold as number | null,
        comparison: item.comparison as StageMetricBlock['items'][number]['comparison'], passed: item.passed as boolean | null,
        details: { ...details } as StageMetricBlock['items'][number]['details'], quality_gate_evaluated: false as const,
      };
    });
    return { stage: block.stage as StageMetricBlock['stage'], scoring_version: block.scoring_version,
      stored_scoring_version_count: block.stored_scoring_version_count, stored_at: block.stored_at, items };
  });
}

type SummaryExperiment = NonNullable<RunSummary['experiment']>;
const optionalText = (value: unknown): value is string | null => value === null || typeof value === 'string' && value.length <= 128;

/** 실행의 실험 설정(KAN-90 5차)을 검증한다. knobs는 문자열·유한수·bool 값만 받는다. Args: 선택 필드 값. Returns: 투영한 설정 또는 미제공(null). */
function experimentConfig(value: unknown): SummaryExperiment | null {
  if (value === undefined || value === null) return null;
  const data = object(value);
  const knobs = object(data.knobs);
  if (typeof data.experiment_id !== 'string' || !isJobId(data.experiment_id) || typeof data.name !== 'string' || !data.name || data.name.length > 120
      || !(data.purpose === 'smoke' || data.purpose === 'rule_replay' || data.purpose === 'pipeline')
      || !(data.stage === null || data.stage === 'classification' || stages.includes(data.stage as typeof stages[number]))
      || typeof data.model_id !== 'string' || !data.model_id || !optionalText(data.prompt_version)
      || !optionalText(data.input_builder_version) || !optionalText(data.index_version)
      || typeof data.config_hash !== 'string' || !/^[0-9a-f]{64}$/.test(data.config_hash)
      || !(data.data_split === null || data.data_split === 'train' || data.data_split === 'valid' || data.data_split === 'test')
      || !(data.data_origin === 'synthetic' || data.data_origin === 'demo' || data.data_origin === 'real')
      || Object.values(knobs).some(v => !(typeof v === 'string' || typeof v === 'boolean' || typeof v === 'number' && Number.isFinite(v)))) throw invalid();
  return {
    experiment_id: data.experiment_id, name: data.name, purpose: data.purpose, stage: data.stage as SummaryExperiment['stage'],
    knobs: { ...knobs } as SummaryExperiment['knobs'], model_id: data.model_id, prompt_version: data.prompt_version,
    input_builder_version: data.input_builder_version, index_version: data.index_version, config_hash: data.config_hash,
    data_split: data.data_split, data_origin: data.data_origin,
  };
}

/** 잠금 정답 품질을 검증해 투영한다. Args: 선택 필드 값. Returns: 검증한 품질 또는 미연결(null). */
function lockedQuality(value: unknown): LockedQuality | null {
  if (value === undefined || value === null) return null;
  const data = object(value);
  if (typeof data.bundle_id !== 'string' || !data.bundle_id || data.attempt !== 'first' || data.quality_gate_evaluated !== false) throw invalid();
  const raw = object(data.metrics);
  if (!exactKeys(raw, lockedMetricKeys)) throw invalid();
  const metrics = Object.fromEntries(lockedMetricKeys.map(key => [key, lockedMetric(raw[key], lockedRows[key])])) as LockedQuality['metrics'];
  return { bundle_id: data.bundle_id, attempt: 'first', quality_gate_evaluated: false, metrics, outcomes: outcomeQuality(data.outcomes) };
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

/** 검증한 v2 응답이 잠금 정답 경로(비합성 실행)인지 판별한다. smoke 경로는 비교 키를 항상 주고 잠금 품질을 주지 않는다. Args: 검증한 요약. Returns: 잠금 경로 여부. */
export const isLockedPath = (summary: RunSummary) => summary.locked_quality !== null || summary.comparison_key === null;

/** 모의 응답을 우회하고 현재 세션으로 집계를 읽는다. Args: 실행 ID·취소 신호. Returns: 검증·투영한 집계. */
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
  const synthetic = syntheticQuality(data.synthetic_quality);
  const locked = lockedQuality(data.locked_quality);
  const stored = locked ? Object.values(locked.metrics).filter(metric => metric !== null).length : 0;
  // 연결 여부와 사유는 저장 지표 수에서 다시 계산한 값과 같아야 한다.
  const available = stored === lockedMetricKeys.length;
  const reason = available ? null : stored ? 'GOLD_PARTIAL' : 'GOLD_NOT_CONNECTED';
  if (typeof data.run_id !== 'string' || data.run_id.toLowerCase() !== runId.toLowerCase()
      || data.report_version !== 'cs-run-summary-v2'
      || !(data.origin === 'ACCEPT' || data.origin === 'IMPORT')
      || !(data.data_origin === 'synthetic' || data.data_origin === 'demo' || data.data_origin === 'real')
      || data.quality_available !== available || data.quality_unavailable_reason !== reason || synthetic && locked
      || typeof data.status !== 'string' || !Object.hasOwn(statusLabels, data.status)
      || typeof data.generated_at !== 'string' || !Number.isFinite(Date.parse(data.generated_at))
      || !count(data.target_count) || !count(data.targets_without_first_attempt) || data.targets_without_first_attempt > data.target_count) throw invalid();
  return {
    report_version: data.report_version, run_id: data.run_id, origin: data.origin, status: data.status as RunSummary['status'],
    generated_at: data.generated_at, data_origin: data.data_origin, quality_available: available,
    quality_unavailable_reason: reason, target_count: data.target_count,
    targets_without_first_attempt: data.targets_without_first_attempt,
    first: attempt(data.first), retry: attempt(data.retry), all: attempt(data.all),
    synthetic_quality: synthetic, locked_quality: locked,
    comparison_key: comparisonKey(data.comparison_key),
    stage_metrics: stageMetrics(data.stage_metrics),
    experiment: experimentConfig(data.experiment),
  };
}
