-- Atomic response replay for department writes; no runtime or platform permissions added.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
CREATE TABLE IF NOT EXISTS tft.recommendation_commands (
    actor_user_id uuid NOT NULL,
    request_id uuid NOT NULL REFERENCES tft.staffing_requests(id),
    operation text NOT NULL,
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    payload jsonb NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_user_id, request_id, operation, idempotency_key)
);
ALTER TABLE tft.staffing_request_revisions
    DROP CONSTRAINT IF EXISTS staffing_request_revisions_action_check;
ALTER TABLE tft.staffing_request_revisions
    ADD CONSTRAINT staffing_request_revisions_action_check
    CHECK (action IN ('EDIT', 'ISSUE', 'RECOMMEND', 'VERIFY'));
REVOKE UPDATE, DELETE, TRUNCATE ON tft.recommendation_commands FROM tft_app;
GRANT SELECT, INSERT ON tft.recommendation_commands TO tft_app;
COMMIT;
