-- AI interpretation of a linked customer reply (KAN-230, engine KAN-109), append-only, including
-- failed calls and model usage. Same shape as C28 (ticket_translations). Made only when a C/S agent
-- asks for it. It changes no ticket state, analysis field or correction.
-- Apply after C30 (reply subject/body), before turning on CS_REPLY_ANALYSIS_ENABLED in the C/S service.
-- Re-runnable; only the owning migrator creates the table. The service adds rows and never edits them.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.ticket_reply_analyses (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    message_id uuid NOT NULL REFERENCES cs.ticket_customer_replies(message_id),
    outcome text NOT NULL CHECK (outcome IN ('VALID', 'INVALID_OUTPUT', 'PROVIDER_ERROR')),
    error_code text CHECK (error_code IS NULL OR error_code ~ '^CS_[A-Z_]+$'),
    -- The validated interpretation (backend/engine/cs/reply.py ReplyInterpretation).
    interpretation jsonb CHECK (interpretation IS NULL OR jsonb_typeof(interpretation) = 'object'),
    -- What it would change in the draft input (ReplyUpdate). Applying it stays a person's step.
    reply_update jsonb CHECK (reply_update IS NULL OR jsonb_typeof(reply_update) = 'object'),
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
    CHECK ((outcome = 'VALID') = (interpretation IS NOT NULL)),
    CHECK ((outcome = 'VALID') = (reply_update IS NOT NULL)),
    CHECK ((outcome = 'VALID') = (error_code IS NULL)),
    CHECK (cost_usd IS NULL OR pricing_version IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ticket_reply_analyses_reply_idx
    ON cs.ticket_reply_analyses(message_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ticket_reply_analyses_ticket_idx
    ON cs.ticket_reply_analyses(ticket_id, created_at DESC, id DESC);
-- One VALID analysis per reply, input and prompt version, even when two instances make it at once.
CREATE UNIQUE INDEX IF NOT EXISTS ticket_reply_analyses_valid_once
    ON cs.ticket_reply_analyses(message_id, input_sha256, prompt_version) WHERE outcome = 'VALID';
REVOKE ALL ON cs.ticket_reply_analyses FROM cs_app;
GRANT SELECT, INSERT ON cs.ticket_reply_analyses TO cs_app;
COMMIT;
