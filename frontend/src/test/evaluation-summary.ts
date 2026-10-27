import type { RunSummary } from '../api/evaluation-summary';

/** 실제 자료 없는 집계 화면용 예시를 만든다. Args: 없음. Returns: 독립 합성 응답. */
export function summaryFixture(): RunSummary {
  const full = { reported_count: 3, missing_count: 0, known_sum: 21, total: 21 };
  const partial = { reported_count: 2, missing_count: 1, known_sum: 6, total: null };
  const absent = { reported_count: 0, missing_count: 0, known_sum: null, total: null };
  const first = {
    stored_attempt_count: 3, unfinished_attempt_count: 0,
    stored_raw_response_count: 2, schema_violation_count: 1, schema_violation_rate: 0.5,
    incomplete_evidence_attempt_count: 0,
    cost_usd: { reported_count: 0, missing_count: 3, known_sum: null, total: null, pricing_version_count: 0 },
    tokens: { input_tokens: {...full}, output_tokens: {...partial}, thought_tokens: {...absent, missing_count: 3}, cached_input_tokens: {...full, known_sum: 0, total: 0}, total_tokens: {...partial, known_sum: 20} },
    latency_ms: { reported_count: 3, missing_count: 0, p50: 5, p95: 8 },
  };
  return structuredClone({
    report_version: 'cs-run-summary-v1', run_id: '22222222-2222-4222-8222-222222222222',
    status: 'PARTIAL', generated_at: '2026-10-27T10:00:00Z', data_origin: 'synthetic',
    quality_available: false, quality_unavailable_reason: 'GOLD_NOT_CONNECTED',
    target_count: 3, targets_without_first_attempt: 0, first, all: first, synthetic_quality: null,
    retry: { stored_attempt_count: 0, unfinished_attempt_count: 0, stored_raw_response_count: 0,
      schema_violation_count: 0, schema_violation_rate: null, incomplete_evidence_attempt_count: 0,
      cost_usd: { reported_count: 0, missing_count: 0, known_sum: null, total: null, pricing_version_count: 0 },
      tokens: { input_tokens: absent, output_tokens: absent, thought_tokens: absent, cached_input_tokens: absent, total_tokens: absent },
      latency_ms: { reported_count: 0, missing_count: 0, p50: null, p95: null } },
  });
}

/** 합성 품질 저장 행을 투영한 예시를 만든다. Args: 없음. Returns: 실제 정답·사례가 없는 독립 값. */
export function syntheticQualityFixture(): NonNullable<RunSummary['synthetic_quality']> {
  return structuredClone({
    metric: 'return_subtype', attempt: 'first', data_origin: 'synthetic', quality_gate_evaluated: false,
    scoring_version: 'syn-v2', stored_scoring_version_count: 2, stored_at: '2026-10-27T11:00:00Z',
    gold_version: null, gold_sha256: 'a'.repeat(64), run_status_at_scoring: 'PARTIAL',
    target_count: 3, scored_target_count: 2, correct: 1, accuracy: 0.5, macro_f1: 0.5,
    null_predictions: 1,
    outcomes: { HELD: 0, NOT_STARTED: 0, SCHEMA_VIOLATION: 0, TIMEOUT: 0, TRANSPORT_FAILURE: 0, VALID: 2 },
    per_class: {
      DEFECT: { support: 1, tp: 1, fp: 0, fn: 0, precision: 1, recall: 1, f1: 1 },
      NON_DEFECT: { support: 1, tp: 0, fp: 0, fn: 1, precision: 0, recall: 0, f1: 0 },
    },
  } as const);
}
