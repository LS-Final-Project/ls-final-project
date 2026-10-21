-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
CREATE TABLE IF NOT EXISTS identity.users (
    id uuid PRIMARY KEY,
    tid uuid NOT NULL,
    oid uuid NOT NULL,
    email text NOT NULL,
    display_name text NOT NULL,
    dept text,
    is_active boolean NOT NULL DEFAULT true,
    UNIQUE (tid, oid)
);
CREATE TABLE IF NOT EXISTS identity.user_roles (
    user_id uuid NOT NULL REFERENCES identity.users(id),
    role text NOT NULL CHECK (role IN ('cs_agent','dept_staff','manager','sales')),
    PRIMARY KEY(user_id,role)
);
CREATE TABLE IF NOT EXISTS identity.dev_sessions (
    token_hash text PRIMARY KEY CHECK(length(token_hash)=64),
    user_id uuid NOT NULL REFERENCES identity.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS dev_sessions_expires_at ON identity.dev_sessions(expires_at);
COMMIT;
