-- KAN-182: 플랫폼 runtime.jobs에 RFP 자사 대응 작업 종류 3개(owner=tft)를 등록한다.
-- 명세 v1.1 §4.3·결정 4. tft_app에는 runtime 권한을 주지 않는다. tft 서비스는 플랫폼 내부 접수
-- 경로(/internal/v1/platform/jobs/tft)로만 작업을 만들고, 플랫폼(platform_app)이 저장한다.
-- 처리 worker가 아직 없으므로 tft 작업은 전송 의도(dispatch_outbox)를 만들지 않는다.
-- 관리자 연결로 적용한다. 다시 적용해도 같은 상태가 된다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;

-- 작업 대상 프로젝트. 평가 작업은 비워 둔다. 같은 프로젝트의 진행 중 작업 검사에 쓴다.
ALTER TABLE runtime.jobs ADD COLUMN IF NOT EXISTS subject_id uuid;

-- 열마다 하나였던 고정 CHECK를 소유자·종류·경로 조합 하나로 바꾼다. 평가 조합은 그대로다.
ALTER TABLE runtime.jobs DROP CONSTRAINT IF EXISTS jobs_owner_check;
ALTER TABLE runtime.jobs DROP CONSTRAINT IF EXISTS jobs_kind_check;
ALTER TABLE runtime.jobs DROP CONSTRAINT IF EXISTS jobs_route_check;
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
);

-- 같은 프로젝트에는 진행 중(QUEUED·RUNNING) tft 작업이 하나뿐이다(409 TFT_JOB_IN_PROGRESS).
CREATE UNIQUE INDEX IF NOT EXISTS jobs_tft_one_active
    ON runtime.jobs (subject_id)
    WHERE owner = 'tft' AND status IN ('QUEUED', 'RUNNING');

COMMIT;
