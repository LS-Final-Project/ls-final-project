-- KAN-188: the RFP worker restores projects.output_status when processing fails or is cancelled.
-- After T1·T7·T14. The older worker migration resets tft privileges; apply this file afterwards again.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
GRANT SELECT (output_status, review_version), UPDATE (output_status) ON tft.projects TO tft_worker;
COMMIT;
