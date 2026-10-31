-- KAN-188: T19. T14/T17 뒤, tft_migrator. 모델 호출/외부 작업 없이 재실행 가능하다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
ALTER TABLE tft.draft_sentences ADD COLUMN IF NOT EXISTS template_text text
    CHECK (template_text IS NULL OR length(template_text) BETWEEN 1 AND 4000);
-- 사람의 현행 본문을 양식으로 오인하지 않는다. 실제 자동 기원만 사용한다.
UPDATE tft.draft_sentences SET template_text=text
    WHERE template_text IS NULL AND provenance_kind<>'human_input';
UPDATE tft.draft_sentences h SET template_text=(
    SELECT old.template_text FROM tft.draft_sentences old
    WHERE old.project_id=h.project_id AND old.sentence_key=h.sentence_key
      AND old.draft_version<=h.draft_version AND old.provenance_kind<>'human_input'
    ORDER BY old.draft_version DESC LIMIT 1
) WHERE h.provenance_kind='human_input' AND h.template_text IS NULL;
CREATE TABLE IF NOT EXISTS tft.draft_rewrites (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    base_version integer NOT NULL CHECK (base_version>0),
    actor_id uuid NOT NULL,
    request_key text NOT NULL,
    input_version text NOT NULL UNIQUE CHECK (input_version ~ '^[0-9a-f]{64}$'),
    job_id uuid,
    state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','saved','discarded')),
    result_version integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id,actor_id,request_key),
    FOREIGN KEY (project_id,base_version) REFERENCES tft.draft_versions(project_id,version)
);
CREATE TABLE IF NOT EXISTS tft.draft_rewrite_calls (
    rewrite_id uuid NOT NULL REFERENCES tft.draft_rewrites(id),
    section text NOT NULL,
    attempt integer NOT NULL CHECK (attempt BETWEEN 0 AND 2),
    outcome text NOT NULL CHECK (outcome IN ('skipped','passed','retry','fallback','error','truncated')),
    reasons jsonb NOT NULL,
    prompt_version text NOT NULL,
    requested_model text NOT NULL,
    returned_model text,
    model_version text,
    input_tokens bigint, output_tokens bigint, thought_tokens bigint, cached_input_tokens bigint, total_tokens bigint,
    cost_usd numeric, price_basis text,
    elapsed_ms bigint NOT NULL CHECK (elapsed_ms>=0),
    provider_attempts integer NOT NULL CHECK (provider_attempts>=0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (rewrite_id,section,attempt)
);
REVOKE ALL ON tft.draft_rewrites,tft.draft_rewrite_calls FROM tft_app,tft_worker;
GRANT SELECT,INSERT ON tft.draft_rewrites TO tft_app;
GRANT UPDATE(job_id) ON tft.draft_rewrites TO tft_app;
GRANT SELECT ON tft.draft_rewrites,tft.draft_rewrite_calls TO tft_worker;
GRANT UPDATE(state,result_version) ON tft.draft_rewrites TO tft_worker;
GRANT INSERT ON tft.draft_rewrite_calls TO tft_worker;
ALTER TABLE tft.rfp_commands DROP CONSTRAINT IF EXISTS rfp_commands_operation_check;
ALTER TABLE tft.rfp_commands ADD CONSTRAINT rfp_commands_operation_check CHECK (operation IN (
    'project.create','document.upload','supplement.select','question.create','question.reply','question.confirm',
    'proposal.create','proposal.approve','proposal.reject','project.process','annotation.process',
    'review_version.create','export.create','draft.rewrite'
));
COMMIT;
