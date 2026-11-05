-- KAN-107: tier given when a ticket is registered through the API. NULL means unknown: mail and
-- older tickets have none, and nothing fills it in. Rules then calculate with the standard tier
-- and the analysis says so, so a later customer tier table finds unknown tickets still unknown.
-- Provided tickets keep their tier in cs.ticket_source_records.
-- Needs C1(tickets), C18(cs_worker). C18 resets all cs_worker rights; reapply this after C18.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;
ALTER TABLE cs.tickets ADD COLUMN IF NOT EXISTS customer_tier text
    CHECK (customer_tier IN ('STANDARD', 'GOLD', 'PLATINUM'));
GRANT SELECT (customer_tier) ON cs.tickets TO cs_worker;
COMMIT;
