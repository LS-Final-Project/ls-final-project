-- KAN-224: 회신 값 AI 읽기 작업(rfp.reply_read). 기존 평가/C/S/TFT 조합을 보존한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
ALTER TABLE runtime.jobs DROP CONSTRAINT IF EXISTS jobs_owner_kind_route;
ALTER TABLE runtime.jobs ADD CONSTRAINT jobs_owner_kind_route CHECK (
    (owner = 'platform' AND kind = 'evaluation.run'
        AND route = '/api/v1/platform/evaluations/runs' AND subject_id IS NULL)
    OR (owner = 'tft' AND subject_id IS NOT NULL AND (
        (kind = 'rfp.context' AND route = '/api/v1/tft/projects/{project_id}/context/process')
        OR (kind = 'rfp.process' AND route = '/api/v1/tft/projects/{project_id}/process')
        OR (kind = 'rfp.draft_rewrite' AND route = '/api/v1/tft/projects/{project_id}/draft/rewrite')
        OR (kind = 'rfp.export' AND route = '/api/v1/tft/projects/{project_id}/draft/exports')
        OR (kind = 'rfp.reply_read' AND route = '/api/v1/tft/questions/{question_id}/reply')
        OR (kind = 'rfp.annotate'
            AND route = '/api/v1/tft/projects/{project_id}/annotations/process')
    ))
    OR (owner = 'cs' AND kind = 'cs.analyze'
        AND route = '/api/v1/cs/tickets/{ticket_id}/analysis'
        AND subject_id IS NOT NULL AND business_id = id)
);
COMMIT;
