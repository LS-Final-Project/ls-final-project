-- KAN-69: 전송 소유권은 평가 worker 실행 소유권과 독립적이다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
ALTER TABLE runtime.dispatch_outbox
    ADD COLUMN state text NOT NULL DEFAULT 'PENDING'
        CHECK (state IN ('PENDING','CLAIMED','SENDING','ACCEPTED','REJECTED','UNKNOWN','BLOCKED','EXHAUSTED')),
    ADD COLUMN attempt_no integer NOT NULL DEFAULT 0 CHECK (attempt_no >= 0),
    ADD COLUMN holder_id uuid,
    ADD COLUMN lease_expires_at timestamptz,
    ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now(),
    ADD CONSTRAINT dispatch_lease_shape CHECK (
        (state IN ('CLAIMED','SENDING') AND holder_id IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (state NOT IN ('CLAIMED','SENDING') AND holder_id IS NULL AND lease_expires_at IS NULL)
    );
CREATE TABLE runtime.dispatch_attempts (
    job_id uuid NOT NULL REFERENCES runtime.dispatch_outbox(job_id) ON DELETE CASCADE,
    attempt_no integer NOT NULL CHECK (attempt_no > 0),
    holder_id uuid NOT NULL,
    state text NOT NULL CHECK (state IN (
        'CLAIMED','SENDING','ACCEPTED','REJECTED','UNKNOWN','EXPIRED','BLOCKED'
    )),
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    finished_at timestamptz,
    PRIMARY KEY (job_id, attempt_no)
);
REVOKE ALL ON runtime.dispatch_attempts FROM PUBLIC, platform_app, cs_app, tft_app;
GRANT SELECT, INSERT ON runtime.dispatch_attempts TO platform_app;
GRANT UPDATE (state, sent_at, finished_at) ON runtime.dispatch_attempts TO platform_app;
GRANT UPDATE (state, attempt_no, holder_id, lease_expires_at, next_attempt_at)
    ON runtime.dispatch_outbox TO platform_app;
COMMIT;
