-- KAN-201 P1/P2, after P30. Re-runnable. No business role/worker receives push secrets.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
CREATE TABLE IF NOT EXISTS notify.push_subscriptions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES identity.users(id),
    endpoint text NOT NULL UNIQUE CHECK (length(endpoint) BETWEEN 1 AND 2048),
    p256dh text NOT NULL,
    auth text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS notify.push_deliveries (
    intent_id uuid NOT NULL REFERENCES notify.notification_intents(id),
    subscription_id uuid NOT NULL REFERENCES notify.push_subscriptions(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENDING','SENT','FAILED','UNKNOWN','SKIPPED')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    started_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(intent_id,subscription_id)
);
CREATE INDEX IF NOT EXISTS push_deliveries_pending ON notify.push_deliveries(next_attempt_at)
    WHERE status='PENDING';
REVOKE ALL ON notify.push_subscriptions,notify.push_deliveries
    FROM PUBLIC,cs_app,tft_app,cs_worker,tft_worker,platform_app;
GRANT SELECT,INSERT,DELETE ON notify.push_subscriptions TO platform_app;
GRANT SELECT,INSERT ON notify.push_deliveries TO platform_app;
GRANT UPDATE(status,attempts,started_at,next_attempt_at) ON notify.push_deliveries TO platform_app;
COMMIT;
