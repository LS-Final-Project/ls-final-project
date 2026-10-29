-- KAN-110: 회신 연결(REPLY)을 업무 서비스(C/S)에 알린 시각. 연결 자체는 그대로이고 통지 칸만 둔다.
-- 20261029_KAN-75_mail_links.sql 뒤에 적용한다. 관리자 연결로 적용한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;

-- 비어 있으면 아직 알리지 않은 연결. 수집 워커가 다음 실행에서 다시 알린다.
ALTER TABLE integration.mail_links ADD COLUMN IF NOT EXISTS business_notified_at timestamptz;
CREATE INDEX IF NOT EXISTS mail_links_unnotified
    ON integration.mail_links (created_at) WHERE kind = 'REPLY' AND business_notified_at IS NULL;
GRANT UPDATE (business_notified_at) ON integration.mail_links TO platform_app;

COMMIT;
