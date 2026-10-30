-- KAN-199: customer question drafts from the RFP processing worker (backend/engine/rfp/questions.py).
-- A draft (status DRAFT) is not a sent question: a person confirms it (POST .../confirm) to OPEN.
-- draft_key identifies the same question across document versions (clause_id changes with every
-- version, and the placeholder wording is the same for every clause), so a new version does not
-- create it again. Questions written by people keep draft_key NULL.
-- The worker only inserts. Inserting without a conflict target needs no SELECT.
-- Needs 20261102_KAN-186_rnd_check.sql (T10). 20261104_KAN-199_tft_worker.sql grants the same INSERT.
-- Re-runnable.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tft_worker') THEN
        CREATE ROLE tft_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END $$;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.customer_questions
    ADD COLUMN IF NOT EXISTS draft_key text CHECK (draft_key IS NULL OR length(draft_key) = 64);
ALTER TABLE tft.customer_questions DROP CONSTRAINT IF EXISTS customer_questions_status_check;
ALTER TABLE tft.customer_questions ADD CONSTRAINT customer_questions_status_check
    CHECK (status IN ('DRAFT', 'OPEN', 'REPLIED'));
CREATE UNIQUE INDEX IF NOT EXISTS customer_questions_draft_key
    ON tft.customer_questions (project_id, draft_key) WHERE draft_key IS NOT NULL;

-- The confirm command (route 31) is recorded like the other commands.
ALTER TABLE tft.rfp_commands DROP CONSTRAINT IF EXISTS rfp_commands_operation_check;
ALTER TABLE tft.rfp_commands ADD CONSTRAINT rfp_commands_operation_check
    CHECK (operation IN ('project.create', 'document.upload', 'project.process',
        'supplement.select', 'question.create', 'question.reply', 'proposal.create', 'proposal.approve',
        'proposal.reject', 'review_version.create', 'export.create', 'annotation.process',
        'question.confirm'));

GRANT INSERT ON tft.customer_questions TO tft_worker;

COMMIT;
