-- KAN-188: T22. DOCX 머리의 접수 번호와 프로젝트 이름을 내보내기 접수 때 고정한다.
-- worker는 tft.projects의 이름을 읽지 않고 이미 SELECT 권한이 있는 이 행만 읽는다.
-- 재시도와 이름 변경에도 파일 바이트가 같다. 옛 행은 NULL이고 머리에서 뺀다. 재실행 가능하다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
ALTER TABLE tft.exports ADD COLUMN IF NOT EXISTS title text CHECK (length(title) <= 300);
COMMIT;
