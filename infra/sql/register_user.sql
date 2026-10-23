-- KAN-78: register one user with roles and manager scopes, outside any API.
-- Run as postgres in team4 with psql. Values come from psql variables:
--
--   psql -X -d team4 -f infra/sql/register_user.sql \
--     -v tid=<tenant uuid> -v oid=<object uuid> \
--     -v email=<email> -v display_name=<name> -v dept=<department code or empty> \
--     -v roles='{manager}' -v managed_depts='{GCS_L1}' -v managed_projects='{}'
--
-- The user is matched by tid+oid. Roles and scopes are replaced by the given values,
-- so running it again with fewer roles removes the others. Values are set by the
-- company administrator (permissions.md 1.2); the script only records them.
\set ON_ERROR_STOP on
BEGIN;

SELECT set_config('register.tid', :'tid', true),
       set_config('register.oid', :'oid', true),
       set_config('register.email', :'email', true),
       set_config('register.display_name', :'display_name', true),
       set_config('register.dept', :'dept', true),
       set_config('register.roles', :'roles', true),
       set_config('register.managed_depts', :'managed_depts', true),
       set_config('register.managed_projects', :'managed_projects', true);

-- TFT projects live in another service's schema; check them before switching role.
DO $check$
DECLARE
    projects uuid[] := current_setting('register.managed_projects')::uuid[];
    missing uuid[];
BEGIN
    IF cardinality(projects) > 0 THEN
        IF to_regclass('tft.projects') IS NULL THEN
            RAISE EXCEPTION 'tft.projects does not exist';
        END IF;
        SELECT array_agg(p) INTO missing FROM unnest(projects) AS p
        WHERE NOT EXISTS (SELECT 1 FROM tft.projects t WHERE t.id = p);
        IF missing IS NOT NULL THEN
            RAISE EXCEPTION 'Unknown TFT projects: %', missing;
        END IF;
    END IF;
END
$check$;

SET LOCAL ROLE platform_migrator;

DO $register$
DECLARE
    v_tid uuid := current_setting('register.tid')::uuid;
    v_oid uuid := current_setting('register.oid')::uuid;
    v_dept text := nullif(current_setting('register.dept'), '');
    v_roles text[] := current_setting('register.roles')::text[];
    v_depts text[] := current_setting('register.managed_depts')::text[];
    v_projects uuid[] := current_setting('register.managed_projects')::uuid[];
    v_user uuid;
BEGIN
    IF cardinality(v_roles) = 0 THEN
        RAISE EXCEPTION 'At least one role is required';
    END IF;
    IF 'dept_staff' = ANY (v_roles) AND v_dept IS NULL THEN
        RAISE EXCEPTION 'dept_staff requires a department';
    END IF;
    IF v_dept IS NOT NULL AND NOT EXISTS (SELECT 1 FROM identity.departments WHERE code = v_dept) THEN
        RAISE EXCEPTION 'Unknown department: %', v_dept;
    END IF;
    IF NOT ('manager' = ANY (v_roles)) AND (cardinality(v_depts) > 0 OR cardinality(v_projects) > 0) THEN
        RAISE EXCEPTION 'Only a manager can have managed departments or projects';
    END IF;

    INSERT INTO identity.users (id, tid, oid, email, display_name, dept)
    VALUES (gen_random_uuid(), v_tid, v_oid, current_setting('register.email'),
            current_setting('register.display_name'), v_dept)
    ON CONFLICT (tid, oid) DO UPDATE
        SET email = EXCLUDED.email, display_name = EXCLUDED.display_name,
            dept = EXCLUDED.dept, is_active = true
    RETURNING id INTO v_user;

    DELETE FROM identity.user_roles WHERE user_id = v_user;
    INSERT INTO identity.user_roles (user_id, role) SELECT v_user, unnest(v_roles);

    -- Unknown department codes fail on the foreign key.
    DELETE FROM identity.manager_departments WHERE user_id = v_user;
    INSERT INTO identity.manager_departments (user_id, dept_code) SELECT v_user, unnest(v_depts);

    DELETE FROM identity.manager_projects WHERE user_id = v_user;
    INSERT INTO identity.manager_projects (user_id, project_id) SELECT v_user, unnest(v_projects);

    RAISE NOTICE 'Registered user %', v_user;
END
$register$;

COMMIT;
