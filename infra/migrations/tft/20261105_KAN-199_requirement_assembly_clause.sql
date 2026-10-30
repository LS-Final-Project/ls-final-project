-- KAN-199 (RFP API spec v1.3 §7): marks a requirement version as an assembly-supply clause, from
-- the extraction field assembly_clause (#336). required and basis_clause_ids of the integration
-- responsibility are computed from this column and task when a project is read, so no computed
-- value is stored on tft.projects. Versions written before this column stay false until the
-- document is processed again. tft_app and tft_worker already have INSERT on the table.
-- Only adds. Needs 20261029_KAN-182_rfp_response.sql (T7). Re-runnable.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.requirement_versions
    ADD COLUMN IF NOT EXISTS assembly_clause boolean NOT NULL DEFAULT false;

COMMIT;
