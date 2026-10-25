-- platform_app으로 실행하는 읽기 전용 점검. psql -X -v ON_ERROR_STOP=1 -f ... 로 실행한다.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';

DO $check$
DECLARE
    relation_name text;
    registered_personal bigint;
BEGIN
    IF current_database() NOT IN ('team4', 'kan77_auth_test') OR current_user <> 'platform_app'
       OR current_setting('transaction_read_only') <> 'on' THEN
        RAISE EXCEPTION 'Use platform_app in the expected database with a read-only transaction';
    END IF;
    FOREACH relation_name IN ARRAY ARRAY[
        'users', 'user_roles', 'oauth_authorizations', 'sessions',
        'departments', 'manager_departments', 'manager_projects'
    ]
    LOOP
        IF to_regclass('identity.' || relation_name) IS NULL THEN
            RAISE EXCEPTION 'Required auth table is missing: identity.%', relation_name;
        END IF;
        IF NOT has_table_privilege(current_user, 'identity.' || relation_name, 'SELECT') THEN
            RAISE EXCEPTION 'Runtime SELECT privilege is missing: identity.%', relation_name;
        END IF;
        IF relation_name IN ('oauth_authorizations', 'sessions') AND
           (NOT has_table_privilege(current_user, 'identity.' || relation_name, 'INSERT') OR
            NOT has_table_privilege(current_user, 'identity.' || relation_name, 'DELETE')) THEN
            RAISE EXCEPTION 'Runtime session write privilege is missing: identity.%', relation_name;
        END IF;
    END LOOP;
    SELECT count(*) INTO registered_personal
    FROM identity.users u
    WHERE u.is_active AND u.tid = '9188040d-6c67-4c5b-b112-36a304b66dad'::uuid
      AND EXISTS (SELECT 1 FROM identity.user_roles r WHERE r.user_id = u.id)
      AND (NULLIF(u.dept, '') IS NOT NULL OR NOT EXISTS (
          SELECT 1 FROM identity.user_roles r WHERE r.user_id = u.id AND r.role = 'dept_staff'
      ));
    IF registered_personal = 0 THEN
        RAISE EXCEPTION 'No eligible personal Microsoft account; registration must be checked separately';
    END IF;
    RAISE NOTICE 'PASS: auth tables/runtime privileges; eligible personal account count=%; verify the selected account separately', registered_personal;
END
$check$;

ROLLBACK;
