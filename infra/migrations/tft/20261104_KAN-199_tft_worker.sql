-- KAN-199 (KAN-188 A07 glue): tft table privileges for the RFP processing worker (tft_worker).
-- The role and its runtime column privileges come from platform P29 (20261031_KAN-199_tft_worker.sql).
-- If this runs first, the role is created here the same way (NOLOGIN, no attributes) and P29 adds the
-- runtime privileges later. Apply with an admin connection. The processor (backend/api/tft/worker_processor.py) reads document versions and
-- partner profiles and only appends rows (clauses, requirements and their first version, comparisons,
-- supplement options, partner grades). It updates nothing and deletes nothing.
-- Needs T7 (20261029_KAN-182_rfp_response.sql). Re-runnable: privileges are cleared first.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tft_worker') THEN
        CREATE ROLE tft_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END $$;
SET LOCAL ROLE tft_migrator;

REVOKE ALL ON ALL TABLES IN SCHEMA tft FROM tft_worker;
GRANT USAGE ON SCHEMA tft TO tft_worker;
GRANT SELECT ON tft.documents, tft.document_versions, tft.requirement_versions,
    tft.partner_profiles TO tft_worker;
GRANT SELECT, INSERT ON tft.clauses TO tft_worker;
GRANT INSERT ON tft.clause_deleted_values, tft.clause_flags, tft.requirements,
    tft.requirement_versions, tft.requirement_conditions, tft.product_comparisons,
    tft.comparison_evidence, tft.supplement_options, tft.partner_grades TO tft_worker;

COMMIT;
