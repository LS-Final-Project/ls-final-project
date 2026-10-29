-- KAN-110: customer mail on a ticket — approved sends handed to the platform (KAN-74), the
-- mail that opened the ticket, and customer replies the platform linked (KAN-75).
-- Apply after 20261027_KAN-108_ticket_workflow.sql. Local bootstrap applies this as postgres.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

-- One row per approved customer message. The platform owns delivery; this row records the
-- approval, the platform's intent ID and the last status the C/S service read back.
CREATE TABLE IF NOT EXISTS cs.ticket_customer_sends (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    kind text NOT NULL CHECK (kind IN ('ANSWER', 'INFO_REQUEST')),
    -- The workflow version the agent approved. One approved send per version.
    workflow_version integer NOT NULL CHECK (workflow_version > 0),
    mailbox_id uuid NOT NULL,
    subject text NOT NULL,
    body_type text NOT NULL CHECK (body_type IN ('text', 'html')),
    body text NOT NULL,
    to_addresses jsonb NOT NULL CHECK (jsonb_typeof(to_addresses) = 'array'),
    cc_addresses jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(cc_addresses) = 'array'),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    approved_by uuid NOT NULL,
    approved_at timestamptz NOT NULL,
    -- PENDING: platform not called yet (stays only if the process died mid-request).
    -- REGISTERED: the platform holds an intent. REJECTED: the platform refused (no intent).
    -- REQUEST_FAILED: the platform was never reached. REQUEST_UNKNOWN: no usable answer.
    request_status text NOT NULL DEFAULT 'PENDING' CHECK (
        request_status IN ('PENDING', 'REGISTERED', 'REJECTED', 'REQUEST_FAILED', 'REQUEST_UNKNOWN')
    ),
    request_error text,
    intent_id uuid UNIQUE,
    -- Last platform status read back (QUEUED, SUBMITTING, ACCEPTED, UNKNOWN, FAILED).
    send_status text,
    send_error_code text,
    attempts integer NOT NULL DEFAULT 0,
    accepted_at timestamptz,
    sent_copy_confirmed_at timestamptz,
    status_checked_at timestamptz,
    -- When ACCEPTED was applied to the workflow (record_customer_send).
    recorded_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((request_status = 'REGISTERED') = (intent_id IS NOT NULL)),
    UNIQUE (ticket_id, workflow_version)
);
CREATE INDEX IF NOT EXISTS ticket_customer_sends_ticket
    ON cs.ticket_customer_sends (ticket_id, created_at);

-- The received mail a ticket was registered from. The platform keeps the mail; this row keeps
-- the reference and the sender so an answer goes back to the same address.
CREATE TABLE IF NOT EXISTS cs.ticket_mail_origins (
    ticket_id uuid PRIMARY KEY REFERENCES cs.tickets(id),
    message_id uuid NOT NULL UNIQUE,
    mailbox_id uuid NOT NULL,
    sender_address text NOT NULL,
    received_at timestamptz NOT NULL,
    registered_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Customer replies the platform linked to the ticket (KAN-75). One row per mail; recording
-- it again changes nothing.
CREATE TABLE IF NOT EXISTS cs.ticket_customer_replies (
    message_id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    method text NOT NULL CHECK (method IN ('HEADER', 'THREAD', 'HUMAN')),
    received_at timestamptz NOT NULL,
    -- Whether this reply moved the ticket back to IN_REVIEW.
    reopened boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ticket_customer_replies_ticket
    ON cs.ticket_customer_replies (ticket_id, received_at);

-- Mail history is kept: the service never removes these rows.
REVOKE DELETE, TRUNCATE ON cs.ticket_customer_sends, cs.ticket_mail_origins,
    cs.ticket_customer_replies FROM cs_app;

COMMIT;
