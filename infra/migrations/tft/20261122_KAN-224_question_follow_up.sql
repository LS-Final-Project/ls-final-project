-- KAN-224: 후속 질의(TQ-1-2)를 원래 질의에 잇고, 회신에서 읽은 새 요구값 후보를 회신 행에 남긴다.
-- 추가 전용 열 둘. 다시 적용해도 깨지지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.customer_questions
    ADD COLUMN IF NOT EXISTS follows_question_id uuid REFERENCES tft.customer_questions(id);
-- 후보일 뿐 요구값이 아니다. 기록 시점에 한 번 읽고(AI 결과 포함) 다시 계산하지 않는다.
ALTER TABLE tft.question_replies
    ADD COLUMN IF NOT EXISTS value_candidate jsonb
    CHECK (value_candidate IS NULL OR jsonb_typeof(value_candidate) = 'object');

COMMIT;
