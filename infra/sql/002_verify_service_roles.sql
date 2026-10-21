-- KAN-65: permission checks using disposable objects, rolled back on success.
\set ON_ERROR_STOP on

BEGIN;

DO $verify$
DECLARE
    mapping record;
    schema_mapping record;
    role_attributes record;
    observed integer;
BEGIN
    IF current_database() <> 'team4' OR current_user <> 'postgres' THEN
        RAISE EXCEPTION 'Run this script as postgres in database team4';
    END IF;

    -- Build real objects as the role that will run migrations. This exercises
    -- future table, sequence and function grants, not just current ACL strings.
    FOR mapping IN
        SELECT * FROM (VALUES
            ('cs', 'cs_migrator'),
            ('tft', 'tft_migrator'),
            ('identity', 'platform_migrator')
        ) AS mappings(schema_name, owner_role)
    LOOP
        EXECUTE format('SET LOCAL ROLE %I', mapping.owner_role);
        EXECUTE format('CREATE TABLE %I.__kan65_acl_probe (id bigserial PRIMARY KEY, value integer NOT NULL DEFAULT 1)', mapping.schema_name);
        EXECUTE format('CREATE FUNCTION %I.__kan65_acl_probe_fn() RETURNS integer LANGUAGE sql AS %L', mapping.schema_name, 'SELECT 1');
        RESET ROLE;
    END LOOP;

    FOR mapping IN
        SELECT * FROM (VALUES
            ('cs_app', 'cs', 'cs_migrator'),
            ('tft_app', 'tft', 'tft_migrator'),
            ('platform_app', 'identity', 'platform_migrator')
        ) AS mappings(app_role, own_schema, owner_role)
    LOOP
        SELECT * INTO STRICT role_attributes FROM pg_roles WHERE rolname = mapping.app_role;
        IF NOT role_attributes.rolcanlogin OR role_attributes.rolsuper
           OR role_attributes.rolcreatedb OR role_attributes.rolcreaterole
           OR role_attributes.rolreplication OR role_attributes.rolbypassrls THEN
            RAISE EXCEPTION 'Unexpected privileged attributes on %', mapping.app_role;
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_auth_members
            WHERE member = role_attributes.oid
        ) THEN
            RAISE EXCEPTION 'Unexpected role membership on %', mapping.app_role;
        END IF;

        FOR schema_mapping IN
            SELECT * FROM (VALUES
                ('cs', 'cs_app'), ('checkpoint_cs', 'cs_app'),
                ('tft', 'tft_app'), ('checkpoint_tft', 'tft_app'),
                ('identity', 'platform_app'), ('integration', 'platform_app'),
                ('catalog', 'platform_app'), ('evaluation', 'platform_app')
            ) AS schemas(schema_name, app_role)
        LOOP
            IF has_schema_privilege(mapping.app_role, schema_mapping.schema_name, 'USAGE')
               IS DISTINCT FROM (mapping.app_role = schema_mapping.app_role) THEN
                RAISE EXCEPTION 'Schema access mismatch: %, %', mapping.app_role, schema_mapping.schema_name;
            END IF;
            IF has_schema_privilege(mapping.app_role, schema_mapping.schema_name, 'CREATE') THEN
                RAISE EXCEPTION 'App has schema CREATE: %, %', mapping.app_role, schema_mapping.schema_name;
            END IF;
        END LOOP;
        IF has_database_privilege(mapping.app_role, 'team4', 'CREATE')
           OR has_database_privilege(mapping.app_role, 'team4', 'TEMP')
           OR has_schema_privilege(mapping.app_role, 'public', 'CREATE') THEN
            RAISE EXCEPTION 'App has unexpected object creation rights: %', mapping.app_role;
        END IF;

        EXECUTE format('SET LOCAL ROLE %I', mapping.app_role);
        EXECUTE format('INSERT INTO %I.__kan65_acl_probe DEFAULT VALUES', mapping.own_schema);
        EXECUTE format('UPDATE %I.__kan65_acl_probe SET value = 2', mapping.own_schema);
        EXECUTE format('SELECT value FROM %I.__kan65_acl_probe', mapping.own_schema) INTO STRICT observed;
        IF observed <> 2 THEN
            RAISE EXCEPTION 'Own-table CRUD failed: %', mapping.app_role;
        END IF;
        EXECUTE format('SELECT %I.__kan65_acl_probe_fn()', mapping.own_schema) INTO observed;
        IF observed <> 1 THEN
            RAISE EXCEPTION 'Own-function execution failed: %', mapping.app_role;
        END IF;

        FOR schema_mapping IN SELECT unnest(ARRAY['cs', 'tft', 'identity']) AS schema_name LOOP
            IF schema_mapping.schema_name <> mapping.own_schema THEN
                BEGIN
                    EXECUTE format('SELECT * FROM %I.__kan65_acl_probe', schema_mapping.schema_name);
                    RAISE EXCEPTION 'Cross-service SELECT unexpectedly allowed';
                EXCEPTION WHEN insufficient_privilege THEN NULL;
                END;
                BEGIN
                    EXECUTE format('INSERT INTO %I.__kan65_acl_probe DEFAULT VALUES', schema_mapping.schema_name);
                    RAISE EXCEPTION 'Cross-service INSERT unexpectedly allowed';
                EXCEPTION WHEN insufficient_privilege THEN NULL;
                END;
            END IF;
        END LOOP;
        BEGIN
            EXECUTE format('CREATE TABLE %I.__kan65_forbidden (id integer)', mapping.own_schema);
            RAISE EXCEPTION 'App CREATE TABLE unexpectedly allowed';
        EXCEPTION WHEN insufficient_privilege THEN NULL;
        END;
        EXECUTE format('DELETE FROM %I.__kan65_acl_probe', mapping.own_schema);
        RESET ROLE;
    END LOOP;
END
$verify$;

ROLLBACK;
\echo 'PASS: app CRUD, schema isolation, DDL denial and absence of inherited roles. Probe objects rolled back.'
