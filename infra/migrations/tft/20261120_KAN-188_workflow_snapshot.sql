-- KAN-188: immutable draft inputs and idempotent single-requirement comparison.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
ALTER TABLE tft.draft_versions ADD COLUMN IF NOT EXISTS input_snapshot jsonb;
ALTER TABLE tft.rfp_commands DROP CONSTRAINT IF EXISTS rfp_commands_operation_check;
ALTER TABLE tft.rfp_commands ADD CONSTRAINT rfp_commands_operation_check CHECK (operation IN (
    'project.create','document.upload','supplement.select','question.create','question.reply','question.confirm',
    'proposal.create','proposal.approve','proposal.reject','project.process', 'context.process','annotation.process',
    'review_version.create','export.create','draft.rewrite','requirement.recompare'
));
CREATE TABLE IF NOT EXISTS tft.requirement_recomparisons (
 id uuid PRIMARY KEY,
 requirement_id uuid NOT NULL REFERENCES tft.requirements(id),
 requirement_version integer NOT NULL,
 result jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (requirement_id,requirement_version) REFERENCES tft.requirement_versions(requirement_id,version)
);
CREATE INDEX IF NOT EXISTS requirement_recomparisons_latest ON tft.requirement_recomparisons(requirement_id,requirement_version,created_at DESC);
REVOKE ALL ON tft.requirement_recomparisons FROM PUBLIC, tft_app, tft_worker;
GRANT SELECT, INSERT ON tft.requirement_recomparisons TO tft_app;
GRANT SELECT ON tft.requirement_recomparisons TO tft_worker;
COMMIT;
