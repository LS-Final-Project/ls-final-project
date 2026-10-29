-- KAN-117: structured action scope (which units an action applies to) and the record of each
-- delivery round (first send, re-request, recurrence reopening). Apply after
-- 20261028_KAN-112_improvement_requests.sql. Local bootstrap applies this as postgres.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

-- What an action applies to, so recurrence on applied units can be told apart from units that
-- never got the fix (2026-10-29 decision). OTHER (the default) means a person must judge.
ALTER TABLE cs.request_actions
    ADD COLUMN IF NOT EXISTS scope_kind text NOT NULL DEFAULT 'OTHER'
        CHECK (scope_kind IN ('FIRMWARE_VERSION', 'SHIPPED_FROM', 'LOT', 'OTHER')),
    ADD COLUMN IF NOT EXISTS scope_value text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'request_actions_scope_value'
          AND conrelid = 'cs.request_actions'::regclass
    ) THEN
        -- A structured scope needs its value; OTHER has none.
        ALTER TABLE cs.request_actions ADD CONSTRAINT request_actions_scope_value
            CHECK ((scope_kind = 'OTHER') = (scope_value IS NULL));
    END IF;
END
$$;

-- One row per delivery round. The previous round's follow-up and completion are copied here
-- when a recurrence reopens a completed request, so the request row can hold the new round.
CREATE TABLE IF NOT EXISTS cs.request_rounds (
    request_id uuid NOT NULL REFERENCES cs.improvement_requests(id),
    round integer NOT NULL CHECK (round > 0),
    kind text NOT NULL CHECK (kind IN ('FIRST', 'REQUEST_AGAIN', 'RECURRENCE')),
    opened_at timestamptz NOT NULL,
    opened_by uuid NOT NULL,
    candidate_index integer CHECK (candidate_index IS NULL OR candidate_index > 0),
    as_of timestamptz,
    new_evidence_note text,
    previous_completion jsonb,
    PRIMARY KEY (request_id, round),
    CHECK ((kind = 'FIRST') = (round = 1))
);

REVOKE UPDATE, DELETE, TRUNCATE ON cs.request_rounds FROM cs_app;

COMMIT;
