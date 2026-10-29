-- KAN-193: the return subtype a person confirmed after inspection, kept apart from what the
-- customer stated at intake (the LLM `return_subtype`, which never counts as confirmed).
-- LS engineers (2026-10-29): the return reason is decided by inspection, not at intake.
-- Needs 20261027_KAN-108_ticket_workflow.sql. Re-runnable. Local bootstrap applies this as postgres.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

ALTER TABLE cs.ticket_workflows
    ADD COLUMN IF NOT EXISTS return_subtype_confirmed text
        CHECK (return_subtype_confirmed IN ('DEFECT', 'NON_DEFECT')),
    ADD COLUMN IF NOT EXISTS return_subtype_confirmed_reason text
        CHECK (return_subtype_confirmed_reason IS NULL
               OR length(return_subtype_confirmed_reason) BETWEEN 1 AND 2000),
    ADD COLUMN IF NOT EXISTS return_subtype_confirmed_by uuid,
    ADD COLUMN IF NOT EXISTS return_subtype_confirmed_at timestamptz;

-- A confirmation is a person's decision with its reason and time, or nothing at all.
-- Named apart from the column's own value CHECK (ticket_workflows_return_subtype_confirmed_check),
-- which stays in place.
ALTER TABLE cs.ticket_workflows DROP CONSTRAINT IF EXISTS ticket_workflows_return_subtype_confirmed_all_or_none;
ALTER TABLE cs.ticket_workflows ADD CONSTRAINT ticket_workflows_return_subtype_confirmed_all_or_none CHECK (
    (return_subtype_confirmed IS NULL) = (return_subtype_confirmed_reason IS NULL)
    AND (return_subtype_confirmed IS NULL) = (return_subtype_confirmed_by IS NULL)
    AND (return_subtype_confirmed IS NULL) = (return_subtype_confirmed_at IS NULL)
);

COMMIT;
