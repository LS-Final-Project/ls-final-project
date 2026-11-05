-- KAN-201: notification dismiss (hide) and read-all. Rows are never deleted; dismissed_at marks a hidden row.
-- P39, after P30 (20261101_KAN-201_notifications.sql). Re-runnable. Dismiss also sets read_at, so the
-- existing unread index stays valid.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
ALTER TABLE notify.user_notifications ADD COLUMN IF NOT EXISTS dismissed_at timestamptz;
REVOKE ALL (dismissed_at) ON notify.user_notifications
    FROM PUBLIC, cs_app, cs_worker, tft_app, tft_worker, platform_app;
GRANT UPDATE (dismissed_at) ON notify.user_notifications TO platform_app;
COMMIT;
