-- KAN-186 (KAN-182 storage part, RFP API spec v1.3 §9): the rest of the A01 columns.
-- projects: strategic customer flag (sales input, a hypothesis; never an input to status,
--   grade, path rank or evaluation) and the integration responsibility of an assembly unit.
-- requirement_versions: the customer-specified item (designated_model stays from T7).
-- supplement_selections: the R&D check recorded with the choice (T10 added only the reason).
-- Only adds. Needs 20261029_KAN-182_rfp_response.sql (T7) and 20261102_KAN-186_rnd_check.sql (T10).
-- Re-runnable. tft.projects keeps the KAN-119 table-wide UPDATE for tft_app (T7 note).
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.projects
    ADD COLUMN IF NOT EXISTS strategic_customer boolean,
    ADD COLUMN IF NOT EXISTS strategic_customer_note text
        CHECK (strategic_customer_note IS NULL OR length(strategic_customer_note) BETWEEN 1 AND 200),
    ADD COLUMN IF NOT EXISTS strategic_customer_set_by uuid,
    ADD COLUMN IF NOT EXISTS strategic_customer_set_at timestamptz,
    ADD COLUMN IF NOT EXISTS integration_holder text NOT NULL DEFAULT 'UNRESOLVED'
        CHECK (integration_holder IN ('OWN', 'SINGLE_PARTNER', 'JOINT', 'UNRESOLVED')),
    ADD COLUMN IF NOT EXISTS integration_holder_candidates jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(integration_holder_candidates) = 'array'),
    ADD COLUMN IF NOT EXISTS integration_note text
        CHECK (integration_note IS NULL OR length(integration_note) BETWEEN 1 AND 1000),
    ADD COLUMN IF NOT EXISTS integration_set_by uuid,
    ADD COLUMN IF NOT EXISTS integration_set_at timestamptz;
-- Who set a value and when go together with the value being set.
ALTER TABLE tft.projects DROP CONSTRAINT IF EXISTS projects_strategic_customer_set_check;
ALTER TABLE tft.projects ADD CONSTRAINT projects_strategic_customer_set_check CHECK (
    (strategic_customer_set_by IS NULL) = (strategic_customer_set_at IS NULL)
    AND (strategic_customer IS NULL OR strategic_customer_set_by IS NOT NULL)
);
ALTER TABLE tft.projects DROP CONSTRAINT IF EXISTS projects_integration_set_check;
ALTER TABLE tft.projects ADD CONSTRAINT projects_integration_set_check CHECK (
    (integration_set_by IS NULL) = (integration_set_at IS NULL)
    AND (integration_holder = 'UNRESOLVED' OR integration_set_by IS NOT NULL)
);

ALTER TABLE tft.requirement_versions
    ADD COLUMN IF NOT EXISTS designated_manufacturer text,
    ADD COLUMN IF NOT EXISTS designated_procurement text
        CHECK (designated_procurement IN ('SPECIFIED_PROCUREMENT', 'ALTERNATIVE_PROPOSAL', 'UNDECIDED')),
    ADD COLUMN IF NOT EXISTS designated_clause_id uuid REFERENCES tft.clauses(id);
-- The specified-item details exist only with a designated model (spec §5.1: designated_item is
-- null when designated_model is null).
ALTER TABLE tft.requirement_versions DROP CONSTRAINT IF EXISTS requirement_versions_designated_item_check;
ALTER TABLE tft.requirement_versions ADD CONSTRAINT requirement_versions_designated_item_check CHECK (
    designated_model IS NOT NULL
    OR (designated_manufacturer IS NULL AND designated_procurement IS NULL
        AND designated_clause_id IS NULL)
);

ALTER TABLE tft.supplement_selections
    ADD COLUMN IF NOT EXISTS rnd_check text
        CHECK (rnd_check IN ('NOT_REQUIRED', 'PENDING', 'CONFIRMED_FEASIBLE',
                             'CONFIRMED_NOT_FEASIBLE', 'CONFIRMED_SCHEDULE_MISMATCH', 'WAIVED'));
-- A waiver reason is the record of a WAIVED choice.
ALTER TABLE tft.supplement_selections DROP CONSTRAINT IF EXISTS supplement_selections_waiver_check;
ALTER TABLE tft.supplement_selections ADD CONSTRAINT supplement_selections_waiver_check CHECK (
    (rnd_check_waiver_reason IS NULL) OR (rnd_check = 'WAIVED')
);

COMMIT;
