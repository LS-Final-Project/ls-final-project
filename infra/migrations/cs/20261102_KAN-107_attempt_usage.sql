-- KAN-107: per-attempt model usage on C/S analysis runs and drafts, with the same column names
-- and checks as evaluation.case_attempts (20261023_KAN-85_evaluation_storage.sql).
-- Needs 20261030_KAN-107_ticket_analysis.sql and 20261101_KAN-107_worker_source.sql.
-- Apply before deploying the worker code that writes these columns. Existing rows keep nulls
-- (null means the provider did not report the value or no attempt was recorded with usage).
-- cs_worker already has table-level INSERT on both tables (C18), so no new grant is needed.
-- Apply with cs_migrator. Re-runnable.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['ticket_analyses', 'ticket_drafts'] LOOP
        EXECUTE format(
            'ALTER TABLE cs.%I '
            'ADD COLUMN IF NOT EXISTS latency_ms integer CHECK (latency_ms IS NULL OR latency_ms >= 0), '
            'ADD COLUMN IF NOT EXISTS input_tokens integer CHECK (input_tokens IS NULL OR input_tokens >= 0), '
            'ADD COLUMN IF NOT EXISTS output_tokens integer CHECK (output_tokens IS NULL OR output_tokens >= 0), '
            'ADD COLUMN IF NOT EXISTS thought_tokens integer CHECK (thought_tokens IS NULL OR thought_tokens >= 0), '
            'ADD COLUMN IF NOT EXISTS cached_input_tokens integer '
            '    CHECK (cached_input_tokens IS NULL OR cached_input_tokens >= 0), '
            'ADD COLUMN IF NOT EXISTS total_tokens integer CHECK (total_tokens IS NULL OR total_tokens >= 0), '
            -- Null when the price table has no USD price for the model or a token count is missing.
            'ADD COLUMN IF NOT EXISTS cost_usd numeric(14, 6) CHECK (cost_usd IS NULL OR cost_usd >= 0), '
            'ADD COLUMN IF NOT EXISTS pricing_version text',
            target
        );
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = format('cs.%I', target)::regclass
              AND conname = target || '_cost_needs_pricing'
        ) THEN
            EXECUTE format(
                'ALTER TABLE cs.%I ADD CONSTRAINT %I '
                'CHECK (cost_usd IS NULL OR pricing_version IS NOT NULL)',
                target,
                target || '_cost_needs_pricing'
            );
        END IF;
    END LOOP;
END $$;

COMMIT;
