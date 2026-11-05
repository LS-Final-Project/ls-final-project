-- KAN-239: 메일 분류·회신 연결 표에서 platform_app의 표 단위 UPDATE·DELETE·TRUNCATE를 회수하고
-- 코드가 실제로 바꾸는 열의 UPDATE만 다시 준다. 001의 integration 기본 권한(SELECT·INSERT·UPDATE·DELETE)이
-- P17(20261028_KAN-73_mail_triage.sql)·P23(20261029_KAN-75_mail_links.sql)에서 회수되지 않아
-- 분류 판본·연결·검토 후보를 고치거나 지울 수 있었다. 코드에는 이 표들의 DELETE가 없다.
-- P17·P23·P26(20261030_KAN-110_mail_link_notify.sql)·P36 뒤에 관리자 연결로 적용한다. 재실행 가능.
-- 표 단위 REVOKE는 열 권한도 함께 지우므로, P17·P23·P26을 다시 적용하면 이 파일을 그 뒤에 다시 적용한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;

REVOKE UPDATE, DELETE, TRUNCATE ON integration.mail_triage, integration.mail_triage_latest,
    integration.mail_links, integration.mail_reply_reviews, integration.mail_reply_checks
    FROM platform_app;
-- 분류(triage.py): 판본은 추가만, 최신 포인터만 옮긴다.
GRANT UPDATE (latest_version) ON integration.mail_triage_latest TO platform_app;
-- 회신 통지(links.py notify_cs_replies): 연결 자체는 바꾸지 않고 통지 시각만 적는다.
GRANT UPDATE (business_notified_at) ON integration.mail_links TO platform_app;
-- 검토 결정(links.py·intake.py·rfp_intake.py): 결정 칸만 바꾼다. 후보·이유는 고치지 못한다.
GRANT UPDATE (status, decided_by, decided_at, note) ON integration.mail_reply_reviews TO platform_app;
GRANT UPDATE (outcome, checked_at) ON integration.mail_reply_checks TO platform_app;
COMMIT;
