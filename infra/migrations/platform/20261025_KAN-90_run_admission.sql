-- KAN-90: 평가 접수와 최초 응답 보존. KAN-85 및 KAN-69 이후 한 번 적용한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
ALTER TABLE runtime.jobs ADD CONSTRAINT jobs_id_business UNIQUE (id,business_id);
ALTER TABLE evaluation.runs ADD CONSTRAINT runs_unique_job UNIQUE (job_id);
ALTER TABLE evaluation.runs ADD CONSTRAINT runs_job_business_fk
    FOREIGN KEY (job_id,id) REFERENCES runtime.jobs(id,business_id);

CREATE TABLE evaluation.run_control (
    run_id uuid PRIMARY KEY REFERENCES evaluation.runs(id),
    generation bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    holder_id uuid,
    lease_expires_at timestamptz,
    cancel_requested_at timestamptz,
    cancel_requested_by uuid REFERENCES identity.users(id),
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    CHECK ((holder_id IS NULL) = (lease_expires_at IS NULL)),
    CHECK ((cancel_requested_at IS NULL) = (cancel_requested_by IS NULL))
);
CREATE TABLE evaluation.admission_receipts (
    run_id uuid PRIMARY KEY REFERENCES evaluation.runs(id),
    response_request_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON evaluation.run_control, evaluation.admission_receipts
    FROM PUBLIC, platform_app, cs_app, tft_app;
GRANT SELECT, INSERT ON evaluation.run_control, evaluation.admission_receipts TO platform_app;
COMMIT;
