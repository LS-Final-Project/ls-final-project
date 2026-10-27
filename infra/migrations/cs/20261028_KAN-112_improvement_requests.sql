-- KAN-112: improvement requests, evidence, department states, replies and actions
-- (C/S proposal 4.4, 6.1; contracts/platform/permissions.md 2.2, 2.2.1).
-- A request is separate from its tickets: closing a ticket never completes a request, and a
-- request's progress never changes a ticket. State changes are KAN-116.
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.improvement_requests (
    id uuid PRIMARY KEY,
    -- FAULT: the same fault repeating across customers. DOCUMENT: a document problem.
    request_type text NOT NULL CHECK (request_type IN ('FAULT', 'DOCUMENT')),
    origin text NOT NULL CHECK (origin IN ('SYSTEM_CANDIDATE', 'MANUAL')),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL DEFAULT 'SYSTEM'
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    -- The group the request is about.
    product_code text NOT NULL,
    fault_id text CHECK (fault_id ~ '^FLT-[0-9]{3}$'),
    doc_signal text
        CHECK (doc_signal IN ('NOT_FOUND', 'VALUE_CONFLICT', 'BROKEN_LINK', 'REPEATED_SPEC')),
    doc_id text,
    CHECK ((request_type = 'FAULT') = (fault_id IS NOT NULL AND doc_signal IS NULL)),
    CHECK ((request_type = 'DOCUMENT') = (doc_signal IS NOT NULL AND fault_id IS NULL)),
    -- Usage conditions split a group. Unknown conditions keep the grouping on hold.
    condition_status text NOT NULL DEFAULT 'UNKNOWN'
        CHECK (condition_status IN ('CONFIRMED', 'UNKNOWN')),
    condition_summary text,
    fw_fixed_version text,
    fw_released_on date,
    -- Request state (permissions.md 2.2). Set by KAN-116 transitions after creation.
    status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN (
        'DRAFT', 'REVIEWED', 'SENT', 'ACKNOWLEDGED', 'ADOPTED', 'INFO_REQUESTED', 'REJECTED',
        'ACTION_TAKEN', 'FOLLOW_UP', 'COMPLETED'
    )),
    -- Content version. Every content or evidence change adds a row to the versions table.
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    current_round integer NOT NULL DEFAULT 0 CHECK (current_round >= 0),
    -- The C/S agent who owns the request. Reassigned only by a C/S manager.
    cs_owner uuid,
    problem_statement text NOT NULL,
    confirmed_facts text,
    inconsistencies text,
    missing_info text,
    not_claimed text,
    review_task text,
    -- Department codes proposed by rules and edited by the agent. A document request may
    -- have none yet: its receiving department needs confirmation with the business.
    proposed_depts text[] NOT NULL DEFAULT '{}',
    reviewed_by uuid,
    reviewed_at timestamptz,
    sent_at timestamptz,
    followup_plan text,
    followup_result text,
    completed_by uuid,
    completed_at timestamptz,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS improvement_requests_group
    ON cs.improvement_requests (request_type, product_code, fault_id, doc_signal);
CREATE INDEX IF NOT EXISTS improvement_requests_status ON cs.improvement_requests (status);

-- The full content of every version, so a department reply can name what it answered.
CREATE TABLE IF NOT EXISTS cs.improvement_request_versions (
    request_id uuid NOT NULL REFERENCES cs.improvement_requests(id),
    version integer NOT NULL CHECK (version > 0),
    content jsonb NOT NULL,
    changed_by uuid,
    changed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, version)
);

-- Tickets (and, for faults, their resolution cases) behind a request. A removed link is kept
-- with who removed it and why.
CREATE TABLE IF NOT EXISTS cs.request_evidence (
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES cs.improvement_requests(id),
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    case_id uuid REFERENCES cs.resolution_cases(id),
    added_by uuid,
    added_at timestamptz NOT NULL DEFAULT now(),
    removed_by uuid,
    removed_at timestamptz,
    removal_reason text,
    CHECK ((removed_at IS NULL) = (removed_by IS NULL AND removal_reason IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS request_evidence_active
    ON cs.request_evidence (request_id, ticket_id) WHERE removed_at IS NULL;

-- Each receiving department in each delivery round (permissions.md 2.2.1).
CREATE TABLE IF NOT EXISTS cs.request_departments (
    request_id uuid NOT NULL REFERENCES cs.improvement_requests(id),
    round integer NOT NULL CHECK (round > 0),
    dept_code text NOT NULL CHECK (dept_code ~ '^[A-Z][A-Z0-9_]*$' AND dept_code <> 'GCS_L1'),
    status text NOT NULL CHECK (status IN (
        'SENT', 'ACKNOWLEDGED', 'ADOPTED', 'INFO_REQUESTED', 'REJECTED', 'ACTION_TAKEN'
    )),
    -- True when the department was not called again and kept its earlier state.
    carried_over boolean NOT NULL DEFAULT false,
    sent_version integer,
    reply_due_at timestamptz,
    rejection_reviewed_by uuid,
    rejection_reviewed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, round, dept_code)
);

-- What a department answered, and which request version it answered.
CREATE TABLE IF NOT EXISTS cs.dept_replies (
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES cs.improvement_requests(id),
    round integer NOT NULL CHECK (round > 0),
    dept_code text NOT NULL,
    request_version integer NOT NULL,
    reply_type text NOT NULL
        CHECK (reply_type IN ('ACKNOWLEDGED', 'ADOPTED', 'INFO_REQUESTED', 'REJECTED')),
    reason text,
    CHECK (reply_type <> 'REJECTED' OR reason IS NOT NULL),
    replied_by uuid NOT NULL,
    replied_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dept_replies_request ON cs.dept_replies (request_id, round, replied_at);

-- A department's improvement action, kept apart from any single customer's repair.
CREATE TABLE IF NOT EXISTS cs.request_actions (
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES cs.improvement_requests(id),
    round integer NOT NULL CHECK (round > 0),
    dept_code text NOT NULL,
    action text NOT NULL,
    action_scope text NOT NULL,
    action_date date NOT NULL,
    action_evidence text NOT NULL,
    recorded_by uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

-- Widen the audit lists only while they lack the new values, so a re-run never narrows them.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'audit_events_entity_type_check'
          AND conrelid = 'cs.audit_events'::regclass
          AND pg_get_constraintdef(oid) LIKE '%improvement_request%'
    ) THEN
        ALTER TABLE cs.audit_events DROP CONSTRAINT IF EXISTS audit_events_entity_type_check;
        ALTER TABLE cs.audit_events ADD CONSTRAINT audit_events_entity_type_check CHECK (
            entity_type IN (
                'ticket', 'ticket_unit_link', 'ticket_workflow', 'dept_result',
                'resolution_case', 'improvement_request'
            )
        );
    END IF;
END
$$;

-- Request history is kept: the service never removes these rows.
REVOKE DELETE, TRUNCATE ON
    cs.improvement_requests, cs.improvement_request_versions, cs.request_evidence,
    cs.request_departments, cs.dept_replies, cs.request_actions
FROM cs_app;
-- Versions, replies and actions are written once.
REVOKE UPDATE ON cs.improvement_request_versions, cs.dept_replies, cs.request_actions FROM cs_app;

COMMIT;
