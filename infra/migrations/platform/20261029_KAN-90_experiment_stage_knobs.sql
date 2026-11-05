-- KAN-90: 평가 실험에 단계(stage)와 비밀 아닌 조절 항목(knobs)을 더한다. 관리자 연결로 적용한다.
-- 20261023_KAN-85_evaluation_storage.sql 뒤에 적용한다. 기존 행은 stage NULL·knobs {}로 남아 config_hash가
-- 그대로다(앱이 비거나 없는 값은 hash에서 생략한다). 표 단위 권한이라 열 권한은 추가하지 않는다.
-- 비밀 키 정규식은 backend/api/platform/evaluations/admission.py의 SECRET_KNOB_PATTERN과 같아야 한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
ALTER TABLE evaluation.experiments
    ADD COLUMN IF NOT EXISTS stage text
        CONSTRAINT experiments_stage_known
        CHECK (stage IN ('classification', 'search', 'draft', 'rfp')),
    ADD COLUMN IF NOT EXISTS knobs jsonb NOT NULL DEFAULT '{}'::jsonb
        CONSTRAINT experiments_knobs_no_secret
        CHECK (
            jsonb_typeof(knobs) = 'object'
            AND NOT jsonb_path_exists(
                knobs,
                '$.keyvalue() ? (@.key like_regex "api_key|token|secret|password|credential" flag "i")'
            )
        );
COMMIT;
