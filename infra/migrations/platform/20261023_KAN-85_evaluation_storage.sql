-- KAN-85: evaluation storage (experiments, runs, cases, attempts, metrics, usage).
-- Denominators and calculations follow docs/evaluation.md; the storage contract is
-- contracts/platform/evaluations.md. Local bootstrap applies this as postgres.
-- Runtime users never execute DDL.
--
-- Credentials never enter this schema: no API key, OAuth/session token, Authorization
-- header, password or provider secret may be written to any column (including jsonb and
-- raw_output). Error columns hold a class name or code only, never a provider message.
-- Raw model output is stored only after masking (no customer text, serial values, PII or
-- gold answers) and is treated as access-restricted audit material.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;

-- One frozen experiment definition: dataset manifest + hash, split, gold version,
-- policy/prompt/model/index versions and the fixed target set. Rows are immutable;
-- a changed configuration is a new experiment (config_hash is unique).
CREATE TABLE IF NOT EXISTS evaluation.experiments (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    -- smoke: synthetic connection check. rule_replay: pure rules on reviewed inputs.
    -- pipeline: AI prediction feeding the full pipeline.
    purpose text NOT NULL CHECK (purpose IN ('smoke', 'rule_replay', 'pipeline')),
    schema_version text NOT NULL DEFAULT '1.0',
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    -- Evaluation records are derived from the dataset; never PROVIDED.
    derivation_kind text NOT NULL DEFAULT 'DERIVED'
        CHECK (derivation_kind IN ('DERIVED', 'SYSTEM')),
    -- Dataset manifest: sources, per-source data_origin, file hashes and counts.
    dataset_manifest jsonb NOT NULL CHECK (jsonb_typeof(dataset_manifest) = 'object'),
    dataset_hash text NOT NULL CHECK (dataset_hash ~ '^[0-9a-f]{64}$'),
    data_split text CHECK (data_split IN ('train', 'valid', 'test')),
    gold_version text,
    policy_version text,
    prompt_version text,
    -- Requested model ID. The served model_version is recorded per attempt.
    model_id text NOT NULL,
    index_version text,
    -- Version of build_input() (PHONE "Urgency:" removal etc.).
    input_builder_version text,
    -- Non-secret provider settings (max_output_tokens, location, ...). No keys or tokens.
    configuration jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(configuration) = 'object'),
    target_set_hash text NOT NULL CHECK (target_set_hash ~ '^[0-9a-f]{64}$'),
    -- SHA-256 over the canonical configuration; see the contract for the input list.
    config_hash text NOT NULL UNIQUE CHECK (config_hash ~ '^[0-9a-f]{64}$'),
    created_by_user_id uuid REFERENCES identity.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    -- Only the synthetic smoke check may run without a split and a gold version.
    CHECK (purpose = 'smoke' OR (data_split IS NOT NULL AND gold_version IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS experiments_created_at_id
    ON evaluation.experiments (created_at DESC, id DESC);
-- Test-split usage must be checked before a run starts (final single use only).
CREATE INDEX IF NOT EXISTS experiments_test_split
    ON evaluation.experiments (created_at) WHERE data_split = 'test';

-- The fixed target ID set of an experiment. target_ref is the dataset's external ID
-- (for example CSV1-000044). attributes hold reporting/exclusion flags such as channel,
-- language and priority_adjusted_by_agent; never the source text or gold answers.
CREATE TABLE IF NOT EXISTS evaluation.experiment_targets (
    experiment_id uuid NOT NULL REFERENCES evaluation.experiments(id),
    target_ref text NOT NULL,
    target_kind text NOT NULL,
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    attributes jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(attributes) = 'object'),
    PRIMARY KEY (experiment_id, target_ref)
);
-- A frozen experiment and its target set are never rewritten; register a new one instead.
REVOKE UPDATE, DELETE, TRUNCATE ON evaluation.experiments, evaluation.experiment_targets
    FROM platform_app;

-- One execution of an experiment. Status is the explicit end state; counts are the
-- denominators required by docs/evaluation.md.
CREATE TABLE IF NOT EXISTS evaluation.runs (
    id uuid PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES evaluation.experiments(id),
    -- Runtime job (KAN-90). The runtime schema is not defined yet, so no FK.
    job_id uuid,
    status text NOT NULL DEFAULT 'QUEUED' CHECK (status IN (
        'QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED', 'TIMED_OUT'
    )),
    needs_review boolean NOT NULL DEFAULT false,
    runner_kind text NOT NULL CHECK (runner_kind IN ('cloud_run_job', 'local', 'test')),
    -- Job execution name or host label. Never a credential.
    runner_ref text,
    requested_by_user_id uuid REFERENCES identity.users(id),
    provider text NOT NULL CHECK (provider IN ('fake', 'gemini', 'vertex', 'rules')),
    -- Scorer version used when this run was scored first. Re-scoring adds metrics rows
    -- with another scoring_version and never rewrites existing ones.
    scoring_version text NOT NULL,
    -- Git SHA of the evaluation code.
    code_version text,
    -- Null means cost was not computed (no price table).
    pricing_version text,
    requested_count integer NOT NULL DEFAULT 0 CHECK (requested_count >= 0),
    valid_output_count integer NOT NULL DEFAULT 0 CHECK (valid_output_count >= 0),
    held_count integer NOT NULL DEFAULT 0 CHECK (held_count >= 0),
    failed_count integer NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    -- {"reason": count} for targets excluded by their flags, e.g. priority_adjusted.
    excluded_counts jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(excluded_counts) = 'object'),
    -- Exception class or safe code only. Never a provider message.
    failure_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    UNIQUE (id, experiment_id),
    CHECK (started_at IS NULL OR started_at >= created_at),
    -- A run cancelled before it started has finished_at but no started_at.
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
    CHECK ((status IN ('QUEUED', 'RUNNING')) = (finished_at IS NULL)),
    CHECK (status <> 'QUEUED' OR started_at IS NULL),
    CHECK (status <> 'RUNNING' OR started_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS runs_experiment_created_at_id
    ON evaluation.runs (experiment_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS runs_active ON evaluation.runs (created_at)
    WHERE status IN ('QUEUED', 'RUNNING');

-- One target inside one run. The composite FKs guarantee the case belongs to the run's
-- experiment and that the target is part of that experiment's frozen target set.
CREATE TABLE IF NOT EXISTS evaluation.cases (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL,
    experiment_id uuid NOT NULL,
    target_ref text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN (
        'PENDING', 'VALID', 'INVALID_OUTPUT', 'HELD', 'FAILED', 'EXCLUDED'
    )),
    exclusion_reason text,
    -- Structured output after retries/fallback (codes only). Raw output lives in attempts.
    final_output jsonb,
    fallback_used boolean NOT NULL DEFAULT false,
    -- Gold label codes of this target at gold_version (codes only; no text, replies or
    -- PII). Stored so per-class scores can be reproduced for the frozen experiment.
    gold_labels jsonb CHECK (gold_labels IS NULL OR jsonb_typeof(gold_labels) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (run_id, target_ref),
    FOREIGN KEY (run_id, experiment_id) REFERENCES evaluation.runs(id, experiment_id),
    FOREIGN KEY (experiment_id, target_ref)
        REFERENCES evaluation.experiment_targets(experiment_id, target_ref),
    CHECK ((status = 'EXCLUDED') = (exclusion_reason IS NOT NULL)),
    CHECK ((status = 'PENDING') = (completed_at IS NULL))
);
CREATE INDEX IF NOT EXISTS cases_run_status ON evaluation.cases (run_id, status);

-- Raw record of every model/rule call: per step and per attempt. Append-only.
-- The first attempt's schema violation is never erased by a later valid attempt.
CREATE TABLE IF NOT EXISTS evaluation.case_attempts (
    id uuid PRIMARY KEY,
    case_id uuid NOT NULL REFERENCES evaluation.cases(id),
    step text NOT NULL CHECK (step IN (
        'classify', 'retrieve', 'rules', 'draft', 'reply_analysis',
        'condition_compare', 'dept_question', 'smoke'
    )),
    attempt integer NOT NULL CHECK (attempt >= 1),
    -- The attempt whose output became the case's final output for this step.
    is_final boolean NOT NULL DEFAULT false,
    outcome text NOT NULL CHECK (outcome IN (
        'VALID', 'SCHEMA_VIOLATION', 'TRANSPORT_FAILURE', 'TIMEOUT', 'HELD'
    )),
    provider text CHECK (provider IN ('fake', 'gemini', 'vertex', 'rules')),
    model_id text,
    -- Model version reported by the provider for this response.
    model_version text,
    finish_reasons text[] NOT NULL DEFAULT '{}',
    -- SHA-256 of the exact model input, so leakage audits can be reproduced without
    -- storing the input itself.
    input_sha256 text CHECK (input_sha256 IS NULL OR input_sha256 ~ '^[0-9a-f]{64}$'),
    -- Masked raw response text. Null when no response was received.
    raw_output text,
    -- Parsed structured output of this attempt, or null when parsing failed.
    parsed_output jsonb,
    -- Exception class name or HTTP status class only. Never a provider message.
    error_code text,
    http_status integer CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    latency_ms integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
    -- Null means the provider did not report the value.
    input_tokens integer CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens integer CHECK (output_tokens IS NULL OR output_tokens >= 0),
    thought_tokens integer CHECK (thought_tokens IS NULL OR thought_tokens >= 0),
    cached_input_tokens integer CHECK (cached_input_tokens IS NULL OR cached_input_tokens >= 0),
    total_tokens integer CHECK (total_tokens IS NULL OR total_tokens >= 0),
    -- Null when no price table is registered. cost requires pricing_version.
    cost_usd numeric(14, 6) CHECK (cost_usd IS NULL OR cost_usd >= 0),
    pricing_version text,
    UNIQUE (case_id, step, attempt),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK (cost_usd IS NULL OR pricing_version IS NOT NULL),
    CHECK (outcome <> 'TRANSPORT_FAILURE' OR raw_output IS NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS case_attempts_one_final_per_step
    ON evaluation.case_attempts (case_id, step) WHERE is_final;
-- Attempts are evidence. The service may add them but never rewrite or remove them.
REVOKE UPDATE, DELETE, TRUNCATE ON evaluation.case_attempts FROM platform_app;

-- Scores with their denominators, per run or per case, per scoring version. Append-only.
-- value null means not computable (for example schema violation rate with 0 raw
-- responses) and must never be reported as passed.
CREATE TABLE IF NOT EXISTS evaluation.metrics (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES evaluation.runs(id),
    -- Null for run-level metrics.
    case_id uuid REFERENCES evaluation.cases(id),
    scoring_version text NOT NULL,
    -- Metric identifier, e.g. category_macro_f1, critical_miss_count, hit_at_5.
    metric_key text NOT NULL,
    -- Reporting slice such as 'all', 'channel=PHONE', 'language=ko'.
    segment text NOT NULL DEFAULT 'all',
    -- Class for per-class precision/recall/F1 rows; null for aggregate rows.
    class_label text,
    value numeric,
    numerator integer CHECK (numerator IS NULL OR numerator >= 0),
    denominator integer CHECK (denominator IS NULL OR denominator >= 0),
    -- Sample count of the class or slice.
    support integer CHECK (support IS NULL OR support >= 0),
    -- Components such as {"tp":..,"fp":..,"fn":..} or confusion pairs.
    details jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details) = 'object'),
    -- Locked pass threshold (docs/evaluation.md). Null until KAN-82/95 lock it.
    threshold numeric,
    comparison text CHECK (comparison IS NULL OR comparison IN ('>=', '<=', '=')),
    -- Null means not judged. A judgement requires a value and a locked threshold.
    passed boolean,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (run_id, case_id, scoring_version, metric_key, segment, class_label),
    CHECK ((threshold IS NULL) = (comparison IS NULL)),
    CHECK (passed IS NULL OR (value IS NOT NULL AND threshold IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS metrics_run_key
    ON evaluation.metrics (run_id, scoring_version, metric_key);
REVOKE UPDATE, DELETE, TRUNCATE ON evaluation.metrics FROM platform_app;

-- Usage aggregated per run, step, provider and model: calls, tokens, latency and cost.
-- Derived from case_attempts at run end; attempts remain the source of truth.
CREATE TABLE IF NOT EXISTS evaluation.usage_summaries (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES evaluation.runs(id),
    step text NOT NULL,
    provider text NOT NULL CHECK (provider IN ('fake', 'gemini', 'vertex', 'rules')),
    -- Null for rule steps without a model.
    model_id text,
    -- Every real call counts, including duplicate executions of the same step.
    call_count integer NOT NULL CHECK (call_count >= 0),
    failed_call_count integer NOT NULL DEFAULT 0 CHECK (failed_call_count >= 0),
    input_tokens bigint CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens IS NULL OR output_tokens >= 0),
    thought_tokens bigint CHECK (thought_tokens IS NULL OR thought_tokens >= 0),
    cached_input_tokens bigint CHECK (cached_input_tokens IS NULL OR cached_input_tokens >= 0),
    total_tokens bigint CHECK (total_tokens IS NULL OR total_tokens >= 0),
    latency_ms_sum bigint CHECK (latency_ms_sum IS NULL OR latency_ms_sum >= 0),
    latency_ms_p50 integer CHECK (latency_ms_p50 IS NULL OR latency_ms_p50 >= 0),
    latency_ms_p95 integer CHECK (latency_ms_p95 IS NULL OR latency_ms_p95 >= 0),
    latency_ms_max integer CHECK (latency_ms_max IS NULL OR latency_ms_max >= 0),
    cost_usd numeric(14, 6) CHECK (cost_usd IS NULL OR cost_usd >= 0),
    pricing_version text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (run_id, step, provider, model_id),
    CHECK (failed_call_count <= call_count),
    CHECK (cost_usd IS NULL OR pricing_version IS NOT NULL)
);

COMMIT;
