-- KAN-73: 수집한 메일의 업무 유형 분류 결과. 20261028_KAN-71_mail_sync.sql 뒤에 관리자 연결로 적용한다.
-- 분류마다 판본을 새로 쓰고 고치거나 지우지 않는다. 메일 원문·본문 텍스트는 저장하지 않는다.
-- 공용 DB에는 이 변경으로 적용하지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;

CREATE TABLE integration.mail_triage (
    message_id uuid NOT NULL REFERENCES integration.mail_messages(id),
    version integer NOT NULL CHECK (version > 0),
    -- TFT 신규 RFP·기존 프로젝트 변경·후보 회신, C/S 문의, 그 밖.
    mail_type text NOT NULL
        CHECK (mail_type IN ('NEW_RFP', 'EXISTING_CHANGE', 'CANDIDATE_REPLY', 'CS', 'OTHER')),
    needs_review boolean NOT NULL,
    attachments_pending boolean NOT NULL,
    -- 텍스트 없는 근거 위치(마스킹 입력의 제목·본문 구간, 회신 헤더)만.
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'array'),
    -- 연결 후보일 뿐 확정 연결이 아니다.
    link_suggestion text CHECK (link_suggestion IN ('NEW_PROJECT', 'EXISTING_THREAD', 'NEW_CS_TICKET')),
    provenance_kind text NOT NULL CHECK (provenance_kind IN ('ai_draft', 'human_input')),
    input_version text,
    output_version text,
    reason text CHECK (length(reason) BETWEEN 1 AND 2000),
    decided_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, version),
    -- 모델 분류는 입력·출력 판본을, 사람 재분류는 사유와 판정자를 남긴다.
    CHECK ((provenance_kind = 'ai_draft') = (input_version IS NOT NULL AND output_version IS NOT NULL)),
    CHECK ((provenance_kind = 'human_input') = (reason IS NOT NULL AND decided_by IS NOT NULL))
);

-- 메일별 최신 판본. 수집 표(KAN-71)는 바꾸지 않으려고 따로 둔다.
CREATE TABLE integration.mail_triage_latest (
    message_id uuid PRIMARY KEY REFERENCES integration.mail_messages(id),
    latest_version integer NOT NULL CHECK (latest_version > 0),
    FOREIGN KEY (message_id, latest_version) REFERENCES integration.mail_triage(message_id, version)
);

REVOKE ALL ON integration.mail_triage, integration.mail_triage_latest
    FROM PUBLIC, cs_app, tft_app;
GRANT SELECT, INSERT ON integration.mail_triage, integration.mail_triage_latest TO platform_app;
GRANT UPDATE (latest_version) ON integration.mail_triage_latest TO platform_app;
COMMIT;
