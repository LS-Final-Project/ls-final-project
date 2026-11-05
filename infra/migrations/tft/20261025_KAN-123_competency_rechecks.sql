-- KAN-123: competency verification is append-only evidence, like experience/availability.
-- Preserve every old record; replay uniqueness remains in recommendation_commands.
BEGIN;
ALTER TABLE tft.competency_checks
    DROP CONSTRAINT IF EXISTS competency_checks_recommendation_id_competency_id_key;
CREATE INDEX IF NOT EXISTS competency_checks_recommendation_competency_time
    ON tft.competency_checks (recommendation_id, competency_id, checked_at DESC, id);
COMMIT;
