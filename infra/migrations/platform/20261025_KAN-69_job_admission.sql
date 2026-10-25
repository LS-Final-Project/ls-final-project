-- KAN-69: 플랫폼 평가 작업의 영속 접수. 관리자 연결로 적용한다.
-- 전송·worker lease·SSE API는 별도 구현이며 이 마이그레이션은 실행을 활성화하지 않는다.
\set ON_ERROR_STOP on
BEGIN;
CREATE SCHEMA IF NOT EXISTS runtime AUTHORIZATION platform_migrator;
SET LOCAL ROLE platform_migrator;
REVOKE ALL ON SCHEMA runtime FROM PUBLIC;
GRANT USAGE ON SCHEMA runtime TO platform_app;

CREATE TABLE runtime.jobs (
    id uuid PRIMARY KEY,
    owner text NOT NULL CHECK (owner = 'platform'),
    kind text NOT NULL CHECK (kind = 'evaluation.run'),
    business_id uuid NOT NULL,
    input_version text NOT NULL CHECK (input_version ~ '^[0-9a-f]{64}$'),
    actor_id uuid NOT NULL REFERENCES identity.users(id),
    method text NOT NULL CHECK (method = 'POST'),
    route text NOT NULL CHECK (route = '/api/v1/platform/evaluations/runs'),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'QUEUED' CHECK (status IN (
        'QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED'
    )),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (actor_id, owner, method, route, idempotency_key),
    UNIQUE (owner, kind, business_id)
);

CREATE TABLE runtime.job_events (
    job_id uuid NOT NULL REFERENCES runtime.jobs(id),
    sequence bigint NOT NULL CHECK (sequence > 0),
    type text NOT NULL CHECK (type IN (
        'job.queued', 'job.started', 'job.progress', 'job.completed', 'job.failed'
    )),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    -- 원문·정답·자격증명 금지. 최초 이벤트는 빈 객체다.
    payload jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (job_id, sequence)
);

CREATE TABLE runtime.dispatch_outbox (
    job_id uuid PRIMARY KEY REFERENCES runtime.jobs(id),
    created_at timestamptz NOT NULL DEFAULT now()
    -- 미전송 의도만 보존한다. 전송 상태·lease는 dispatcher 구현에서 확장한다.
);

REVOKE ALL ON runtime.jobs, runtime.job_events, runtime.dispatch_outbox
    FROM PUBLIC, platform_app, cs_app, tft_app;
GRANT SELECT, INSERT ON runtime.jobs, runtime.job_events, runtime.dispatch_outbox
    TO platform_app;
COMMIT;
