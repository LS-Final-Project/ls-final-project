-- KAN-78: users.dept uses the same department codes as manager scopes.
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
-- NOT VALID: new and changed rows are checked; rows from before this migration
-- (for example the old local seed value 'technical') stay until the DB is recreated
-- or the user is registered again with infra/sql/register_user.sql.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;

DO $fk$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'users_dept_fkey' AND conrelid = 'identity.users'::regclass
    ) THEN
        ALTER TABLE identity.users
            ADD CONSTRAINT users_dept_fkey FOREIGN KEY (dept)
            REFERENCES identity.departments(code) NOT VALID;
    END IF;
END
$fk$;

COMMIT;
