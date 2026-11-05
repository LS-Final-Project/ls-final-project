-- KAN-188: persist generated review flags and one draft version per processing job.
-- Needs T7, T10, T11, T12, T13, T14, T15. Apply as admin (tft_worker role comes from T14).
-- Exactly two columns; existing draft/review/export tables are reused. Re-runnable.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
ALTER TABLE tft.draft_sentences ADD COLUMN IF NOT EXISTS needs_rereview boolean NOT NULL DEFAULT false;
ALTER TABLE tft.draft_versions ADD COLUMN IF NOT EXISTS source_job_id uuid;
CREATE UNIQUE INDEX IF NOT EXISTS draft_versions_source_job
    ON tft.draft_versions (source_job_id) WHERE source_job_id IS NOT NULL;
REVOKE SELECT ON tft.projects FROM tft_worker;
GRANT SELECT (id,responsible_user_id,sales_user_id,review_departments,data_origin,
    integration_holder,integration_holder_candidates,integration_note,integration_set_by,integration_set_at)
    ON tft.projects TO tft_worker;
-- KAN-187 T20 뒤 재적용해도 사람 입력3열의 최소 SELECT를 보존한다. T20 전 설치는 건너뛴다.
DO $$
BEGIN
    IF (SELECT count(*) FROM information_schema.columns
        WHERE table_schema='tft' AND table_name='projects'
        AND column_name IN ('supply_region','customer_manufacturer_list','off_list_permission')) = 3 THEN
        EXECUTE 'GRANT SELECT(supply_region,customer_manufacturer_list,off_list_permission)
            ON tft.projects TO tft_worker';
    END IF;
END $$;
GRANT SELECT ON tft.requirements, tft.requirement_conditions,
    tft.clause_deleted_values, tft.product_comparisons, tft.comparison_evidence,
    tft.supplement_options, tft.supplement_selections, tft.partner_grades,
    tft.customer_questions, tft.question_replies, tft.change_proposals,
    tft.rereview_marks, tft.department_kinds, tft.review_versions, tft.exports,
    tft.drafts, tft.draft_versions, tft.draft_sentences, tft.draft_sentence_evidence TO tft_worker;
GRANT INSERT ON tft.drafts, tft.draft_versions, tft.draft_sentences,
    tft.draft_sentence_evidence, tft.rfp_audit_events TO tft_worker;
GRANT UPDATE (latest_version) ON tft.drafts TO tft_worker;
GRANT UPDATE (job_id,storage_key,sha256,size,completed_at) ON tft.exports TO tft_worker;
COMMIT;
