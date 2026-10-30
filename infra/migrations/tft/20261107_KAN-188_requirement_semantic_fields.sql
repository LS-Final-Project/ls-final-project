-- KAN-188: keeps the extraction fields of a requirement version that the read routes need.
-- tolerance: the tolerance as printed. needs_review: set by code when the extraction needs a person.
-- attribute_key, equipment_family: the links used for the comparison, kept for re-comparison and to
-- rebuild the search scope of a requirement with no comparison rows.
-- Versions written before this column stay NULL (unknown, not "no tolerance") and false until the
-- document is processed again. tft_app and tft_worker already have INSERT on the table.
-- Only adds. Needs 20261029_KAN-182_rfp_response.sql (T7). Re-runnable.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.requirement_versions
    ADD COLUMN IF NOT EXISTS tolerance text,
    ADD COLUMN IF NOT EXISTS needs_review boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS attribute_key text,
    ADD COLUMN IF NOT EXISTS equipment_family text;

COMMIT;
