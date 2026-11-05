-- KAN-214: C/S agent skills (proposal p.23 employee table, assumed data) and the rule-based
-- assignment decisions of tickets and improvement requests (contracts/cs/assignment.md). Apply
-- after C1 (tickets), C4 (ticket_workflows) and C6 (improvement_requests), before deploying the
-- C/S service with automatic assignment.
-- Re-runnable; only the owning migrator creates the tables. Local bootstrap applies this as postgres.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;

-- Which languages a C/S agent reads and which product families (product_master.family) the agent
-- handles. Written only by the local seed (infra/local/seed_demo.sql) or by an operator with
-- infra/sql/register_agent_skills.sql. The service reads it and never writes it.
CREATE TABLE IF NOT EXISTS cs.agent_skills (
    -- identity.users.id of an active cs_agent. The C/S service cannot read identity.
    user_id uuid PRIMARY KEY,
    languages text[] NOT NULL CHECK (
        cardinality(languages) > 0 AND array_position(languages, NULL) IS NULL
        AND array_to_string(languages, ',') ~ '^[a-z]{2,3}(,[a-z]{2,3})*$'
    ),
    families text[] NOT NULL CHECK (
        cardinality(families) > 0 AND array_position(families, NULL) IS NULL
        AND array_to_string(families, ',') ~ '^[A-Z][A-Z0-9_]*(,[A-Z][A-Z0-9_]*)*$'
    ),
    active boolean NOT NULL DEFAULT true,
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL DEFAULT 'ASSUMED'
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- One row per rule assignment that set an owner, append-only. The owner itself stays in
-- cs.ticket_workflows.owner_user_id (changed with an audit event); this keeps why. A ticket the
-- rules could not assign (NO_MATCH) gets no row: it stays in the manager preview with its reason.
CREATE TABLE IF NOT EXISTS cs.ticket_assignments (
    id uuid PRIMARY KEY,
    ticket_id uuid NOT NULL REFERENCES cs.tickets(id),
    user_id uuid NOT NULL,
    rule_version text NOT NULL,
    reason_code text NOT NULL CHECK (
        reason_code IN ('LANGUAGE_AND_FAMILY', 'LANGUAGE_ONLY', 'FAMILY_ENGLISH_FALLBACK')
    ),
    matched_language text,
    matched_family text,
    candidate_count integer NOT NULL CHECK (candidate_count >= 0),
    chosen_open_count integer CHECK (chosen_open_count IS NULL OR chosen_open_count >= 0),
    -- INTAKE: on ticket creation, by the system. BATCH: applied by a C/S manager.
    trigger text NOT NULL CHECK (trigger IN ('INTAKE', 'BATCH')),
    decided_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((trigger = 'BATCH') = (decided_by IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ticket_assignments_ticket
    ON cs.ticket_assignments (ticket_id, created_at DESC, id DESC);

-- Improvement requests (cs_owner): one row per rule decision, NO_MATCH included, append-only.
-- A separate table, not a subject kind on ticket_assignments, so each keeps its foreign key and
-- its own reason list. ON DELETE CASCADE only matters to administrators: cs_app cannot delete
-- requests.
CREATE TABLE IF NOT EXISTS cs.improvement_assignments (
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES cs.improvement_requests(id) ON DELETE CASCADE,
    user_id uuid,
    rule_version text NOT NULL,
    reason_code text NOT NULL CHECK (reason_code IN ('FAMILY', 'NO_MATCH')),
    matched_family text,
    candidate_count integer NOT NULL CHECK (candidate_count >= 0),
    chosen_open_count integer CHECK (chosen_open_count IS NULL OR chosen_open_count >= 0),
    -- CANDIDATE: when the system saves a candidate. BATCH: applied by a C/S manager.
    trigger text NOT NULL CHECK (trigger IN ('CANDIDATE', 'BATCH')),
    decided_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((reason_code = 'NO_MATCH') = (user_id IS NULL)),
    CHECK ((trigger = 'BATCH') = (decided_by IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS improvement_assignments_request
    ON cs.improvement_assignments (request_id, created_at DESC, id DESC);

REVOKE ALL ON cs.agent_skills, cs.ticket_assignments, cs.improvement_assignments FROM cs_app;
GRANT SELECT ON cs.agent_skills TO cs_app;
GRANT SELECT, INSERT ON cs.ticket_assignments, cs.improvement_assignments TO cs_app;
COMMIT;
