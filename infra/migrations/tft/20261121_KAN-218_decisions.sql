-- KAN-218: 결정 패널 저장(결정 기록, 편차 내용, 질문 철회).
-- 결정은 누르면 바로 확정한다. 결정 한 건은 requirement_decisions 한 행이고, 같은 사용자·Idempotency-Key의
-- 재전송은 이 행의 result를 돌려준다(rfp_commands와 같은 규칙).
-- 새 연산 이름·질문 상태 값을 기존 CHECK에 더하지 않는다. 그 CHECK를 다시 정의하는 이전 마이그레이션
-- (20261106·20261111·20261119·20261120)이 재적용될 때 새 값의 행이 있으면 실패하기 때문이다. 대신 새 표 셋을 둔다.
-- 추가 전용(앱은 SELECT·INSERT만). 다시 적용해도 깨지지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

CREATE TABLE IF NOT EXISTS tft.requirement_decisions (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    requirement_id uuid NOT NULL REFERENCES tft.requirements(id),
    -- 결정이 본 판본과 결정 뒤 최신 판본. 뒤 판본이 최신이 아니게 되면 결정은 다시 확인 대상이다.
    base_version integer NOT NULL CHECK (base_version > 0),
    result_version integer NOT NULL CHECK (result_version > 0),
    action text NOT NULL CHECK (action IN ('confirm_met', 'record_deviation', 'request_check',
        'add_query', 'select_partner', 'ask_rnd', 'assign', 'change_verdict', 'undo',
        'review_confirm', 'review_return')),
    summary text NOT NULL CHECK (length(summary) BETWEEN 1 AND 200),
    detail jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(detail) = 'object'),
    -- undo·review_return이 되돌린 결정.
    undoes uuid REFERENCES tft.requirement_decisions(id),
    actor_user_id uuid NOT NULL,
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (actor_user_id, idempotency_key),
    CHECK ((action IN ('undo', 'review_return')) = (undoes IS NOT NULL)),
    FOREIGN KEY (requirement_id, result_version) REFERENCES tft.requirement_versions(requirement_id, version)
);
CREATE INDEX IF NOT EXISTS requirement_decisions_latest
    ON tft.requirement_decisions (requirement_id, created_at DESC);
CREATE INDEX IF NOT EXISTS requirement_decisions_project ON tft.requirement_decisions (project_id);

-- 편차표의 행. 요구 판본을 올리지 않는다. 요구별 최신 행이 RECORDED면 편차표에 나간다.
CREATE TABLE IF NOT EXISTS tft.deviations (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    requirement_id uuid NOT NULL REFERENCES tft.requirements(id),
    requirement_version integer NOT NULL,
    text text NOT NULL CHECK (length(text) BETWEEN 1 AND 2000),
    state text NOT NULL CHECK (state IN ('RECORDED', 'WITHDRAWN')),
    decision_id uuid NOT NULL REFERENCES tft.requirement_decisions(id),
    decided_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (requirement_id, requirement_version) REFERENCES tft.requirement_versions(requirement_id, version)
);
CREATE INDEX IF NOT EXISTS deviations_latest ON tft.deviations (requirement_id, created_at DESC);

-- 보내지 않을 질문(고친 문장으로 새로 만든 AI 초안, 되돌린 결정의 질문). 질문 행은 그대로 둔다.
CREATE TABLE IF NOT EXISTS tft.question_withdrawals (
    question_id uuid PRIMARY KEY REFERENCES tft.customer_questions(id),
    decision_id uuid NOT NULL REFERENCES tft.requirement_decisions(id),
    withdrawn_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON tft.requirement_decisions, tft.deviations, tft.question_withdrawals FROM PUBLIC;
GRANT SELECT, INSERT ON tft.requirement_decisions, tft.deviations, tft.question_withdrawals TO tft_app;
REVOKE UPDATE, DELETE, TRUNCATE ON tft.requirement_decisions, tft.deviations, tft.question_withdrawals
    FROM tft_app;
COMMIT;
