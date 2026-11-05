-- KAN-107: AI analysis runs of a ticket (ai_draft) and human corrections (human_input).
-- Both tables only grow: a new run never edits an older one, and a correction never edits the
-- AI row it was based on. Apply after 20261028_KAN-114_condition_comparisons_writes.sql
-- (audit entity list). Local bootstrap applies this as postgres.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

-- One row per classification run of a ticket. job_id is null until the platform job runner
-- (KAN-69) is connected; synthetic rows in tests and demos also have no job.
CREATE TABLE IF NOT EXISTS cs.ticket_analyses (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    run_seq integer NOT NULL CHECK (run_seq > 0),
    job_id uuid,
    outcome text NOT NULL
        CHECK (outcome IN ('VALID', 'SCHEMA_VIOLATION', 'TRANSPORT_FAILURE', 'TIMEOUT', 'HELD')),
    -- The five classification fields exactly as validated. Only a VALID run has them.
    classification jsonb CHECK (classification IS NULL OR jsonb_typeof(classification) = 'object'),
    input_version text NOT NULL,
    input_sha256 text CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    model_version text,
    prompt_version text,
    error_code text,
    provenance_kind text NOT NULL DEFAULT 'ai_draft' CHECK (provenance_kind = 'ai_draft'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((outcome = 'VALID') = (classification IS NOT NULL)),
    UNIQUE (ticket_id, run_seq)
);

-- One row per reply draft run (three options). What a person edits and approves is kept by the
-- send record (KAN-110), never by rewriting this row.
CREATE TABLE IF NOT EXISTS cs.ticket_drafts (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    seq integer NOT NULL CHECK (seq > 0),
    -- The analysis run whose classification the draft was written from.
    analysis_id uuid REFERENCES cs.ticket_analyses(id),
    job_id uuid,
    outcome text NOT NULL
        CHECK (outcome IN ('VALID', 'SCHEMA_VIOLATION', 'TRANSPORT_FAILURE', 'TIMEOUT', 'HELD')),
    draft jsonb CHECK (draft IS NULL OR jsonb_typeof(draft) = 'object'),
    input_version text NOT NULL,
    input_sha256 text CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    model_version text,
    prompt_version text,
    error_code text,
    provenance_kind text NOT NULL DEFAULT 'ai_draft' CHECK (provenance_kind = 'ai_draft'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((outcome = 'VALID') = (draft IS NOT NULL)),
    UNIQUE (ticket_id, seq)
);

-- One row per human correction. `fields` holds only the fields this correction changed
-- (category_code, secondary_category, urgency_flag, provided_info). The latest value per
-- field wins over the AI value; a later AI run does not replace it.
CREATE TABLE IF NOT EXISTS cs.ticket_classification_corrections (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    seq integer NOT NULL CHECK (seq > 0),
    -- The AI run the person was looking at. Null only when no valid run existed.
    based_on_analysis_id uuid REFERENCES cs.ticket_analyses(id),
    fields jsonb NOT NULL CHECK (jsonb_typeof(fields) = 'object' AND fields <> '{}'::jsonb),
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
    corrected_by uuid NOT NULL,
    provenance_kind text NOT NULL DEFAULT 'human_input' CHECK (provenance_kind = 'human_input'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticket_id, seq)
);

-- Widen the audit list only while it lacks the new value, so a re-run never narrows it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'audit_events_entity_type_check'
          AND conrelid = 'cs.audit_events'::regclass
          AND pg_get_constraintdef(oid) LIKE '%ticket_analysis%'
    ) THEN
        ALTER TABLE cs.audit_events DROP CONSTRAINT IF EXISTS audit_events_entity_type_check;
        ALTER TABLE cs.audit_events ADD CONSTRAINT audit_events_entity_type_check CHECK (
            entity_type IN (
                'ticket', 'ticket_unit_link', 'ticket_workflow', 'dept_result',
                'resolution_case', 'improvement_request', 'condition_comparison',
                'ticket_analysis'
            )
        );
    END IF;
END
$$;

-- Append-only for the service account.
REVOKE UPDATE, DELETE, TRUNCATE ON cs.ticket_analyses, cs.ticket_drafts,
    cs.ticket_classification_corrections FROM cs_app;

COMMIT;
