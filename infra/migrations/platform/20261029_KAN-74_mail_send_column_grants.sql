-- KAN-74 후속: 발송 의도의 승인 내용(본문·수신자·첨부·payload 해시)을 런타임 계정이 고치지 못하게 한다.
-- 20261028_KAN-74_mail_send.sql 뒤에 관리자 연결로 한 번 적용한다. 상태 관련 열만 UPDATE를 허용한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
REVOKE UPDATE ON integration.mail_send_intents FROM platform_app;
GRANT UPDATE (
    status, attempts, error_code, submitted_at, accepted_at,
    sent_copy_confirmed_at, sent_graph_id, sent_internet_message_id, updated_at
) ON integration.mail_send_intents TO platform_app;
COMMIT;
