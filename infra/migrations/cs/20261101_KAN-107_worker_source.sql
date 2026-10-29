-- KAN-107: what the C/S processing worker (cs_worker, KAN-194) may read and write in the C/S
-- tables, and the information codes an information request asked for.
-- Needs 20261030_KAN-107_ticket_analysis.sql, 20261030_KAN-110_customer_mail.sql,
-- 20261029_KAN-194_checkpoint_schema.sql and 20261029_KAN-99_search_chunks.sql.
-- The role is created here too when the platform migration (20261031_KAN-194_cs_jobs.sql) has
-- not run yet, with the same attributes. Apply with the admin connection. Re-runnable.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cs_worker') THEN
        CREATE ROLE cs_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END $$;
SET LOCAL ROLE cs_migrator;

-- Codes the agent asked the customer for in this message (info_field_codebook codes). The
-- worker reads them back as "already asked" so a later draft does not ask again. An answer
-- may ask too, so both kinds keep the list; an empty list asked nothing.
ALTER TABLE cs.ticket_customer_sends
    ADD COLUMN IF NOT EXISTS requested_info_codes jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(requested_info_codes) = 'array');

-- Start from nothing so a re-run leaves exactly the grants below.
REVOKE ALL ON ALL TABLES IN SCHEMA cs FROM cs_worker;
REVOKE ALL ON ALL TABLES IN SCHEMA checkpoint_cs FROM cs_worker;
GRANT USAGE ON SCHEMA cs, checkpoint_cs TO cs_worker;

-- Read only what the graph input needs. No customer identifiers, no answers or labels.
GRANT SELECT (id, channel, body_language, subject, body, product_code, received_at)
    ON cs.tickets TO cs_worker;
GRANT SELECT (ticket_id, customer_tier) ON cs.ticket_source_records TO cs_worker;
GRANT SELECT (ticket_id, kind, requested_info_codes, recorded_at)
    ON cs.ticket_customer_sends TO cs_worker;
-- Evidence search (backend/agent/cs_search.py) reads the document chunks.
GRANT SELECT ON cs.document_chunks TO cs_worker;
-- Results only grow: the worker adds runs and drafts, never edits or removes them.
GRANT SELECT, INSERT ON cs.ticket_analyses, cs.ticket_drafts TO cs_worker;
-- Same checkpoint rights as cs_app: the saver writes during a run and never deletes.
GRANT SELECT, INSERT, UPDATE
    ON checkpoint_cs.checkpoints, checkpoint_cs.checkpoint_blobs, checkpoint_cs.checkpoint_writes
    TO cs_worker;

COMMIT;
