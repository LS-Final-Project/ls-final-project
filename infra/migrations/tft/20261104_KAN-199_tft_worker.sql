-- KAN-199 (KAN-188 A07 glue): tft table privileges for the RFP processing worker (tft_worker).
-- The role and its runtime column privileges come from platform P29 (20261031_KAN-199_tft_worker.sql).
-- If this runs first, the role is created here the same way (NOLOGIN, no attributes) and P29 adds the
-- runtime privileges later. Apply with an admin connection. The processor (backend/api/tft/worker_processor.py) reads document versions and
-- partner profiles and only appends rows (clauses, requirements and their first version, comparisons,
-- supplement options, partner grades, draft versions). Only draft head/export output columns can be updated; no deletes.
-- Needs T7, T10 (department kinds), T11 (project integration columns).
-- Re-runnable: privileges are cleared first and all current processing grants are restored.
-- Draft table grants work before T17 adds needs_rereview/source_job_id; no new column is named here.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tft_worker') THEN
        CREATE ROLE tft_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END $$;
SET LOCAL ROLE tft_migrator;

REVOKE ALL ON ALL TABLES IN SCHEMA tft FROM tft_worker;
GRANT USAGE ON SCHEMA tft TO tft_worker;
GRANT SELECT ON tft.documents, tft.document_versions, tft.requirement_versions,
    tft.partner_profiles TO tft_worker;
GRANT SELECT, INSERT ON tft.clauses TO tft_worker;
GRANT INSERT ON tft.clause_deleted_values, tft.clause_flags, tft.requirements,
    tft.requirement_versions, tft.requirement_conditions, tft.product_comparisons,
    tft.comparison_evidence, tft.supplement_options, tft.partner_grades,
    tft.customer_questions TO tft_worker;

-- KAN-188: keep draft/export and responsible-user access when T14 is re-applied.
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

-- KAN-188 T19가 이미 있으면 재적용 후에도 rewrite 최소 권한을 복구한다. 새 설치에서는 건너뛴다.
DO $$
BEGIN
    IF to_regclass('tft.draft_rewrites') IS NOT NULL THEN
        EXECUTE 'GRANT SELECT ON tft.draft_rewrites TO tft_worker';
        EXECUTE 'GRANT UPDATE(state,result_version) ON tft.draft_rewrites TO tft_worker';
    END IF;
    IF to_regclass('tft.draft_rewrite_calls') IS NOT NULL THEN
        EXECUTE 'GRANT SELECT,INSERT ON tft.draft_rewrite_calls TO tft_worker';
    END IF;
END $$;
COMMIT;
