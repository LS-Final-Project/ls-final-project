-- KAN-102: C/S intake records and resolution cases (C/S proposal 6.1, PROVIDED tables).
-- Classification, rule results and workflow status are stored by their own tickets.
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.tickets (
    id uuid PRIMARY KEY,
    source text NOT NULL,
    source_instance text NOT NULL,
    external_id text,
    schema_version text NOT NULL DEFAULT '1.0',
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    received_at timestamptz NOT NULL,
    known_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    channel text NOT NULL
        CHECK (channel IN ('EMAIL', 'PARTNER_PORTAL', 'WEB_FORM', 'PHONE', 'CHAT')),
    body_language text NOT NULL,
    customer_external_id text NOT NULL,
    product_code text NOT NULL,
    subject text,
    body text NOT NULL,
    fw_version_reported text,
    error_code_reported text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- External IDs are unique per source instance. Manual input may have no external ID.
CREATE UNIQUE INDEX IF NOT EXISTS tickets_source_external_id
    ON cs.tickets (source, source_instance, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS tickets_received_at_id ON cs.tickets (received_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS tickets_customer_product
    ON cs.tickets (customer_external_id, product_code);

CREATE TABLE IF NOT EXISTS cs.resolution_cases (
    id uuid PRIMARY KEY,
    -- One provided ticket has at most one resolution case.
    ticket_id uuid NOT NULL UNIQUE REFERENCES cs.tickets(id),
    source text NOT NULL,
    source_instance text NOT NULL,
    external_id text,
    schema_version text NOT NULL DEFAULT '1.0',
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    -- A resolution may only be used after it was known (no future information).
    resolved_at timestamptz,
    known_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    product_code text NOT NULL,
    fault_id text NOT NULL,
    fw_version_at_issue text,
    symptom text NOT NULL,
    root_cause text NOT NULL,
    root_cause_type text NOT NULL,
    action_taken text NOT NULL,
    onsite_visit boolean NOT NULL,
    rma_no text,
    fw_fixed_version text,
    resolved_by_dept text NOT NULL,
    recurrence_prevention text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS resolution_cases_source_external_id
    ON cs.resolution_cases (source, source_instance, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS resolution_cases_product_fault
    ON cs.resolution_cases (product_code, fault_id, known_at);

-- What the provided source recorded about a ticket: labels, rule results and handling.
-- These are evaluation answers and after-the-fact history. They are never classifier input,
-- and they are kept apart from AI results, rule results and human edits of the live workflow.
CREATE TABLE IF NOT EXISTS cs.ticket_source_records (
    ticket_id uuid PRIMARY KEY REFERENCES cs.tickets(id),
    schema_version text NOT NULL DEFAULT '1.0',
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL DEFAULT 'PROVIDED'
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    ingested_at timestamptz NOT NULL DEFAULT now(),
    data_split text CHECK (data_split IN ('train', 'valid', 'test')),
    -- Customer context at the time of the inquiry
    language text,
    locale text,
    country text,
    customer_tier text,
    customer_segment text,
    product_family text,
    -- Recorded classification
    category_code text,
    secondary_category text,
    urgency_flag text,
    required_info text[] NOT NULL DEFAULT '{}',
    provided_info text[] NOT NULL DEFAULT '{}',
    missing_info text[] NOT NULL DEFAULT '{}',
    missing_blocking_info text[] NOT NULL DEFAULT '{}',
    -- Recorded rule results
    priority text,
    priority_rule_id text,
    priority_adjusted_by_agent boolean,
    routing_rule_id text,
    assigned_dept text,
    -- Recorded handling
    assignee_masked text,
    escalated boolean,
    escalated_to text,
    escalation_reason text,
    info_request_sent boolean,
    info_request_message text,
    status text,
    first_response_at timestamptz,
    resolved_at timestamptz,
    first_response_hours numeric,
    customer_wait_hours numeric,
    customer_followups integer,
    sla_first_response_target_h integer,
    sla_resolution_target_h integer,
    sla_elapsed_hours numeric,
    sla_first_response_status text,
    sla_resolution_status text,
    expected_action text,
    final_action text,
    final_reply text,
    rma_no text,
    reference_doc_id text,
    reference_section text
);
CREATE INDEX IF NOT EXISTS ticket_source_records_split ON cs.ticket_source_records (data_split);

COMMIT;
