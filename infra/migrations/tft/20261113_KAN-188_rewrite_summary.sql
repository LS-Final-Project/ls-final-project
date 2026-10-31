-- KAN-188: T21. T19 뒤, tft_migrator. 모델 작성 결과 요약 조회가 호출 원장의 사유를 읽는다. 재실행 가능하다.
-- 새 설치는 T19가 같은 권한을 준다. 이 파일은 옛 T19를 이미 적용한 DB를 위한 것이다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
GRANT SELECT ON tft.draft_rewrite_calls TO tft_app;
COMMIT;
