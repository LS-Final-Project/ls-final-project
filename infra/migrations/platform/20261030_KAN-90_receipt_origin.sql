-- KAN-90 4차: 접수 영수증에 출처(origin)와 반입 출처 참조(import_ref)를 더한다. 관리자 연결로 적용한다.
-- 20261025_KAN-90_run_admission.sql과 20261027_KAN-90_worker_role.sql 뒤에 적용한다. 기존 행은 ACCEPT다.
-- IMPORT는 scripts/eval_import_run.py가 만든 반입 실행이며 import_ref(반입 결과 JSON sha256 앞 12자)가 있다.
-- 작업자 역할은 dispatch 경계(origin='ACCEPT')를 읽어야 하므로 origin 열 SELECT만 더한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
ALTER TABLE evaluation.admission_receipts
    ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'ACCEPT'
        CONSTRAINT admission_receipts_origin_known CHECK (origin IN ('ACCEPT', 'IMPORT')),
    ADD COLUMN IF NOT EXISTS import_ref text
        CONSTRAINT admission_receipts_import_ref CHECK (
            (origin = 'IMPORT') = (import_ref IS NOT NULL)
            AND (import_ref IS NULL OR import_ref ~ '^[0-9a-f]{12}$')
        );
GRANT SELECT (origin) ON evaluation.admission_receipts TO evaluation_worker;
COMMIT;
