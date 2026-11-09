-- AI help on a customer reply draft (KAN-251), append-only, including failed calls and model usage:
-- a draft from an accepted department answer (DEPT_DRAFT), a Korean translation of a draft (TO_KO)
-- and a customer-language draft from the agent's Korean edits (FROM_KO). Same shape as C28
-- (ticket_translations). Nothing here is sent; the agent puts it in the reply box and approves the send.
-- Apply after C1 (tickets) and C4 (dept_results), before turning on CS_DEPT_REPLY_DRAFT_ENABLED or
-- CS_REPLY_TRANSLATION_ENABLED in the C/S service.
-- Re-runnable; only the owning migrator creates the table. The service adds rows and never edits them.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.reply_draft_assists (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    kind text NOT NULL CHECK (kind IN ('DEPT_DRAFT', 'TO_KO', 'FROM_KO')),
    dept_result_id uuid REFERENCES cs.dept_results(id),
    outcome text NOT NULL CHECK (outcome IN ('VALID', 'INVALID_OUTPUT', 'PROVIDER_ERROR')),
    error_code text CHECK (error_code IS NULL OR error_code ~ '^CS_[A-Z_]+$'),
    output_text text CHECK (output_text IS NULL OR char_length(output_text) BETWEEN 1 AND 8000),
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
    CHECK ((kind = 'DEPT_DRAFT') = (dept_result_id IS NOT NULL)),
    CHECK ((outcome = 'VALID') = (output_text IS NOT NULL)),
    CHECK ((outcome = 'VALID') = (error_code IS NULL)),
    CHECK (cost_usd IS NULL OR pricing_version IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS reply_draft_assists_ticket_idx
    ON cs.reply_draft_assists(ticket_id, created_at DESC, id DESC);
-- One VALID result per ticket, kind, input and prompt version, even when two instances make it at once.
CREATE UNIQUE INDEX IF NOT EXISTS reply_draft_assists_valid_once
    ON cs.reply_draft_assists(ticket_id, kind, input_sha256, prompt_version) WHERE outcome = 'VALID';
REVOKE ALL ON cs.reply_draft_assists FROM cs_app;
GRANT SELECT, INSERT ON cs.reply_draft_assists TO cs_app;
COMMIT;
