-- KAN-110: which AI draft (and which of its three options) an approved customer send was
-- reviewed from. The approved body stays what the person approved; the draft row is not edited.
-- Apply after 20261030_KAN-107_ticket_analysis.sql and 20261030_KAN-110_customer_mail.sql.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

ALTER TABLE cs.ticket_customer_sends
    ADD COLUMN IF NOT EXISTS draft_id uuid REFERENCES cs.ticket_drafts(id),
    ADD COLUMN IF NOT EXISTS draft_option text
        CHECK (draft_option IN ('CONCISE', 'DETAILED', 'INFO_FIRST'));

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ticket_customer_sends_draft_pair'
          AND conrelid = 'cs.ticket_customer_sends'::regclass
    ) THEN
        -- A send names both the draft and the option, or neither (written by hand).
        ALTER TABLE cs.ticket_customer_sends ADD CONSTRAINT ticket_customer_sends_draft_pair
            CHECK ((draft_id IS NULL) = (draft_option IS NULL));
    END IF;
END
$$;

COMMIT;
