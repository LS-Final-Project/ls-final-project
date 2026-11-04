-- KAN-221: 입찰 대응 XLSX(편차표·질의서·현황표·요구별) 내보내기.
-- exports.format CHECK에 XLSX를 더한다. 이 CHECK는 KAN-182만 정의한다(CREATE TABLE IF NOT EXISTS라 재적용해도
-- 다시 정의하지 않는다). 내보내기 worker(tft_worker)가 판정·결정·편차·철회 기록을 읽는다
-- (20261104_KAN-199_tft_worker.sql이 재적용될 때도 같은 권한을 복구한다).
-- 표·행은 더하거나 지우지 않는다. 다시 적용해도 깨지지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.exports DROP CONSTRAINT IF EXISTS exports_format_check;
ALTER TABLE tft.exports ADD CONSTRAINT exports_format_check CHECK (format IN ('DOCX', 'PDF', 'XLSX'));
GRANT SELECT ON tft.requirement_decisions, tft.deviations, tft.question_withdrawals TO tft_worker;
COMMIT;
