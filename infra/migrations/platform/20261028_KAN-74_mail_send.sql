-- KAN-74: 승인된 메일 발송 의도와 감사 이력. 관리자 연결로 적용한다.
-- KAN-81 mailbox_vault, KAN-78 identity, KAN-71 mail_sync 뒤에 적용한다(integration 스키마 사용).
-- 본문·수신자·첨부 위치와 승인 시점 payload 해시를 함께 저장한다. 첨부 바이트는 객체 저장소에 둔다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;

-- 발송 의도 하나. 같은 업무의 같은 판본은 한 번만 만든다(같은 승인본을 두 번 보내지 않는다).
CREATE TABLE integration.mail_send_intents (
    id uuid PRIMARY KEY,
    mailbox_id uuid NOT NULL REFERENCES mailbox_auth.connections(id),
    owner_service text NOT NULL CHECK (owner_service IN ('cs', 'tft', 'platform')),
    -- 업무 객체 참조(예: cs_ticket:TCK-000123)와 그 승인 판본.
    business_ref text NOT NULL CHECK (business_ref ~ '^[a-z][a-z_]{1,31}:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    business_version integer NOT NULL CHECK (business_version >= 1),
    approved_by uuid NOT NULL REFERENCES identity.users(id),
    approved_at timestamptz NOT NULL,
    subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
    body_type text NOT NULL CHECK (body_type IN ('text', 'html')),
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 1000000),
    -- {"to": [주소...], "cc": [주소...]}
    recipients jsonb NOT NULL CHECK (jsonb_typeof(recipients) = 'object'),
    -- [{"name", "content_type", "storage_key", "sha256", "size"}]
    attachments jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(attachments) = 'array'),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED', 'SUBMITTING', 'ACCEPTED', 'UNKNOWN', 'FAILED')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    -- 고정 코드만 기록한다. 공급자 원시 오류·토큰·URL은 넣지 않는다.
    error_code text CHECK (error_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz,
    accepted_at timestamptz,
    -- 보낸편지함 사본 확인. 배달 완료가 아니다.
    sent_copy_confirmed_at timestamptz,
    sent_graph_id text CHECK (length(sent_graph_id) BETWEEN 1 AND 512),
    sent_internet_message_id text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_service, business_ref, business_version),
    CHECK (status <> 'FAILED' OR error_code IS NOT NULL),
    CHECK ((status = 'ACCEPTED') = (accepted_at IS NOT NULL)),
    CHECK (sent_copy_confirmed_at IS NULL OR status = 'ACCEPTED'),
    CHECK ((sent_copy_confirmed_at IS NULL) = (sent_graph_id IS NULL))
);
CREATE INDEX mail_send_intents_status ON integration.mail_send_intents (status, updated_at);

-- 감사 이력. 한 번 쓰면 바꾸지 않는다. actor_user_id가 비어 있으면 시스템 처리다.
CREATE TABLE integration.mail_send_events (
    id uuid PRIMARY KEY,
    intent_id uuid NOT NULL REFERENCES integration.mail_send_intents(id),
    at timestamptz NOT NULL DEFAULT now(),
    event text NOT NULL CHECK (event IN (
        'QUEUED', 'SUBMITTING', 'ACCEPTED', 'DEFERRED', 'UNKNOWN', 'FAILED',
        'SENT_COPY_CONFIRMED', 'SENT_COPY_NOT_FOUND', 'RESEND_AUTHORIZED'
    )),
    actor_user_id uuid REFERENCES identity.users(id),
    code text CHECK (code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    note text CHECK (length(note) <= 500),
    CHECK (event <> 'RESEND_AUTHORIZED' OR (actor_user_id IS NOT NULL AND note IS NOT NULL))
);
CREATE INDEX mail_send_events_intent ON integration.mail_send_events (intent_id, at);

REVOKE ALL ON integration.mail_send_intents, integration.mail_send_events
    FROM PUBLIC, cs_app, tft_app;
REVOKE DELETE ON integration.mail_send_intents, integration.mail_send_events FROM platform_app;
GRANT SELECT, INSERT, UPDATE ON integration.mail_send_intents TO platform_app;
-- 이력은 추가만 한다.
REVOKE UPDATE ON integration.mail_send_events FROM platform_app;
GRANT SELECT, INSERT ON integration.mail_send_events TO platform_app;
COMMIT;
