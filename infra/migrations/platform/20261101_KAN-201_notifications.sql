-- KAN-201 N1/N2. Apply after P1/P4(manager scopes), P7(runtime), P28/P29(worker roles).
-- Re-runnable, payload append-only. Only platform expands and updates processing/read metadata.
\set ON_ERROR_STOP on
BEGIN;
-- Schema creation follows P7: the operator creates it, then DDL uses the owning migrator.
-- No new database CREATE privilege or role membership is granted to runtime/migration roles.
CREATE SCHEMA IF NOT EXISTS notify AUTHORIZATION platform_migrator;
SET LOCAL ROLE platform_migrator;
REVOKE ALL ON SCHEMA notify FROM PUBLIC, cs_app, tft_app, cs_worker, tft_worker, platform_app;
-- Undo the old P30 grant only for business apps. Existing worker/platform runtime rights remain.
REVOKE USAGE ON SCHEMA runtime FROM cs_app, tft_app;

-- Upgrading the old P30 preserves intent IDs, recipient/read rows, indexes and FK/PK constraints.
-- Two live copies are ambiguous: fail atomically instead of hiding or deleting either history.
DO $$
BEGIN
    IF to_regclass('runtime.notification_intents') IS NOT NULL THEN
        IF to_regclass('notify.notification_intents') IS NOT NULL THEN
            RAISE EXCEPTION 'NOTIFICATION_SCHEMA_CONFLICT';
        END IF;
        ALTER TABLE runtime.notification_intents SET SCHEMA notify;
    END IF;
    IF to_regclass('runtime.user_notifications') IS NOT NULL THEN
        IF to_regclass('notify.user_notifications') IS NOT NULL THEN
            RAISE EXCEPTION 'NOTIFICATION_SCHEMA_CONFLICT';
        END IF;
        ALTER TABLE runtime.user_notifications SET SCHEMA notify;
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS notify.notification_intents (
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
CREATE TABLE IF NOT EXISTS notify.user_notifications (
    intent_id uuid NOT NULL REFERENCES notify.notification_intents(id),
    user_id uuid NOT NULL REFERENCES identity.users(id),
    read_at timestamptz,
    PRIMARY KEY (intent_id,user_id)
);
CREATE INDEX IF NOT EXISTS notification_intents_pending
    ON notify.notification_intents(created_at,id) WHERE expanded_at IS NULL;
CREATE INDEX IF NOT EXISTS user_notifications_unread
    ON notify.user_notifications(user_id,intent_id) WHERE read_at IS NULL;

REVOKE ALL ON notify.notification_intents, notify.user_notifications
    FROM PUBLIC, cs_app, cs_worker, tft_app, tft_worker, platform_app;
-- Clear column grants too: REVOKE ALL at table level does not revoke column privileges.
REVOKE ALL (id,owner,kind,title,link_path,audience,actor_id,dedupe_key,created_at,expanded_at)
    ON notify.notification_intents FROM cs_app, cs_worker, tft_app, tft_worker, platform_app;
REVOKE ALL (intent_id,user_id,read_at) ON notify.user_notifications
    FROM cs_app, cs_worker, tft_app, tft_worker, platform_app;
GRANT USAGE ON SCHEMA notify TO cs_app, tft_app, cs_worker, tft_worker, platform_app;
GRANT INSERT (id,owner,kind,title,link_path,audience,actor_id,dedupe_key)
    ON notify.notification_intents TO cs_app, tft_app, cs_worker, tft_worker, platform_app;
GRANT SELECT ON notify.notification_intents, notify.user_notifications TO platform_app;
GRANT UPDATE (expanded_at) ON notify.notification_intents TO platform_app;
GRANT INSERT (intent_id,user_id), UPDATE (read_at) ON notify.user_notifications TO platform_app;
COMMIT;
