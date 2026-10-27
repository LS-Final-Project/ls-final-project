-- KAN-108: resolution cases registered by a C/S agent after a ticket is closed.
-- Provided cases keep these columns null. Local bootstrap applies this as postgres.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

-- The accepted department result the case was written from, and who registered it.
ALTER TABLE cs.resolution_cases
    ADD COLUMN IF NOT EXISTS dept_result_id uuid REFERENCES cs.dept_results(id),
    ADD COLUMN IF NOT EXISTS registered_by uuid;

-- Widen the audit list only while it lacks resolution cases, so a re-run never narrows it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'audit_events_entity_type_check'
          AND conrelid = 'cs.audit_events'::regclass
          AND pg_get_constraintdef(oid) LIKE '%resolution_case%'
    ) THEN
        ALTER TABLE cs.audit_events DROP CONSTRAINT IF EXISTS audit_events_entity_type_check;
        ALTER TABLE cs.audit_events ADD CONSTRAINT audit_events_entity_type_check CHECK (
            entity_type IN (
                'ticket', 'ticket_unit_link', 'ticket_workflow', 'dept_result', 'resolution_case'
            )
        );
    END IF;
END
$$;

-- Cases are evidence for later tickets and repeat-issue candidates: the service never
-- removes them.
REVOKE DELETE, TRUNCATE ON cs.resolution_cases FROM cs_app;

COMMIT;
