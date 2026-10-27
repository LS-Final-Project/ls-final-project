-- KAN-71: 메일 수집 실행·커서, 메시지·첨부의 원문 위치. 관리자 연결로 적용한다.
-- 사서함 연결은 KAN-81의 mailbox_auth.connections를 참조한다(20261027_KAN-81_mailbox_vault.sql 뒤에 적용).
-- 토큰·MSAL 캐시는 이 표에 두지 않는다. 원문 바이트는 객체 저장소에 두고 위치·해시만 기록한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
-- 수집 실행 한 번. 사서함마다 RUNNING은 하나뿐이다.
CREATE TABLE integration.mail_sync_runs (
    id uuid PRIMARY KEY,
    mailbox_id uuid NOT NULL REFERENCES mailbox_auth.connections(id),
    status text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    started_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz NOT NULL,
    finished_at timestamptz,
    -- 고정 코드만 기록한다. 공급자 원시 오류·토큰·URL은 넣지 않는다.
    error_code text CHECK (error_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    messages_stored integer NOT NULL DEFAULT 0 CHECK (messages_stored >= 0),
    messages_removed integer NOT NULL DEFAULT 0 CHECK (messages_removed >= 0),
    CHECK ((status = 'RUNNING') = (finished_at IS NULL)),
    CHECK ((status = 'FAILED') = (error_code IS NOT NULL))
);
CREATE UNIQUE INDEX mail_sync_runs_one_running
    ON integration.mail_sync_runs (mailbox_id) WHERE status = 'RUNNING';

-- 폴더별 Graph delta 커서. next_link는 진행 중 페이지, delta_link는 완료된 동기화 지점이다.
CREATE TABLE integration.mail_sync_cursors (
    mailbox_id uuid NOT NULL REFERENCES mailbox_auth.connections(id),
    folder text NOT NULL CHECK (folder IN ('inbox', 'sentitems')),
    next_link text,
    delta_link text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mailbox_id, folder)
);

-- 메시지. graph_id는 ImmutableId이며 사서함 안에서 유일하다. 삭제·이동은 removed_at으로 남긴다.
CREATE TABLE integration.mail_messages (
    id uuid PRIMARY KEY,
    mailbox_id uuid NOT NULL REFERENCES mailbox_auth.connections(id),
    graph_id text NOT NULL CHECK (length(graph_id) BETWEEN 1 AND 512),
    folder text NOT NULL CHECK (folder IN ('inbox', 'sentitems')),
    internet_message_id text,
    conversation_id text,
    subject text,
    from_address text,
    received_at timestamptz NOT NULL,
    sent_at timestamptz,
    has_attachments boolean NOT NULL,
    raw_storage_key text NOT NULL,
    raw_sha256 text NOT NULL CHECK (raw_sha256 ~ '^[0-9a-f]{64}$'),
    raw_size bigint NOT NULL CHECK (raw_size >= 0),
    first_sync_run_id uuid NOT NULL REFERENCES integration.mail_sync_runs(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    removed_at timestamptz,
    UNIQUE (mailbox_id, graph_id)
);
CREATE INDEX mail_messages_received ON integration.mail_messages (mailbox_id, received_at);
CREATE INDEX mail_messages_conversation ON integration.mail_messages (mailbox_id, conversation_id);

-- 첨부. 링크형(reference) 첨부는 바이트가 없어 위치·해시를 비운다.
CREATE TABLE integration.mail_attachments (
    id uuid PRIMARY KEY,
    message_id uuid NOT NULL REFERENCES integration.mail_messages(id),
    graph_attachment_id text NOT NULL CHECK (length(graph_attachment_id) BETWEEN 1 AND 512),
    kind text NOT NULL CHECK (kind IN ('file', 'item', 'reference')),
    name text,
    content_type text,
    is_inline boolean NOT NULL,
    storage_key text,
    sha256 text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size bigint CHECK (size >= 0),
    CHECK ((kind = 'reference') = (storage_key IS NULL)),
    CHECK ((storage_key IS NULL) = (sha256 IS NULL) AND (sha256 IS NULL) = (size IS NULL)),
    UNIQUE (message_id, graph_attachment_id)
);

REVOKE ALL ON integration.mail_sync_runs, integration.mail_sync_cursors,
    integration.mail_messages, integration.mail_attachments FROM PUBLIC, cs_app, tft_app;
-- 메일 삭제도 행을 지우지 않고 removed_at으로 남긴다.
REVOKE DELETE ON integration.mail_sync_runs, integration.mail_sync_cursors,
    integration.mail_messages, integration.mail_attachments FROM platform_app;
GRANT SELECT, INSERT, UPDATE ON integration.mail_sync_runs, integration.mail_sync_cursors,
    integration.mail_messages, integration.mail_attachments TO platform_app;
COMMIT;
