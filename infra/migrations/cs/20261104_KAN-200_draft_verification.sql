-- KAN-200: append-only per-draft verification, including failed calls and model usage.
-- Apply after C19 (attempt usage) and C18 (worker role), before deploying the KAN-200 worker.
-- Re-runnable; only the owning migrator creates the table. Runtime roles cannot rewrite history.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.ticket_draft_verifications (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    draft_id uuid NOT NULL REFERENCES cs.ticket_drafts(id),
    job_id uuid,
    attempt_number integer NOT NULL CHECK (attempt_number IN (1, 2)),
    outcome text NOT NULL CHECK (outcome IN ('VALID', 'SCHEMA_VIOLATION', 'TRANSPORT_FAILURE', 'TIMEOUT', 'HELD')),
    sentences jsonb NOT NULL CHECK (jsonb_typeof(sentences) = 'array'),
    input_version text NOT NULL,
    input_sha256 text CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    model_version text,
    requested_model text,
    prompt_version text NOT NULL,
    instruction_fingerprint text CHECK (instruction_fingerprint ~ '^[0-9a-f]{16}$'),
    error_code text,
    call_attempts integer CHECK (call_attempts IS NULL OR call_attempts >= 1),
    latency_ms integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
    input_tokens integer CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens integer CHECK (output_tokens IS NULL OR output_tokens >= 0),
    thought_tokens integer CHECK (thought_tokens IS NULL OR thought_tokens >= 0),
    cached_input_tokens integer CHECK (cached_input_tokens IS NULL OR cached_input_tokens >= 0),
    total_tokens integer CHECK (total_tokens IS NULL OR total_tokens >= 0),
    cost_usd numeric(14, 6) CHECK (cost_usd IS NULL OR cost_usd >= 0),
    pricing_version text,
    provenance_kind text NOT NULL DEFAULT 'ai_draft' CHECK (provenance_kind = 'ai_draft'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (cost_usd IS NULL OR pricing_version IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ticket_draft_verifications_draft_idx
    ON cs.ticket_draft_verifications(draft_id, created_at, id);
REVOKE ALL ON cs.ticket_draft_verifications FROM cs_app, cs_worker;
GRANT SELECT ON cs.ticket_draft_verifications TO cs_app;
GRANT SELECT, INSERT ON cs.ticket_draft_verifications TO cs_worker;
COMMIT;
