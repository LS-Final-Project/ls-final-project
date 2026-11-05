-- KAN-114: judgement writes on condition comparisons (contracts/cs/condition-comparison.md).
-- Applied after 20261028_KAN-114_condition_comparisons.sql. It is not applied to the shared DB
-- by this change; the operator applies it separately. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

-- Why a person confirmed or reviewed an item. Kept with the version that holds the judgement.
ALTER TABLE cs.condition_comparison_items ADD COLUMN IF NOT EXISTS reason text;

DO $$
BEGIN
    -- A side that shows a value names the evidence it came from, so scope checks can hide it.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'condition_comparison_items_side_evidence'
          AND conrelid = 'cs.condition_comparison_items'::regclass
    ) THEN
        ALTER TABLE cs.condition_comparison_items
            ADD CONSTRAINT condition_comparison_items_side_evidence CHECK (
                (baseline_value IS NULL OR cardinality(baseline_evidence_ids) > 0)
                AND (target_value IS NULL OR cardinality(target_evidence_ids) > 0)
            );
    END IF;
    -- Widen the audit list only while it lacks the new value, so a re-run never narrows it.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'audit_events_entity_type_check'
          AND conrelid = 'cs.audit_events'::regclass
          AND pg_get_constraintdef(oid) LIKE '%condition_comparison%'
    ) THEN
        ALTER TABLE cs.audit_events DROP CONSTRAINT IF EXISTS audit_events_entity_type_check;
        ALTER TABLE cs.audit_events ADD CONSTRAINT audit_events_entity_type_check CHECK (
            entity_type IN (
                'ticket', 'ticket_unit_link', 'ticket_workflow', 'dept_result',
                'resolution_case', 'improvement_request', 'condition_comparison'
            )
        );
    END IF;
END
$$;

COMMIT;
