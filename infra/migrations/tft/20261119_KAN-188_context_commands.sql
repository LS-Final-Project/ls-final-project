-- KAN-188: 기존 요구만 맥락 정리하는 접수 멱등 연산.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
ALTER TABLE tft.rfp_commands DROP CONSTRAINT IF EXISTS rfp_commands_operation_check;
ALTER TABLE tft.rfp_commands ADD CONSTRAINT rfp_commands_operation_check CHECK (operation IN (
    'project.create','document.upload','supplement.select','question.create','question.reply','question.confirm',
    'proposal.create','proposal.approve','proposal.reject','project.process', 'context.process','annotation.process',
    'review_version.create','export.create','draft.rewrite'
));
COMMIT;
