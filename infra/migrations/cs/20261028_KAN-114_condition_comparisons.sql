-- KAN-114: condition comparison results (contracts/cs/condition-comparison.md).
-- One comparison per (product_code, fault_id) group. Every judgement change writes a whole new
-- version (items and evidence included); earlier versions are never changed or removed.
-- Applied after 20261028_KAN-112_improvement_requests.sql, whose table a version points to.
-- Local bootstrap and the test DB apply this as postgres. It is not applied to the shared DB
-- by this change; the operator applies it separately. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.condition_comparisons (
    id uuid PRIMARY KEY,
    product_code text NOT NULL,
    fault_id text NOT NULL CHECK (fault_id ~ '^FLT-[0-9]{3}$'),
    latest_version integer NOT NULL CHECK (latest_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (product_code, fault_id)
);

CREATE TABLE IF NOT EXISTS cs.condition_comparison_versions (
    comparison_id uuid NOT NULL REFERENCES cs.condition_comparisons(id),
    version integer NOT NULL CHECK (version > 0),
    -- The request and request version the judgements were made against. A group without a
    -- request has neither.
    improvement_request_id uuid REFERENCES cs.improvement_requests(id),
    request_version integer CHECK (request_version > 0),
    CHECK ((improvement_request_id IS NULL) = (request_version IS NULL)),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    -- A comparison is always derived from its evidence, even when a person confirmed it.
    derivation_kind text NOT NULL DEFAULT 'DERIVED' CHECK (derivation_kind = 'DERIVED'),
    known_at timestamptz NOT NULL,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (comparison_id, version)
);

-- Evidence references only: no ticket, case or document text.
CREATE TABLE IF NOT EXISTS cs.condition_comparison_evidence (
    comparison_id uuid NOT NULL,
    version integer NOT NULL,
    evidence_id text NOT NULL CHECK (length(evidence_id) BETWEEN 1 AND 64),
    case_id uuid REFERENCES cs.resolution_cases(id),
    ticket_id uuid REFERENCES cs.tickets(id),
    evidence_location jsonb NOT NULL CHECK (jsonb_typeof(evidence_location) = 'object'),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    PRIMARY KEY (comparison_id, version, evidence_id),
    FOREIGN KEY (comparison_id, version)
        REFERENCES cs.condition_comparison_versions(comparison_id, version)
);

-- Condition items, causes and document signals of one version. The code lists match
-- backend/api/cs/comparisons/schemas.py; conditions reuse info_field_codebook codes.
CREATE TABLE IF NOT EXISTS cs.condition_comparison_items (
    comparison_id uuid NOT NULL,
    version integer NOT NULL,
    kind text NOT NULL CHECK (kind IN ('CONDITION', 'CAUSE', 'DOCUMENT_SIGNAL')),
    code text NOT NULL,
    CHECK (kind <> 'CONDITION' OR code IN (
        'application', 'site_condition', 'operating_condition', 'load_profile', 'current_fw',
        'install_date', 'string_config', 'battery_age'
    )),
    CHECK (kind <> 'CAUSE' OR code IN (
        'SITE_CONDITION', 'HARDWARE', 'SETTING', 'WEAR_PART', 'FIRMWARE'
    )),
    CHECK (kind <> 'DOCUMENT_SIGNAL' OR code IN (
        'NOT_FOUND', 'VALUE_CONFLICT', 'BROKEN_LINK', 'REPEATED_SPEC'
    )),
    judgement text CHECK (judgement IN ('CONFIRMED', 'SUSPECTED', 'UNCONFIRMED')),
    finding text CHECK (finding IN ('DIFFERS', 'SAME')),
    unconfirmed_reason text CHECK (unconfirmed_reason IN (
        'NO_EVIDENCE', 'EVIDENCE_REMOVED', 'NOT_YET_KNOWN', 'VALUE_NOT_PROVIDED'
    )),
    baseline_value text,
    baseline_missing_reason text
        CHECK (baseline_missing_reason IN ('NOT_PROVIDED', 'HIDDEN_BY_SCOPE', 'MASKING_FAILED')),
    baseline_evidence_ids text[] NOT NULL DEFAULT '{}',
    target_value text,
    target_missing_reason text
        CHECK (target_missing_reason IN ('NOT_PROVIDED', 'HIDDEN_BY_SCOPE', 'MASKING_FAILED')),
    target_evidence_ids text[] NOT NULL DEFAULT '{}',
    masked_summary text,
    summary_missing_reason text
        CHECK (summary_missing_reason IN ('NOT_PROVIDED', 'HIDDEN_BY_SCOPE', 'MASKING_FAILED')),
    evidence_ids text[] NOT NULL DEFAULT '{}',
    provenance_kind text NOT NULL
        CHECK (provenance_kind IN ('ai_draft', 'human_input', 'system_record')),
    decided_by uuid,
    decided_by_name text CHECK (decided_by_name IS NULL OR provenance_kind = 'human_input'),
    decided_at timestamptz,
    -- Document signals carry no judgement; conditions and causes always do.
    CHECK ((kind = 'DOCUMENT_SIGNAL') = (judgement IS NULL)),
    CHECK (kind = 'DOCUMENT_SIGNAL' OR decided_at IS NOT NULL),
    CHECK ((unconfirmed_reason IS NOT NULL) = (judgement IS NOT DISTINCT FROM 'UNCONFIRMED')),
    -- finding exists only on conditions and is null only when unconfirmed.
    CHECK (CASE WHEN kind = 'CONDITION' THEN (finding IS NULL) = (judgement = 'UNCONFIRMED')
                ELSE finding IS NULL END),
    -- Baseline and target exist only on conditions; a value is null only with a reason.
    CHECK (CASE WHEN kind = 'CONDITION'
                THEN (baseline_value IS NULL) = (baseline_missing_reason IS NOT NULL)
                     AND (target_value IS NULL) = (target_missing_reason IS NOT NULL)
                ELSE baseline_value IS NULL AND baseline_missing_reason IS NULL
                     AND target_value IS NULL AND target_missing_reason IS NULL
                     AND baseline_evidence_ids = '{}' AND target_evidence_ids = '{}' END),
    -- An empty value is never judged the same or different.
    CHECK (judgement = 'UNCONFIRMED' OR 'NOT_PROVIDED' NOT IN (
        coalesce(baseline_missing_reason, ''), coalesce(target_missing_reason, '')
    )),
    CHECK (CASE WHEN kind = 'DOCUMENT_SIGNAL'
                THEN masked_summary IS NULL AND summary_missing_reason IS NULL
                ELSE (masked_summary IS NULL) = (summary_missing_reason IS NOT NULL) END),
    -- Confirmed and suspected judgements need evidence.
    CHECK (judgement IS NULL OR judgement = 'UNCONFIRMED' OR cardinality(evidence_ids) > 0),
    CHECK (baseline_evidence_ids <@ evidence_ids AND target_evidence_ids <@ evidence_ids),
    PRIMARY KEY (comparison_id, version, kind, code),
    FOREIGN KEY (comparison_id, version)
        REFERENCES cs.condition_comparison_versions(comparison_id, version)
);

-- Versions are written once. Only the pointer to the latest version moves.
REVOKE UPDATE, DELETE, TRUNCATE ON
    cs.condition_comparisons, cs.condition_comparison_versions,
    cs.condition_comparison_evidence, cs.condition_comparison_items
FROM cs_app;
GRANT UPDATE (latest_version) ON cs.condition_comparisons TO cs_app;

COMMIT;
