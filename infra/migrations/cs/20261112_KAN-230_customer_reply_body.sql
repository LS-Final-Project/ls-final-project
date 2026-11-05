-- Subject and body text of a customer reply the platform linked to a ticket (KAN-230). The platform
-- reads them from the stored original when it reports the reply, with the quoted history of our own
-- earlier mail cut off. Same level as the original of a ticket received by mail (cs.tickets.body).
-- Old rows stay null (the screen then says to read the reply in the mail account).
-- Apply after C13 (KAN-110 customer mail), before the C/S code that sends these columns.
-- Re-runnable; only the owning migrator alters the table.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

ALTER TABLE cs.ticket_customer_replies
    ADD COLUMN IF NOT EXISTS subject text CHECK (subject IS NULL OR length(subject) <= 500),
    ADD COLUMN IF NOT EXISTS body text CHECK (body IS NULL OR length(body) BETWEEN 1 AND 20000);

COMMIT;
