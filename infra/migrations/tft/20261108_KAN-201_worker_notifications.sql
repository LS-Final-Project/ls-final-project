-- KAN-201 E1. After T1(projects), worker grant migration 20261104_KAN-199_tft_worker.sql.
-- The older worker migration resets tft privileges; apply this file afterwards again.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
GRANT SELECT (id,responsible_user_id) ON tft.projects TO tft_worker;
COMMIT;
