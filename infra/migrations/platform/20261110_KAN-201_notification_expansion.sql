-- KAN-201: expand recipients at intent creation, never from a GET request.
-- P34, after P30. Re-runnable; no runtime role gains identity or recipient access.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;

CREATE OR REPLACE FUNCTION notify.expand_notification_intent(p_intent uuid)
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    PERFORM id FROM notify.notification_intents
        WHERE id=p_intent AND expanded_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    INSERT INTO notify.user_notifications(intent_id,user_id)
    SELECT DISTINCT i.id,u.id
    FROM notify.notification_intents i
    CROSS JOIN LATERAL jsonb_array_elements(i.audience) s
    JOIN identity.users u ON u.is_active AND (
        u.id::text=s->>'user'
        OR (s->>'role'='cs_agent' AND EXISTS (
            SELECT 1 FROM identity.user_roles r WHERE r.user_id=u.id AND r.role='cs_agent'))
        OR (u.dept=s->>'dept' AND EXISTS (
            SELECT 1 FROM identity.user_roles r WHERE r.user_id=u.id AND r.role='dept_staff'))
        OR (EXISTS (SELECT 1 FROM identity.user_roles r WHERE r.user_id=u.id AND r.role='manager')
            AND EXISTS (SELECT 1 FROM identity.manager_departments m
                        WHERE m.user_id=u.id AND m.dept_code=s->>'manager_dept'))
    )
    WHERE i.id=p_intent AND (i.actor_id IS NULL OR u.id<>i.actor_id)
    ON CONFLICT DO NOTHING;
    UPDATE notify.notification_intents SET expanded_at=clock_timestamp() WHERE id=p_intent;
END; $$;

CREATE OR REPLACE FUNCTION notify.expand_new_notification()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM notify.expand_notification_intent(NEW.id);
    RETURN NEW;
END; $$;

-- Trigger execution uses the owner's fixed function, not a runtime EXECUTE grant.
REVOKE ALL ON FUNCTION notify.expand_notification_intent(uuid), notify.expand_new_notification()
    FROM PUBLIC, cs_app, cs_worker, tft_app, tft_worker, platform_app;
DROP TRIGGER IF EXISTS notification_expand_on_insert ON notify.notification_intents;
CREATE TRIGGER notification_expand_on_insert AFTER INSERT ON notify.notification_intents
    FOR EACH ROW EXECUTE FUNCTION notify.expand_new_notification();

-- Preserve pre-upgrade pending intents too, even when Web Push is disabled.
DO $$
DECLARE pending record;
BEGIN
    FOR pending IN SELECT id FROM notify.notification_intents WHERE expanded_at IS NULL
        ORDER BY created_at,id FOR UPDATE
    LOOP
        PERFORM notify.expand_notification_intent(pending.id);
    END LOOP;
END; $$;
COMMIT;
