-- KAN-131: append-only Korean summaries of a ticket's original text, including failed calls and
-- model usage. Apply after C1 (tickets), before turning on CS_SUMMARY_ENABLED in the C/S service.
-- Re-runnable; only the owning migrator creates the table. The service adds rows and never edits them.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.ticket_summaries (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    outcome text NOT NULL CHECK (outcome IN ('VALID', 'INVALID_OUTPUT', 'PROVIDER_ERROR')),
    error_code text CHECK (error_code IS NULL OR error_code ~ '^CS_[A-Z_]+$'),
    summary_ko text CHECK (summary_ko IS NULL OR char_length(summary_ko) BETWEEN 1 AND 300),
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
    CHECK ((outcome = 'VALID') = (summary_ko IS NOT NULL)),
    CHECK ((outcome = 'VALID') = (error_code IS NULL)),
    CHECK (cost_usd IS NULL OR pricing_version IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ticket_summaries_ticket_idx
    ON cs.ticket_summaries(ticket_id, created_at DESC, id DESC);
-- One VALID summary per ticket text and prompt version, even when two instances create it at once.
CREATE UNIQUE INDEX IF NOT EXISTS ticket_summaries_valid_once
    ON cs.ticket_summaries(ticket_id, input_sha256, prompt_version) WHERE outcome = 'VALID';
REVOKE ALL ON cs.ticket_summaries FROM cs_app;
GRANT SELECT, INSERT ON cs.ticket_summaries TO cs_app;
COMMIT;
