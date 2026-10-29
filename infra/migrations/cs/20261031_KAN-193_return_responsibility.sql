-- KAN-193: widen the confirmed return from DEFECT/NON_DEFECT to what a person decides after
-- inspection: who is responsible, the cause type and the response. Resolution cases get the
-- same responsibility as an optional field.
-- Needs 20261030_KAN-193_return_subtype_confirmed.sql (C11) and
-- 20261028_KAN-108_resolution_cases.sql (C5). Re-runnable. Local bootstrap applies this as postgres.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

-- Cause types are the five resolution-case types; a transport damage is NON_MANUFACTURING
-- with a free-text reason, not a new cause type (decided 2026-10-29).
ALTER TABLE cs.ticket_workflows
    ADD COLUMN IF NOT EXISTS return_responsibility text
        CHECK (return_responsibility IN ('MANUFACTURING', 'NON_MANUFACTURING', 'UNDETERMINED')),
    ADD COLUMN IF NOT EXISTS return_cause_type text
        CHECK (return_cause_type IN ('SITE_CONDITION', 'HARDWARE', 'SETTING', 'WEAR_PART', 'FIRMWARE')),
    ADD COLUMN IF NOT EXISTS return_non_manufacturing_reason text
        CHECK (return_non_manufacturing_reason IS NULL
               OR length(return_non_manufacturing_reason) BETWEEN 1 AND 2000),
    ADD COLUMN IF NOT EXISTS return_response_note text
        CHECK (return_response_note IS NULL OR length(return_response_note) BETWEEN 1 AND 2000);

-- Rows confirmed under C11 keep their meaning: DEFECT was a manufacturing fault, NON_DEFECT
-- was not, and the reason becomes the response note. Only rows not yet carried over.
UPDATE cs.ticket_workflows
SET return_responsibility = CASE return_subtype_confirmed
        WHEN 'DEFECT' THEN 'MANUFACTURING' ELSE 'NON_MANUFACTURING' END,
    return_response_note = return_subtype_confirmed_reason
WHERE return_subtype_confirmed IS NOT NULL AND return_responsibility IS NULL;

-- The C11 reason column is no longer written; new confirmations keep it null.
ALTER TABLE cs.ticket_workflows DROP CONSTRAINT IF EXISTS ticket_workflows_return_subtype_confirmed_all_or_none;
ALTER TABLE cs.ticket_workflows DROP CONSTRAINT IF EXISTS ticket_workflows_return_confirmation_check;
ALTER TABLE cs.ticket_workflows ADD CONSTRAINT ticket_workflows_return_confirmation_check CHECK (
    -- A confirmation is a person's decision with its note and time, or nothing at all.
    (return_responsibility IS NULL) = (return_response_note IS NULL)
    AND (return_responsibility IS NULL) = (return_subtype_confirmed_by IS NULL)
    AND (return_responsibility IS NULL) = (return_subtype_confirmed_at IS NULL)
    AND (return_responsibility IS NOT NULL OR return_cause_type IS NULL)
    -- The subtype routing uses is derived from responsibility; UNDETERMINED confirms nothing.
    AND return_subtype_confirmed IS NOT DISTINCT FROM CASE return_responsibility
        WHEN 'MANUFACTURING' THEN 'DEFECT'
        WHEN 'NON_MANUFACTURING' THEN 'NON_DEFECT' END
    AND (return_non_manufacturing_reason IS NULL OR return_responsibility = 'NON_MANUFACTURING')
);

ALTER TABLE cs.resolution_cases
    ADD COLUMN IF NOT EXISTS responsibility text
        CHECK (responsibility IN ('MANUFACTURING', 'NON_MANUFACTURING', 'UNDETERMINED')),
    ADD COLUMN IF NOT EXISTS non_manufacturing_reason text
        CHECK (non_manufacturing_reason IS NULL
               OR length(non_manufacturing_reason) BETWEEN 1 AND 2000);
ALTER TABLE cs.resolution_cases DROP CONSTRAINT IF EXISTS resolution_cases_non_manufacturing_reason_scope;
ALTER TABLE cs.resolution_cases ADD CONSTRAINT resolution_cases_non_manufacturing_reason_scope CHECK (
    non_manufacturing_reason IS NULL OR responsibility = 'NON_MANUFACTURING'
);

COMMIT;
