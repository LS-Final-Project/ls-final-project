-- KAN-108: ticket workflow state, department transfers and department results
-- (contracts/platform/permissions.md 2.1). Provided `status` stays in ticket_source_records.
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

-- One row per ticket once its workflow starts. A ticket without a row is RECEIVED.
CREATE TABLE IF NOT EXISTS cs.ticket_workflows (
    ticket_id uuid PRIMARY KEY REFERENCES cs.tickets(id),
    state text NOT NULL CHECK (
        state IN ('RECEIVED', 'IN_REVIEW', 'WAITING_CUSTOMER', 'ANSWERED', 'TRANSFERRED', 'CLOSED')
    ),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    -- The C/S agent responsible for the ticket. Reassigned only by a C/S manager.
    owner_user_id uuid,
    -- Set by the mail sending path (KAN-74) once a customer message was accepted.
    customer_sent_at timestamptz,
    closed_at timestamptz,
    closed_by uuid,
    CHECK ((state = 'CLOSED') = (closed_at IS NOT NULL AND closed_by IS NOT NULL)),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ticket_workflows_state ON cs.ticket_workflows (state);

-- Each transfer to a department. Kept after it ends: the department keeps read access.
CREATE TABLE IF NOT EXISTS cs.ticket_transfers (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    round integer NOT NULL CHECK (round > 0),
    dept_code text NOT NULL CHECK (dept_code ~ '^[A-Z][A-Z0-9_]*$' AND dept_code <> 'GCS_L1'),
    status text NOT NULL
        CHECK (status IN ('OPEN', 'RESULT_SUBMITTED', 'RETURNED_BY_DEPT', 'RECALLED')),
    reason text NOT NULL,
    transferred_by uuid NOT NULL,
    transferred_at timestamptz NOT NULL DEFAULT now(),
    ended_by uuid,
    ended_at timestamptz,
    end_reason text,
    CHECK ((status = 'OPEN') = (ended_by IS NULL AND ended_at IS NULL)),
    CHECK (status NOT IN ('RETURNED_BY_DEPT', 'RECALLED') OR end_reason IS NOT NULL),
    UNIQUE (ticket_id, round)
);
CREATE UNIQUE INDEX IF NOT EXISTS ticket_transfers_one_open
    ON cs.ticket_transfers (ticket_id) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS ticket_transfers_dept ON cs.ticket_transfers (dept_code, ticket_id);

-- What a department reports for a transfer. Submitted is not reviewed, and reviewed is not
-- closed: only an accepted result counts toward closing the ticket.
CREATE TABLE IF NOT EXISTS cs.dept_results (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    transfer_id uuid NOT NULL UNIQUE REFERENCES cs.ticket_transfers(id),
    dept_code text NOT NULL,
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    result text NOT NULL,
    -- Content the C/S agent may pass on to the customer after review.
    reply_content text,
    submitted_by uuid NOT NULL,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    review_status text NOT NULL DEFAULT 'SUBMITTED'
        CHECK (review_status IN ('SUBMITTED', 'ACCEPTED', 'REWORK_REQUESTED')),
    reviewed_by uuid,
    reviewed_at timestamptz,
    review_note text,
    CHECK ((review_status = 'SUBMITTED') = (reviewed_by IS NULL AND reviewed_at IS NULL)),
    CHECK (review_status <> 'REWORK_REQUESTED' OR review_note IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS dept_results_ticket ON cs.dept_results (ticket_id, submitted_at);

-- Widen the audit lists only while they lack the new values, so a re-run never narrows them.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'audit_events_entity_type_check'
          AND conrelid = 'cs.audit_events'::regclass
          AND pg_get_constraintdef(oid) LIKE '%dept_result%'
    ) THEN
        ALTER TABLE cs.audit_events DROP CONSTRAINT IF EXISTS audit_events_entity_type_check;
        ALTER TABLE cs.audit_events ADD CONSTRAINT audit_events_entity_type_check CHECK (
            entity_type IN ('ticket', 'ticket_unit_link', 'ticket_workflow', 'dept_result')
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'audit_events_action_check'
          AND conrelid = 'cs.audit_events'::regclass
          AND pg_get_constraintdef(oid) LIKE '%transition%'
    ) THEN
        ALTER TABLE cs.audit_events DROP CONSTRAINT IF EXISTS audit_events_action_check;
        ALTER TABLE cs.audit_events ADD CONSTRAINT audit_events_action_check
            CHECK (action IN ('create', 'update', 'transition', 'review'));
    END IF;
END
$$;

-- Workflow history is kept: the service never removes these rows.
REVOKE DELETE, TRUNCATE ON cs.ticket_workflows, cs.ticket_transfers, cs.dept_results FROM cs_app;

COMMIT;
