-- KAN-102: append-only audit trail for C/S ticket creation and correction.
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.audit_events (
    id uuid PRIMARY KEY,
    entity_type text NOT NULL CHECK (entity_type IN ('ticket')),
    entity_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('create', 'update')),
    -- Version of the entity after this change.
    version integer NOT NULL CHECK (version > 0),
    -- Filled once KAN-78 provides the signed-in user. Null means not yet attributable.
    actor_user_id uuid,
    -- {"field": {"from": old, "to": new}} for updates, {"field": {"to": value}} for creation.
    changes jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entity_type, entity_id, version)
);
CREATE INDEX IF NOT EXISTS audit_events_entity
    ON cs.audit_events (entity_type, entity_id, occurred_at);

-- The service may add audit events but never rewrite or remove them.
REVOKE UPDATE, DELETE, TRUNCATE ON cs.audit_events FROM cs_app;

COMMIT;
