-- KAN-115: append-only AI drafts of an improvement request's content and department questions,
-- including failed calls and model usage. Apply after C6 (improvement requests), before turning on
-- CS_IMPROVEMENT_DRAFT_ENABLED in the C/S service. Re-runnable; only the owning migrator creates
-- the table. The service adds rows and never edits them. A draft never changes the request: a
-- person applies it through the normal content update, which makes a new request version.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.improvement_ai_drafts (
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES cs.improvement_requests(id),
    -- The request version the input was read from. The draft is stale once the request changes.
    request_version integer NOT NULL CHECK (request_version >= 1),
    outcome text NOT NULL CHECK (outcome IN ('VALID', 'INVALID_OUTPUT', 'PROVIDER_ERROR')),
    error_code text CHECK (error_code IS NULL OR error_code ~ '^CS_[A-Z_]+$'),
    -- The validated model output (backend/engine/cs/improvement_drafting.ImprovementDraft).
    draft jsonb CHECK (draft IS NULL OR jsonb_typeof(draft) = 'object'),
    input_version text NOT NULL,
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    prompt_version text NOT NULL,
    requested_model text NOT NULL,
    model_version text,
    requested_by uuid NOT NULL,
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
    CHECK ((outcome = 'VALID') = (draft IS NOT NULL)),
    CHECK ((outcome = 'VALID') = (error_code IS NULL)),
    CHECK (cost_usd IS NULL OR pricing_version IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS improvement_ai_drafts_request_idx
    ON cs.improvement_ai_drafts(request_id, created_at DESC, id DESC);
-- One VALID draft per request input and prompt version, even when two instances create it at once.
CREATE UNIQUE INDEX IF NOT EXISTS improvement_ai_drafts_valid_once
    ON cs.improvement_ai_drafts(request_id, input_sha256, prompt_version) WHERE outcome = 'VALID';
REVOKE ALL ON cs.improvement_ai_drafts FROM cs_app;
GRANT SELECT, INSERT ON cs.improvement_ai_drafts TO cs_app;
COMMIT;
