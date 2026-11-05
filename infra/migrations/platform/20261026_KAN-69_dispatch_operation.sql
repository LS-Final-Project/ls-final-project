-- KAN-69: 전송 접수 대조에 필요한 operation 식별자만 보존한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
ALTER TABLE runtime.dispatch_attempts ADD COLUMN operation_name text CHECK (
    operation_name IS NULL OR operation_name ~
    '^projects/[1-9][0-9]{5,19}/locations/asia-northeast3/operations/[A-Za-z0-9_-]{1,128}$'
);
GRANT UPDATE (operation_name) ON runtime.dispatch_attempts TO platform_app;
COMMIT;
