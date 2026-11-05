-- KAN-224: 회신 AI 읽기는 API가 아니라 worker 작업(rfp.reply_read)이 한다(모델 키·비용 원장은 worker에만 있다).
-- 요청(QUEUED·UNAVAILABLE)과 결과(DONE·FAILED)를 추가 전용으로 남긴다. 회신 행의 value_candidate(규칙 결과)는 바꾸지 않는다.
-- 추가 전용. 다시 적용해도 깨지지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

CREATE TABLE IF NOT EXISTS tft.reply_value_reads (
    seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reply_id uuid NOT NULL REFERENCES tft.question_replies(id),
    -- 같은 키 재전송은 같은 플랫폼 작업이다(입력 판본 = sha256(reply_id, request_key)).
    request_key text NOT NULL CHECK (length(request_key) BETWEEN 8 AND 128),
    status text NOT NULL CHECK (status IN ('QUEUED', 'UNAVAILABLE', 'DONE', 'FAILED')),
    candidate jsonb CHECK (candidate IS NULL OR jsonb_typeof(candidate) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (reply_id, request_key, status)
);
REVOKE ALL ON tft.reply_value_reads FROM PUBLIC;
GRANT SELECT, INSERT ON tft.reply_value_reads TO tft_app, tft_worker;

COMMIT;
