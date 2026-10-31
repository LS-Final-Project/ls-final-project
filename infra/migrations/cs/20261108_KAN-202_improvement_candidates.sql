-- KAN-202: saved improvement candidates and the audit of a collection run.
-- A candidate is a DRAFT for a person to review (permissions.md 2.2). The four columns keep why
-- it was raised, so a later rule change does not rewrite the reason. Apply after C6 and C14
-- (audit entity list), and before the code: the request detail reads these columns.
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

ALTER TABLE cs.improvement_requests
    ADD COLUMN IF NOT EXISTS candidate_reason_code text,
    ADD COLUMN IF NOT EXISTS candidate_reached_at timestamptz,
    ADD COLUMN IF NOT EXISTS candidate_rules_version text,
    ADD COLUMN IF NOT EXISTS candidate_as_of timestamptz;

-- One saved candidate per group, so a repeated collection adds nothing.
CREATE UNIQUE INDEX IF NOT EXISTS improvement_requests_candidate_group
    ON cs.improvement_requests (product_code, fault_id, data_origin)
    WHERE origin = 'SYSTEM_CANDIDATE' AND request_type = 'FAULT';

-- Widen the audit list only while it lacks the new value, so a re-run never narrows it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'audit_events_entity_type_check'
          AND conrelid = 'cs.audit_events'::regclass
          AND pg_get_constraintdef(oid) LIKE '%improvement_candidate_run%'
    ) THEN
        ALTER TABLE cs.audit_events DROP CONSTRAINT IF EXISTS audit_events_entity_type_check;
        ALTER TABLE cs.audit_events ADD CONSTRAINT audit_events_entity_type_check CHECK (
            entity_type IN (
                'ticket', 'ticket_unit_link', 'ticket_workflow', 'dept_result',
                'resolution_case', 'improvement_request', 'condition_comparison',
                'ticket_analysis', 'improvement_candidate_run'
            )
        );
    END IF;
END
$$;

COMMIT;
