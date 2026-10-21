-- KAN-65: run once as postgres in the team4 database with psql.
-- Passwords are deliberately absent. Set them with psql's \password command.
\set ON_ERROR_STOP on

BEGIN;

DO $guard$
BEGIN
    IF current_database() <> 'team4' OR current_user <> 'postgres' THEN
        RAISE EXCEPTION 'Run this script as postgres in database team4';
    END IF;
END
$guard$;

-- These roles cannot log in. Administrators SET ROLE for schema migrations.
CREATE ROLE cs_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE tft_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE platform_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE ROLE cs_app LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE tft_app LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE platform_app LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Explicit membership also supports Cloud SQL's non-superuser postgres account.
-- No app role receives membership in a migration or other service role.
GRANT cs_migrator, tft_migrator, platform_migrator,
      cs_app, tft_app, platform_app TO postgres;

REVOKE ALL ON DATABASE team4 FROM PUBLIC;
GRANT CONNECT ON DATABASE team4 TO
    cs_migrator, tft_migrator, platform_migrator,
    cs_app, tft_app, platform_app;

-- public is reserved for administrator-installed extensions such as pgvector.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO
    cs_migrator, tft_migrator, platform_migrator,
    cs_app, tft_app, platform_app;

-- PUBLIC function execution must be revoked globally, before per-schema grants.
ALTER DEFAULT PRIVILEGES FOR ROLE cs_migrator REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE tft_migrator REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE platform_migrator REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

DO $schemas$
DECLARE
    mapping record;
BEGIN
    FOR mapping IN
        SELECT * FROM (VALUES
            ('cs', 'cs_migrator', 'cs_app'),
            ('checkpoint_cs', 'cs_migrator', 'cs_app'),
            ('tft', 'tft_migrator', 'tft_app'),
            ('checkpoint_tft', 'tft_migrator', 'tft_app'),
            ('identity', 'platform_migrator', 'platform_app'),
            ('integration', 'platform_migrator', 'platform_app'),
            ('catalog', 'platform_migrator', 'platform_app'),
            ('evaluation', 'platform_migrator', 'platform_app')
        ) AS mappings(schema_name, owner_role, app_role)
    LOOP
        EXECUTE format('CREATE SCHEMA %I AUTHORIZATION %I', mapping.schema_name, mapping.owner_role);
        EXECUTE format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', mapping.schema_name);
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', mapping.schema_name, mapping.app_role);
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
            mapping.owner_role, mapping.schema_name, mapping.app_role
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO %I',
            mapping.owner_role, mapping.schema_name, mapping.app_role
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT EXECUTE ON FUNCTIONS TO %I',
            mapping.owner_role, mapping.schema_name, mapping.app_role
        );
    END LOOP;
END
$schemas$;

ALTER ROLE cs_app IN DATABASE team4 SET search_path = cs, public;
ALTER ROLE tft_app IN DATABASE team4 SET search_path = tft, public;
ALTER ROLE platform_app IN DATABASE team4 SET search_path = identity, integration, catalog, evaluation, public;

COMMIT;
\echo 'Service roles and schemas created. Run 002_verify_service_roles.sql next.'
