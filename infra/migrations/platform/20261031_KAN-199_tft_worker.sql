-- KAN-199: RFP 처리 worker 역할(tft_worker)을 만들고 runtime 열 권한만 준다(cs_worker와 같은 모양).
-- tft 표 권한은 처리 함수를 구현하는 tft 마이그레이션(KAN-186·KAN-184)이 준다.
-- 20261031_KAN-194_cs_jobs.sql(P28) 뒤에 적용한다(runtime.job_control이 필요하다). P28을 다시 적용해도
-- tft_worker 권한은 바뀌지 않는다(P28의 REVOKE 목록에 없음).
-- 관리자 연결로 적용한다. 다시 적용해도 같은 상태가 된다.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tft_worker') THEN
        CREATE ROLE tft_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END $$;
SET LOCAL ROLE platform_migrator;

-- 다시 적용해도 아래 열 권한만 남도록 tft_worker의 runtime 권한을 먼저 비운다.
REVOKE ALL ON runtime.jobs, runtime.job_events, runtime.dispatch_outbox, runtime.dispatch_attempts,
    runtime.job_control FROM tft_worker;
GRANT USAGE ON SCHEMA runtime TO tft_worker;
GRANT SELECT (id, owner, kind, subject_id, input_version, actor_id, status)
    ON runtime.jobs TO tft_worker;
GRANT UPDATE (status) ON runtime.jobs TO tft_worker;
GRANT SELECT (job_id, sequence) ON runtime.job_events TO tft_worker;
GRANT INSERT (job_id, sequence, type, payload) ON runtime.job_events TO tft_worker;
GRANT SELECT ON runtime.job_control TO tft_worker;
GRANT UPDATE (generation, holder_id, lease_expires_at, version, failure_code)
    ON runtime.job_control TO tft_worker;
COMMIT;
