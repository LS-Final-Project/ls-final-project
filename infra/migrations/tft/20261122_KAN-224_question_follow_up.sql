-- KAN-224: 후속 질의(TQ-1-2)를 원래 질의에 잇는다. 추가 전용 열 하나. 다시 적용해도 깨지지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.customer_questions
    ADD COLUMN IF NOT EXISTS follows_question_id uuid REFERENCES tft.customer_questions(id);

COMMIT;
