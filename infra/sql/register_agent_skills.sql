-- KAN-214: record one C/S agent's languages and product families, outside any API.
-- Run as postgres in team4 with psql after registering the user (register_user.sql):
--
--   psql -X -d team4 -f infra/sql/register_agent_skills.sql \
--     -v user_id=<identity.users.id> -v languages='{en,th}' -v families='{LV_BREAKER,DRIVE}' \
--     -v active=true -v data_origin=demo
--
-- Values come from the company employee table (proposal p.23); until LS provides it they are
-- assumed data (derivation_kind ASSUMED). Running it again replaces the row. The user must be an
-- active cs_agent and every family must exist in product_master (contracts/cs/assignment.md).
\set ON_ERROR_STOP on
BEGIN;

SELECT set_config('skills.user_id', :'user_id', true),
       set_config('skills.languages', :'languages', true),
       set_config('skills.families', :'families', true),
       set_config('skills.active', :'active', true),
       set_config('skills.data_origin', :'data_origin', true);

DO $check$
DECLARE
    v_user uuid := current_setting('skills.user_id')::uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM identity.users u JOIN identity.user_roles r ON r.user_id = u.id
        WHERE u.id = v_user AND u.is_active AND r.role = 'cs_agent'
    ) THEN
        RAISE EXCEPTION 'Not an active cs_agent: %', v_user;
    END IF;
END
$check$;

SET LOCAL ROLE cs_migrator;
-- Array shape and codes are checked by the table constraints (C29). Product families are the
-- family column of backend/engine/cs/policy/product_master.csv.
INSERT INTO cs.agent_skills (user_id, languages, families, active, data_origin)
VALUES (
    current_setting('skills.user_id')::uuid,
    current_setting('skills.languages')::text[],
    current_setting('skills.families')::text[],
    current_setting('skills.active')::boolean,
    current_setting('skills.data_origin')
)
ON CONFLICT (user_id) DO UPDATE
    SET languages = EXCLUDED.languages, families = EXCLUDED.families,
        active = EXCLUDED.active, data_origin = EXCLUDED.data_origin, updated_at = now();

COMMIT;
