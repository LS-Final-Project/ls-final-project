-- KAN-86: 평가 시도에 공급자 사용량 원문·요청 모델·호출 시도 수를 남긴다. 관리자 연결로 적용한다.
-- 20261023_KAN-85_evaluation_storage.sql과 20261027_KAN-90_worker_usage_KAN-86.sql 뒤에 적용한다.
-- 원문은 토큰 수 메타데이터만 담는다. 시도 읽기/수정/삭제 권한은 추가하지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
ALTER TABLE evaluation.case_attempts
    ADD COLUMN IF NOT EXISTS usage_raw jsonb,
    ADD COLUMN IF NOT EXISTS requested_model text,
    ADD COLUMN IF NOT EXISTS call_attempts smallint CHECK (call_attempts IS NULL OR call_attempts >= 1);
GRANT INSERT (usage_raw,requested_model,call_attempts,cost_usd,pricing_version)
    ON evaluation.case_attempts TO evaluation_worker;
COMMIT;
