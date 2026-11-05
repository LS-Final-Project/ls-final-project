-- KAN-75: 메일과 업무의 연결(회신·스레드)과 사람이 판단할 회신 검토. 관리자 연결로 적용한다.
-- 20261028_KAN-71_mail_sync.sql·20261028_KAN-74_mail_send.sql 뒤에 적용한다. 메일 본문·주소는 저장하지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;

-- 메일 한 통과 업무 객체의 연결. 한 번 쓰면 바꾸지 않는다.
-- ORIGIN: 업무를 만든 원 메일(업무 쪽이 기록), REPLY: 그 업무에 대한 회신.
-- 근거: BUSINESS(업무가 지정), HEADER(In-Reply-To·References), THREAD(Graph conversationId), HUMAN(사람 확인).
CREATE TABLE integration.mail_links (
    id uuid PRIMARY KEY,
    message_id uuid NOT NULL REFERENCES integration.mail_messages(id),
    owner_service text NOT NULL CHECK (owner_service IN ('cs', 'platform')),
    business_ref text NOT NULL CHECK (business_ref ~ '^[a-z][a-z_]{1,31}:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    kind text NOT NULL CHECK (kind IN ('ORIGIN', 'REPLY')),
    method text NOT NULL CHECK (method IN ('BUSINESS', 'HEADER', 'THREAD', 'HUMAN')),
    decided_by uuid REFERENCES identity.users(id),
    note text CHECK (length(note) BETWEEN 1 AND 500),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id, owner_service, business_ref),
    -- 원 메일은 업무가, 회신은 헤더·스레드·사람만 연결한다. 사람 연결은 판단자와 사유가 있다.
    CHECK ((kind = 'ORIGIN') = (method = 'BUSINESS')),
    CHECK ((method = 'HUMAN') = (decided_by IS NOT NULL AND note IS NOT NULL))
);
CREATE INDEX mail_links_business ON integration.mail_links (owner_service, business_ref);

-- 자동으로 연결하지 않은 회신. 메일 한 통에 하나.
CREATE TABLE integration.mail_reply_reviews (
    message_id uuid PRIMARY KEY REFERENCES integration.mail_messages(id),
    -- AMBIGUOUS: 후보가 여러 업무, UNMATCHED_REPLY: 회신 표시가 있는데 맞는 업무 없음,
    -- CONFLICT: 헤더와 스레드가 다른 업무를 가리킴, TRIAGE_DISAGREES: 분류가 기존 스레드가 아니라고 봄.
    reason text NOT NULL CHECK (reason IN ('AMBIGUOUS', 'UNMATCHED_REPLY', 'CONFLICT', 'TRIAGE_DISAGREES')),
    -- [{"owner_service", "business_ref", "method"}]
    candidates jsonb NOT NULL CHECK (jsonb_typeof(candidates) = 'array'),
    status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'LINKED', 'DISMISSED')),
    decided_by uuid REFERENCES identity.users(id),
    decided_at timestamptz,
    note text CHECK (length(note) BETWEEN 1 AND 500),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'OPEN') = (decided_by IS NULL)),
    CHECK ((status = 'OPEN') = (decided_at IS NULL)),
    CHECK ((status = 'OPEN') = (note IS NULL))
);
CREATE INDEX mail_reply_reviews_open ON integration.mail_reply_reviews (status, created_at);

-- 받은 메일마다 회신 판정을 한 번 했다는 표시. 수집 워커가 판정하지 않은 메일만 고른다.
CREATE TABLE integration.mail_reply_checks (
    message_id uuid PRIMARY KEY REFERENCES integration.mail_messages(id),
    outcome text NOT NULL CHECK (outcome IN ('LINKED', 'REVIEW', 'NONE')),
    checked_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON integration.mail_links, integration.mail_reply_reviews, integration.mail_reply_checks
    FROM PUBLIC, cs_app, tft_app;
GRANT SELECT, INSERT ON integration.mail_links, integration.mail_reply_reviews,
    integration.mail_reply_checks TO platform_app;
-- 사람이 검토를 결정하면 판정 결과만 바꾼다.
GRANT UPDATE (outcome, checked_at) ON integration.mail_reply_checks TO platform_app;
-- 검토는 결정 칸만 바꾼다. 후보·이유는 고치지 못한다.
GRANT UPDATE (status, decided_by, decided_at, note) ON integration.mail_reply_reviews TO platform_app;
COMMIT;
