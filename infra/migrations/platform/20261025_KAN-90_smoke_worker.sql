-- KAN-90: 로컬 합성 worker에 필요한 열만 갱신 허용. 실제 모델 실행 권한은 아니다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
GRANT UPDATE (generation,holder_id,lease_expires_at,version) ON evaluation.run_control TO platform_app;
GRANT UPDATE (status) ON runtime.jobs TO platform_app;
COMMIT;
