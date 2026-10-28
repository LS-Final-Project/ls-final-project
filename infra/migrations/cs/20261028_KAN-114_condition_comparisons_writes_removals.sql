-- KAN-114: evidence removal on condition comparisons (contracts/cs/condition-comparison.md).
-- Applied after 20261028_KAN-114_condition_comparisons_writes.sql. It is not applied to the
-- shared DB by this change; the operator applies it separately. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

-- One row per removed evidence ID: the version the removal made, the items that cited it, and
-- why. A removed ID is never used again, and EVIDENCE_REMOVED is allowed only for those items.
CREATE TABLE IF NOT EXISTS cs.condition_comparison_removals (
    comparison_id uuid NOT NULL REFERENCES cs.condition_comparisons(id),
    evidence_id text NOT NULL,
    version integer NOT NULL,
    item_keys text[] NOT NULL DEFAULT '{}',
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
    removed_by uuid NOT NULL,
    removed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (comparison_id, evidence_id),
    FOREIGN KEY (comparison_id, version)
        REFERENCES cs.condition_comparison_versions(comparison_id, version)
);

-- Removals are history: the service reads and adds them only.
REVOKE UPDATE, DELETE, TRUNCATE ON cs.condition_comparison_removals FROM cs_app;

COMMIT;
