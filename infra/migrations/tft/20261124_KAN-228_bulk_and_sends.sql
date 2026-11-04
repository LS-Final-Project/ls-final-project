-- KAN-228: 질의서 메일 발송 기록과 프로젝트 제출 마감.
-- 일괄 결정은 표를 더하지 않는다(건별 결정 기록 detail.batch_id).
-- 추가 전용. 다시 적용해도 깨지지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

-- 제안서 제출 마감. 기존 프로젝트는 null(입력 전)이다. tft_app은 KAN-119의 표 전체 UPDATE를 그대로 쓴다.
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS submission_due_at date;

-- 질문마다 처음 보낸 기록 한 행. 다시 보내도 바꾸지 않는다(회신 기다림 날수는 처음 보낸 날부터).
CREATE TABLE IF NOT EXISTS tft.question_sends (
    question_id uuid PRIMARY KEY REFERENCES tft.customer_questions(id),
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    sent_at timestamptz NOT NULL,
    sent_by uuid NOT NULL,
    mail_ref text CHECK (mail_ref IS NULL OR length(mail_ref) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON tft.question_sends TO tft_app;

COMMIT;
