-- KAN-201 N1/N2. Apply after P1/P4(manager scopes), P7(runtime), P28/P29(worker roles).
-- Re-runnable, payload append-only. Only platform expands and updates processing/read metadata.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
CREATE TABLE IF NOT EXISTS runtime.notification_intents (
    id uuid PRIMARY KEY,
    owner text NOT NULL CHECK (owner IN ('cs','tft','platform')),
    kind text NOT NULL CHECK (kind IN ('cs.review_wait','cs.safety','cs.reply','mail.unknown','cs.transfer','rfp.done','rfp.failed')),
    title text NOT NULL CHECK (title IN ('C/S 답변 확인 대기','안전 문의 확인 필요','고객 회신 재검토','메일 발송 결과 확인 필요','이관 문의 수신','RFP 처리 완료','RFP 처리 완료, 검토 필요','RFP 처리 실패')),
    link_path text NOT NULL CHECK (link_path ~ '^/(cs/tickets|tft/projects)/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
    audience jsonb NOT NULL CHECK (jsonb_typeof(audience)='array' AND jsonb_array_length(audience) BETWEEN 1 AND 64),
    actor_id uuid,
    dedupe_key text NOT NULL UNIQUE CHECK (length(dedupe_key) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now(),
    expanded_at timestamptz
);
CREATE TABLE IF NOT EXISTS runtime.user_notifications (
    intent_id uuid NOT NULL REFERENCES runtime.notification_intents(id),
    user_id uuid NOT NULL REFERENCES identity.users(id),
    read_at timestamptz,
    PRIMARY KEY (intent_id,user_id)
);
CREATE INDEX IF NOT EXISTS notification_intents_pending
    ON runtime.notification_intents(created_at,id) WHERE expanded_at IS NULL;
CREATE INDEX IF NOT EXISTS user_notifications_unread
    ON runtime.user_notifications(user_id,intent_id) WHERE read_at IS NULL;

REVOKE ALL ON runtime.notification_intents, runtime.user_notifications
    FROM PUBLIC, cs_app, cs_worker, tft_app, tft_worker, platform_app;
-- Clear column grants too: REVOKE ALL at table level does not revoke column privileges.
REVOKE ALL (id,owner,kind,title,link_path,audience,actor_id,dedupe_key,created_at,expanded_at)
    ON runtime.notification_intents FROM cs_app, cs_worker, tft_app, tft_worker, platform_app;
REVOKE ALL (intent_id,user_id,read_at) ON runtime.user_notifications
    FROM cs_app, cs_worker, tft_app, tft_worker, platform_app;
GRANT USAGE ON SCHEMA runtime TO cs_app, cs_worker, tft_worker, platform_app;
GRANT INSERT (id,owner,kind,title,link_path,audience,actor_id,dedupe_key)
    ON runtime.notification_intents TO cs_app, cs_worker, tft_worker, platform_app;
GRANT SELECT ON runtime.notification_intents, runtime.user_notifications TO platform_app;
GRANT UPDATE (expanded_at) ON runtime.notification_intents TO platform_app;
GRANT INSERT (intent_id,user_id), UPDATE (read_at) ON runtime.user_notifications TO platform_app;
COMMIT;
