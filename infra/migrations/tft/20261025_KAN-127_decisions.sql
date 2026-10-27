\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
CREATE TABLE IF NOT EXISTS tft.decision_commands (
    actor_user_id uuid NOT NULL,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    operation text NOT NULL,
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    payload jsonb NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_user_id, project_id, operation, idempotency_key)
);
CREATE TABLE IF NOT EXISTS tft.decision_history (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    action text NOT NULL CHECK (action IN ('COMPOSE','CONFIRM','REJECT','REOPEN')),
    actor_user_id uuid NOT NULL,
    reason text NOT NULL CHECK (length(trim(reason)) > 0),
    project_version integer NOT NULL,
    composition_version integer,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, project_version)
);
GRANT SELECT, INSERT ON tft.decision_commands, tft.decision_history TO tft_app;
REVOKE UPDATE, DELETE, TRUNCATE ON tft.decision_commands, tft.decision_history FROM tft_app;
COMMIT;
