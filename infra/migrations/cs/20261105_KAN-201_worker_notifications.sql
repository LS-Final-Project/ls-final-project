-- KAN-201 E1. After C4(workflows), C18(cs_worker). No identity or workflow body access.
-- C18 resets all cs_worker rights; reapply this migration after reapplying C18.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;
GRANT SELECT (ticket_id,owner_user_id) ON cs.ticket_workflows TO cs_worker;
COMMIT;
