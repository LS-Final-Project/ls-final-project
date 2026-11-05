-- KAN-159: 플랫폼이 시작하지 못한 C/S·RFP 작업을 실패로 닫는다(전송 주기). 소유권을 비우고 실패 코드만 쓴다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
GRANT UPDATE (status) ON runtime.jobs TO platform_app;
GRANT UPDATE (holder_id, lease_expires_at, version, failure_code) ON runtime.job_control TO platform_app;
COMMIT;
