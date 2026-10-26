-- KAN-90: 로컬 합성 실행의 취소/재개 요청 결과를 추가 전용으로 저장한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
CREATE TABLE evaluation.run_action_receipts (
    run_id uuid NOT NULL REFERENCES evaluation.runs(id),
    actor_id uuid NOT NULL REFERENCES identity.users(id),
    action text NOT NULL CHECK (action IN ('cancel','resume')),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    response jsonb NOT NULL CHECK (jsonb_typeof(response)='object'),
    PRIMARY KEY (run_id,actor_id,action,idempotency_key)
);
REVOKE ALL ON evaluation.run_action_receipts FROM PUBLIC,platform_app,cs_app,tft_app;
GRANT SELECT,INSERT ON evaluation.run_action_receipts TO platform_app;
GRANT UPDATE (cancel_requested_at,cancel_requested_by) ON evaluation.run_control TO platform_app;
COMMIT;
