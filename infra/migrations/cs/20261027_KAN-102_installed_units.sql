-- KAN-102: installed units and ticket-to-unit links (C/S proposal 4.3, 6.1 ASSUMED tables).
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
-- Units are assumed CRM install-base data. They are loaded by an adapter, never by the API.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

CREATE TABLE IF NOT EXISTS cs.installed_units (
    id uuid PRIMARY KEY,
    source text NOT NULL,
    source_instance text NOT NULL,
    external_id text,
    schema_version text NOT NULL DEFAULT '1.0',
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL DEFAULT 'ASSUMED'
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    -- Provided serials are masked, so demo units use virtual serials (VSN-<product>-<no>).
    serial_no text NOT NULL,
    customer_external_id text NOT NULL,
    product_code text NOT NULL,
    -- A site code instead of the street address.
    site_code text,
    commissioning_date date,
    commissioning_status text NOT NULL DEFAULT 'UNKNOWN'
        CHECK (commissioning_status IN ('CONFIRMED', 'PLANNED', 'UNKNOWN')),
    ship_date date,
    lot_no text,
    fw_version_last text,
    unit_status text NOT NULL DEFAULT 'ACTIVE'
        CHECK (unit_status IN ('ACTIVE', 'REPLACED', 'RETIRED')),
    -- The unit sent in its place (RMA or warranty replacement).
    replaced_by_unit_id uuid REFERENCES cs.installed_units(id) CHECK (replaced_by_unit_id <> id),
    confirmed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (product_code, serial_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS installed_units_source_external_id
    ON cs.installed_units (source, source_instance, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS installed_units_customer_product
    ON cs.installed_units (customer_external_id, product_code);
CREATE INDEX IF NOT EXISTS installed_units_replaced_by
    ON cs.installed_units (replaced_by_unit_id) WHERE replaced_by_unit_id IS NOT NULL;

-- Which unit a ticket is about. A ticket may have several candidates but one confirmed unit.
-- Only confirmed links carry history between tickets.
CREATE TABLE IF NOT EXISTS cs.ticket_unit_links (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    unit_id uuid NOT NULL REFERENCES cs.installed_units(id),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL DEFAULT 'SYSTEM'
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    link_status text NOT NULL CHECK (link_status IN ('CANDIDATE', 'CONFIRMED', 'REJECTED')),
    link_basis text NOT NULL
        CHECK (link_basis IN ('SERIAL_PROVIDED', 'CUSTOMER_CONFIRMED', 'AGENT_REVIEW')),
    repeat_type text CHECK (
        repeat_type IN ('SAME_UNIT', 'REPLACEMENT', 'MULTIPLE_UNITS', 'CONFLICT', 'DIFFERENT_UNIT')
    ),
    -- The person who confirmed or rejected the link. Candidates have no decision yet.
    decided_by uuid,
    decided_at timestamptz,
    CHECK ((link_status = 'CANDIDATE') = (decided_by IS NULL AND decided_at IS NULL)),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticket_id, unit_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ticket_unit_links_one_confirmed
    ON cs.ticket_unit_links (ticket_id) WHERE link_status = 'CONFIRMED';
CREATE INDEX IF NOT EXISTS ticket_unit_links_confirmed_unit
    ON cs.ticket_unit_links (unit_id) WHERE link_status = 'CONFIRMED';

-- Link decisions are audited next to ticket changes.
ALTER TABLE cs.audit_events DROP CONSTRAINT IF EXISTS audit_events_entity_type_check;
ALTER TABLE cs.audit_events ADD CONSTRAINT audit_events_entity_type_check
    CHECK (entity_type IN ('ticket', 'ticket_unit_link'));

-- Units come from the install-base adapter; the service only reads them.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON cs.installed_units FROM cs_app;
REVOKE DELETE, TRUNCATE ON cs.ticket_unit_links FROM cs_app;

COMMIT;
