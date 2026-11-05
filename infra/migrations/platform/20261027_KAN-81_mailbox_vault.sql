-- 운영자 postgres 연결에서만 적용한다. HTTP·수집·토큰 제공을 활성화하지 않는다.
\set ON_ERROR_STOP on
BEGIN;
DO $guard$
BEGIN
    IF current_database() NOT IN ('team4','kan77_auth_test') OR current_user<>'postgres' THEN
        RAISE EXCEPTION 'Use the operator postgres connection in the expected database';
    END IF;
END
$guard$;
CREATE SCHEMA mailbox_auth AUTHORIZATION platform_migrator;
SET LOCAL ROLE platform_migrator;
REVOKE ALL ON SCHEMA mailbox_auth FROM PUBLIC;
GRANT USAGE ON SCHEMA mailbox_auth TO platform_app;

CREATE TABLE mailbox_auth.connections (
    id uuid PRIMARY KEY,
    owner_user_id uuid NOT NULL REFERENCES identity.users(id),
    subject_tid uuid NOT NULL CHECK (subject_tid='9188040d-6c67-4c5b-b112-36a304b66dad'::uuid),
    subject_oid uuid NOT NULL CHECK (subject_oid<>'00000000-0000-0000-0000-000000000000'::uuid),
    revision bigint NOT NULL CHECK (revision>0),
    state text NOT NULL CHECK (state IN ('connected','disconnected')),
    key_version text NOT NULL CHECK (key_version ~ '^[A-Za-z0-9._-]{1,64}$'),
    cache_ciphertext bytea,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((state='connected') = (cache_ciphertext IS NOT NULL)),
    CHECK (cache_ciphertext IS NULL OR octet_length(cache_ciphertext)>28)
);
CREATE INDEX mailbox_connections_owner ON mailbox_auth.connections(owner_user_id);
REVOKE ALL ON mailbox_auth.connections FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE,DELETE ON mailbox_auth.connections TO platform_app;
COMMIT;
