-- 플랫폼 런타임은 DDL을 실행하지 않는다. MSAL flow는 서버 DB에만 보관한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
CREATE TABLE IF NOT EXISTS identity.oauth_authorizations (
    state_hash text PRIMARY KEY CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    purpose text NOT NULL CHECK (purpose IN ('login','mailbox')),
    browser_binding_hash text NOT NULL CHECK (browser_binding_hash ~ '^[0-9a-f]{64}$'),
    owner_user_id uuid REFERENCES identity.users(id),
    expires_at timestamptz NOT NULL,
    provider_flow jsonb NOT NULL CHECK (jsonb_typeof(provider_flow)='object'),
    CHECK ((purpose='mailbox') = (owner_user_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS oauth_authorizations_expiry
    ON identity.oauth_authorizations(expires_at);
COMMIT;
