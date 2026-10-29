-- KAN-194: 플랫폼 runtime.jobs에 C/S 문의 처리 작업 종류(owner=cs, kind=cs.analyze)를 등록한다.
-- cs 서비스는 플랫폼 내부 접수 경로(/internal/v1/platform/jobs/cs)로만 작업을 만들고 플랫폼(platform_app)이
-- 저장한다. 평가와 같이 접수 때 전송 의도(dispatch_outbox)를 만든다(tft와 다르다).
-- worker의 lease·취소·실패 코드는 범용 runtime.job_control에 둔다(평가 evaluation.run_control은 그대로).
-- cs_worker 역할에는 runtime 권한만 준다. cs 테이블·checkpoint_cs 권한은 KAN-107의 cs 마이그레이션이 준다.
-- 플랫폼·cs·checkpoint는 같은 Postgres DB(team4)의 스키마로 나뉜다는 전제다.
-- 관리자 연결로 적용한다. 다시 적용해도 같은 상태가 된다.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cs_worker') THEN
        CREATE ROLE cs_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END $$;
SET LOCAL ROLE platform_migrator;

-- 소유자·종류·경로 조합에 cs를 더한다. 평가·tft 조합은 그대로다. cs 작업은 문의(subject_id)가 있고
-- business_id가 작업 자신이다.
ALTER TABLE runtime.jobs DROP CONSTRAINT IF EXISTS jobs_owner_kind_route;
ALTER TABLE runtime.jobs ADD CONSTRAINT jobs_owner_kind_route CHECK (
    (owner = 'platform' AND kind = 'evaluation.run'
        AND route = '/api/v1/platform/evaluations/runs' AND subject_id IS NULL)
    OR (owner = 'tft' AND subject_id IS NOT NULL AND (
        (kind = 'rfp.process' AND route = '/api/v1/tft/projects/{project_id}/process')
        OR (kind = 'rfp.export' AND route = '/api/v1/tft/projects/{project_id}/draft/exports')
        OR (kind = 'rfp.annotate'
            AND route = '/api/v1/tft/projects/{project_id}/annotations/process')
    ))
    OR (owner = 'cs' AND kind = 'cs.analyze'
        AND route = '/api/v1/cs/tickets/{ticket_id}/analysis'
        AND subject_id IS NOT NULL AND business_id = id)
);

-- 같은 문의에는 진행 중(QUEUED·RUNNING) cs 작업이 하나뿐이다(409 CS_JOB_IN_PROGRESS).
CREATE UNIQUE INDEX IF NOT EXISTS jobs_cs_one_active
    ON runtime.jobs (subject_id)
    WHERE owner = 'cs' AND status IN ('QUEUED', 'RUNNING');

-- worker 소유권(generation·holder·lease)과 취소·실패 코드. 접수 때 cs 작업마다 한 행을 만든다.
CREATE TABLE IF NOT EXISTS runtime.job_control (
    job_id uuid PRIMARY KEY REFERENCES runtime.jobs(id),
    generation integer NOT NULL DEFAULT 0 CHECK (generation >= 0),
    holder_id uuid,
    lease_expires_at timestamptz,
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    cancel_requested_at timestamptz,
    -- 원문 없는 고정 코드만(예 CS_WORKER_SOURCE_MISSING).
    failure_code text CHECK (failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((holder_id IS NULL) = (lease_expires_at IS NULL))
);
REVOKE ALL ON runtime.job_control FROM PUBLIC, platform_app, cs_app, tft_app, cs_worker;
GRANT SELECT, INSERT ON runtime.job_control TO platform_app;

-- 다시 적용해도 아래 열 권한만 남도록 cs_worker의 runtime 권한을 먼저 비운다.
REVOKE ALL ON runtime.jobs, runtime.job_events, runtime.dispatch_outbox, runtime.dispatch_attempts
    FROM cs_worker;
GRANT USAGE ON SCHEMA runtime TO cs_worker;
GRANT SELECT (id, owner, kind, subject_id, input_version, actor_id, status)
    ON runtime.jobs TO cs_worker;
GRANT UPDATE (status) ON runtime.jobs TO cs_worker;
GRANT SELECT (job_id, sequence) ON runtime.job_events TO cs_worker;
GRANT INSERT (job_id, sequence, type, payload) ON runtime.job_events TO cs_worker;
GRANT SELECT ON runtime.job_control TO cs_worker;
GRANT UPDATE (generation, holder_id, lease_expires_at, version, failure_code)
    ON runtime.job_control TO cs_worker;
COMMIT;
