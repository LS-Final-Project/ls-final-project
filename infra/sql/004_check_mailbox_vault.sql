-- 보관용 마이그레이션을 적용한 환경에서만 운영자가 실행하는 별도 읽기 전용 검사.
-- 기존 기본 로그인 점검003의 선행 조건을 확대하지 않는다.
\set ON_ERROR_STOP on
BEGIN READ ONLY;
SET LOCAL statement_timeout='5s';
DO $check$
DECLARE
    role_name text;
    privilege_name text;
    relation_name text;
BEGIN
    IF current_database() NOT IN ('team4','kan77_auth_test') OR current_user<>'platform_app'
       OR current_setting('transaction_read_only')<>'on' THEN
        RAISE EXCEPTION 'Use platform_app in the expected database with a read-only transaction';
    END IF;
    IF to_regclass('mailbox_auth.connections') IS NULL THEN
        RAISE EXCEPTION 'Mailbox vault migration has not been applied';
    END IF;
    IF NOT has_schema_privilege(current_user,'mailbox_auth','USAGE')
       OR has_schema_privilege(current_user,'mailbox_auth','CREATE')
       OR has_table_privilege(current_user,'mailbox_auth.connections','TRUNCATE') THEN
        RAISE EXCEPTION 'Mailbox runtime privileges differ from the expected minimum';
    END IF;
    FOREACH privilege_name IN ARRAY ARRAY['SELECT','INSERT','DELETE']
    LOOP
        IF NOT has_table_privilege(current_user,'mailbox_auth.connections',privilege_name) THEN
            RAISE EXCEPTION 'A required mailbox runtime privilege is missing';
        END IF;
    END LOOP;
    -- UPDATE는 연결 상태·캐시 열만 필요하다. 재연결 상태 마이그레이션 뒤에는 그 열만 허용되고
    -- 소유자·Microsoft 식별자는 바꿀 수 없어야 한다.
    FOREACH relation_name IN ARRAY ARRAY['revision','state','key_version','cache_ciphertext','updated_at']
    LOOP
        IF NOT has_column_privilege(current_user,'mailbox_auth.connections',relation_name,'UPDATE') THEN
            RAISE EXCEPTION 'A required mailbox runtime privilege is missing';
        END IF;
    END LOOP;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns WHERE table_schema='mailbox_auth'
        AND table_name='connections' AND column_name='reconnect_required_at'
    ) THEN
        IF NOT has_column_privilege(current_user,'mailbox_auth.connections','reconnect_required_at','UPDATE')
           OR NOT has_column_privilege(current_user,'mailbox_auth.connections','reconnect_reason','UPDATE')
           OR has_table_privilege(current_user,'mailbox_auth.connections','UPDATE') THEN
            RAISE EXCEPTION 'Mailbox runtime update privileges differ from the expected columns';
        END IF;
        FOREACH relation_name IN ARRAY ARRAY['id','owner_user_id','subject_tid','subject_oid']
        LOOP
            IF has_column_privilege(current_user,'mailbox_auth.connections',relation_name,'UPDATE') THEN
                RAISE EXCEPTION 'Mailbox runtime may change a connection identity column';
            END IF;
        END LOOP;
    END IF;
    FOREACH relation_name IN ARRAY ARRAY['identity.users','identity.user_roles','identity.sessions']
    LOOP
        IF NOT has_table_privilege(current_user,relation_name,'SELECT')
           OR NOT has_table_privilege(current_user,relation_name,'UPDATE') THEN
            RAISE EXCEPTION 'Identity row lock privileges are unavailable';
        END IF;
    END LOOP;
    FOREACH role_name IN ARRAY ARRAY['cs_app','tft_app','evaluation_worker']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) AND (
            has_schema_privilege(role_name,'mailbox_auth','USAGE') OR
            has_table_privilege(role_name,'mailbox_auth.connections','SELECT')
        ) THEN
            RAISE EXCEPTION 'Another service has mailbox vault access';
        END IF;
    END LOOP;
    RAISE NOTICE 'PASS: mailbox vault table and runtime/service isolation';
END
$check$;
ROLLBACK;
