-- KAN-182 후속(AI 리뷰 P3). 20261029_KAN-182_rfp_response.sql 뒤에 적용한다.
-- 1. 계산 결과 표 4개에 판본 유일 키. 같은 요구 판본을 다시 계산하거나 동시에 계산해도
--    같은 version이 두 번 쌓이지 않고, 읽는 쪽이 최신 판본을 하나로 고를 수 있다.
--    tft_app은 UPDATE·DELETE가 없어 중복이 생기면 정리할 수 없으므로 DB가 막는다.
-- 2. 재검토 표시의 원인 제안은 같은 프로젝트의 제안만 가리킨다.
-- 다시 적용해도 같은 상태가 된다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.product_comparisons DROP CONSTRAINT IF EXISTS product_comparisons_version_key;
-- variant가 없는 제품도 한 번만 세도록 NULL을 같은 값으로 본다.
ALTER TABLE tft.product_comparisons ADD CONSTRAINT product_comparisons_version_key
    UNIQUE NULLS NOT DISTINCT (requirement_id, requirement_version, product_code, variant, version);

ALTER TABLE tft.supplement_options DROP CONSTRAINT IF EXISTS supplement_options_version_key;
ALTER TABLE tft.supplement_options ADD CONSTRAINT supplement_options_version_key
    UNIQUE (requirement_id, requirement_version, path, version);

ALTER TABLE tft.partner_grades DROP CONSTRAINT IF EXISTS partner_grades_version_key;
ALTER TABLE tft.partner_grades ADD CONSTRAINT partner_grades_version_key
    UNIQUE (project_id, candidate_code, profile_version, requirement_id, requirement_version, version);

ALTER TABLE tft.partner_grade_summaries DROP CONSTRAINT IF EXISTS partner_grade_summaries_version_key;
ALTER TABLE tft.partner_grade_summaries ADD CONSTRAINT partner_grade_summaries_version_key
    UNIQUE (project_id, candidate_code, profile_version, version);

ALTER TABLE tft.rereview_marks DROP CONSTRAINT IF EXISTS rereview_marks_proposal_project_fkey;
ALTER TABLE tft.change_proposals DROP CONSTRAINT IF EXISTS change_proposals_project_id_key;
ALTER TABLE tft.change_proposals ADD CONSTRAINT change_proposals_project_id_key UNIQUE (project_id, id);
ALTER TABLE tft.rereview_marks ADD CONSTRAINT rereview_marks_proposal_project_fkey
    FOREIGN KEY (project_id, cause_proposal_id) REFERENCES tft.change_proposals(project_id, id);

COMMIT;
