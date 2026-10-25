-- KAN-119: TFT projects and the six separated stage records (KAN-57 decisions,
-- TFT proposal 4.5 / 6.1): initial review, department recommendation, experience,
-- availability, composition versions and the responsible-user decision.
-- Write APIs (requests, recommendations, versions, confirmation) are later tickets.
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

-- Organizations inside the host company. The demo is one company, but the company
-- code is kept so affiliated-company data can be distinguished later.
CREATE TABLE IF NOT EXISTS tft.organizations (
    id uuid PRIMARY KEY,
    source text NOT NULL,
    source_instance text NOT NULL,
    external_id text,
    schema_version text NOT NULL DEFAULT '1.0',
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    ingested_at timestamptz NOT NULL DEFAULT now(),
    company_code text NOT NULL,
    name text NOT NULL,
    parent_id uuid REFERENCES tft.organizations(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS organizations_source_external_id
    ON tft.organizations (source, source_instance, external_id) WHERE external_id IS NOT NULL;

-- Work-field tags are a classification. They never replace real department names
-- or login roles, and one organization may carry several.
CREATE TABLE IF NOT EXISTS tft.organization_field_tags (
    organization_id uuid NOT NULL REFERENCES tft.organizations(id),
    field_tag text NOT NULL CHECK (field_tag IN (
        'DESIGN', 'CIVIL_STRUCTURE', 'CONSTRUCTION_TRANSPORT', 'QUALITY_TEST',
        'PROJECT_MANAGEMENT'
    )),
    PRIMARY KEY (organization_id, field_tag)
);

-- Candidate personnel. A candidate ID is never a login user ID (identity.users).
CREATE TABLE IF NOT EXISTS tft.candidates (
    id uuid PRIMARY KEY,
    source text NOT NULL,
    source_instance text NOT NULL,
    external_id text,
    schema_version text NOT NULL DEFAULT '1.0',
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    ingested_at timestamptz NOT NULL DEFAULT now(),
    organization_id uuid NOT NULL REFERENCES tft.organizations(id),
    display_name text NOT NULL,
    job_title text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS candidates_source_external_id
    ON tft.candidates (source, source_instance, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS candidates_organization ON tft.candidates (organization_id);

CREATE TABLE IF NOT EXISTS tft.projects (
    id uuid PRIMARY KEY,
    source text NOT NULL,
    source_instance text NOT NULL,
    external_id text,
    schema_version text NOT NULL DEFAULT '1.0',
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    company_code text NOT NULL,
    name text NOT NULL,
    client_name text,
    -- Coarse project stage. Each stage keeps its own records and statuses below;
    -- this column never stands in for them, and partial role confirmation is not CONFIRMED.
    status text NOT NULL DEFAULT 'INITIAL_REVIEW'
        CHECK (status IN ('INITIAL_REVIEW', 'STAFFING', 'COMPOSING', 'CONFIRMED')),
    -- Login user ID (identity.users, no cross-schema FK). Changed only together with
    -- a tft.responsible_changes row. Null means no responsible user: confirmation is refused.
    responsible_user_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS projects_source_external_id
    ON tft.projects (source, source_instance, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS projects_created_at_id ON tft.projects (created_at DESC, id DESC);

-- Stage 1: initial review. RFP requirements with their source position (proposal 6.1:
-- document name, version, page, clause, excerpt). Document IDs follow the data
-- contract and stay null until the document pipeline stores RFP versions.
CREATE TABLE IF NOT EXISTS tft.rfp_requirements (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    requirement_code text NOT NULL,
    field_tag text CHECK (field_tag IN (
        'DESIGN', 'CIVIL_STRUCTURE', 'CONSTRUCTION_TRANSPORT', 'QUALITY_TEST',
        'PROJECT_MANAGEMENT'
    )),
    title text NOT NULL,
    description text,
    document_name text,
    document_id uuid,
    document_version_id uuid,
    clause text,
    page integer CHECK (page IS NULL OR page > 0),
    excerpt text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, requirement_code)
);

-- Roles the sales user derives from the requirements. `status` is the role request
-- pipeline of proposal 4.5 (drafting -> issued -> recommendations received -> department
-- verified -> comparing -> in composition -> confirmed). Headcount null means undecided.
CREATE TABLE IF NOT EXISTS tft.required_roles (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    role_code text NOT NULL,
    title text NOT NULL,
    field_tag text CHECK (field_tag IN (
        'DESIGN', 'CIVIL_STRUCTURE', 'CONSTRUCTION_TRANSPORT', 'QUALITY_TEST',
        'PROJECT_MANAGEMENT'
    )),
    tasks text,
    experience_condition text,
    is_core boolean NOT NULL DEFAULT true,
    headcount integer CHECK (headcount IS NULL OR headcount > 0),
    period_start date,
    period_end date,
    allocation_rate numeric(5, 2)
        CHECK (allocation_rate IS NULL OR (allocation_rate >= 0 AND allocation_rate <= 100)),
    status text NOT NULL DEFAULT 'DRAFTING' CHECK (status IN (
        'DRAFTING', 'ISSUED', 'RECOMMENDATIONS_RECEIVED', 'DEPT_VERIFIED', 'COMPARING',
        'IN_COMPOSITION', 'CONFIRMED'
    )),
    -- Initial review by the responsible sales user (login user ID).
    reviewed_by_user_id uuid,
    reviewed_at timestamptz,
    -- When no candidate or not enough information: who acts next and by when (4.5).
    next_action text,
    next_action_organization_id uuid REFERENCES tft.organizations(id),
    reply_due_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, role_code),
    CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end),
    CHECK (status = 'DRAFTING' OR (reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL))
);

-- Requirement <-> role is N:M with the reason the role was derived (6.1).
CREATE TABLE IF NOT EXISTS tft.role_requirement_links (
    required_role_id uuid NOT NULL REFERENCES tft.required_roles(id),
    requirement_id uuid NOT NULL REFERENCES tft.rfp_requirements(id),
    rationale text,
    PRIMARY KEY (required_role_id, requirement_id)
);

-- Required and preferred competencies are kept apart (proposal 4.3).
CREATE TABLE IF NOT EXISTS tft.role_competencies (
    id uuid PRIMARY KEY,
    required_role_id uuid NOT NULL REFERENCES tft.required_roles(id),
    kind text NOT NULL CHECK (kind IN ('REQUIRED', 'PREFERRED')),
    description text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS role_competencies_role ON tft.role_competencies (required_role_id);

-- Department staffing requests. The sales user reviews and issues them directly;
-- there is no intermediate approval step (KAN-57).
CREATE TABLE IF NOT EXISTS tft.staffing_requests (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    required_role_id uuid NOT NULL REFERENCES tft.required_roles(id),
    organization_id uuid NOT NULL REFERENCES tft.organizations(id),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    -- Lead department or supporting department for this role.
    participation text NOT NULL DEFAULT 'LEAD' CHECK (participation IN ('LEAD', 'SUPPORT')),
    status text NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ISSUED', 'ANSWERED', 'CLOSED')),
    issued_by_user_id uuid,
    issued_at timestamptz,
    due_at timestamptz,
    message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status = 'DRAFT' OR (issued_by_user_id IS NOT NULL AND issued_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS staffing_requests_project ON tft.staffing_requests (project_id);
CREATE INDEX IF NOT EXISTS staffing_requests_organization
    ON tft.staffing_requests (organization_id, status);

-- Stage 2: department recommendation. Recommendation is recorded apart from the
-- experience, competency and availability checks below, even when the same user does both.
CREATE TABLE IF NOT EXISTS tft.candidate_recommendations (
    id uuid PRIMARY KEY,
    staffing_request_id uuid NOT NULL REFERENCES tft.staffing_requests(id),
    candidate_id uuid NOT NULL REFERENCES tft.candidates(id),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    recommended_by_user_id uuid NOT NULL,
    recommended_at timestamptz NOT NULL,
    rationale text,
    status text NOT NULL DEFAULT 'RECOMMENDED' CHECK (status IN ('RECOMMENDED', 'WITHDRAWN')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (staffing_request_id, candidate_id)
);
CREATE INDEX IF NOT EXISTS candidate_recommendations_candidate
    ON tft.candidate_recommendations (candidate_id);

-- Check statuses (proposal 4.5, data contract): UNSPECIFIED = nothing recorded,
-- NEEDS_CHECK = recorded or missing data but not confirmed (never "lacking"),
-- CONFIRMED and UNMET = checked by a department user at a time with a basis.

-- Stage 3: experience evidence for a recommendation (similar projects, period, actual tasks).
CREATE TABLE IF NOT EXISTS tft.experience_evidence (
    id uuid PRIMARY KEY,
    recommendation_id uuid NOT NULL REFERENCES tft.candidate_recommendations(id),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    summary text NOT NULL,
    project_name text,
    role_title text,
    period_start date,
    period_end date,
    evidence_source text,
    status text NOT NULL DEFAULT 'NEEDS_CHECK'
        CHECK (status IN ('UNSPECIFIED', 'NEEDS_CHECK', 'CONFIRMED', 'UNMET')),
    verified_by_user_id uuid,
    verified_at timestamptz,
    basis text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end),
    CHECK (
        status NOT IN ('CONFIRMED', 'UNMET')
        OR (verified_by_user_id IS NOT NULL AND verified_at IS NOT NULL AND basis IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS experience_evidence_recommendation
    ON tft.experience_evidence (recommendation_id);

-- Competency check per recommendation and role competency (required / preferred apart).
CREATE TABLE IF NOT EXISTS tft.competency_checks (
    id uuid PRIMARY KEY,
    recommendation_id uuid NOT NULL REFERENCES tft.candidate_recommendations(id),
    competency_id uuid NOT NULL REFERENCES tft.role_competencies(id),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    status text NOT NULL DEFAULT 'NEEDS_CHECK'
        CHECK (status IN ('UNSPECIFIED', 'NEEDS_CHECK', 'CONFIRMED', 'UNMET')),
    checked_by_user_id uuid,
    checked_at timestamptz,
    basis text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recommendation_id, competency_id),
    CHECK (
        status NOT IN ('CONFIRMED', 'UNMET')
        OR (checked_by_user_id IS NOT NULL AND checked_at IS NOT NULL AND basis IS NOT NULL)
    )
);

-- Stage 4: availability check for a recommendation. CONFIRMED requires the period,
-- allocation rate, checker, time and basis (data contract); UNMET requires the checker,
-- time and basis. UNSPECIFIED and NEEDS_CHECK are kept apart from both.
CREATE TABLE IF NOT EXISTS tft.availability_checks (
    id uuid PRIMARY KEY,
    recommendation_id uuid NOT NULL REFERENCES tft.candidate_recommendations(id),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    period_start date,
    period_end date,
    -- Percent of working time the candidate can commit in the period.
    allocation_rate numeric(5, 2)
        CHECK (allocation_rate IS NULL OR (allocation_rate >= 0 AND allocation_rate <= 100)),
    constraints_note text,
    checked_by_user_id uuid,
    checked_at timestamptz,
    basis text,
    status text NOT NULL DEFAULT 'UNSPECIFIED'
        CHECK (status IN ('UNSPECIFIED', 'NEEDS_CHECK', 'CONFIRMED', 'UNMET')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end),
    CHECK (
        status <> 'CONFIRMED'
        OR (
            period_start IS NOT NULL AND period_end IS NOT NULL
            AND allocation_rate IS NOT NULL AND checked_by_user_id IS NOT NULL
            AND checked_at IS NOT NULL AND basis IS NOT NULL
        )
    ),
    CHECK (
        status <> 'UNMET'
        OR (checked_by_user_id IS NOT NULL AND checked_at IS NOT NULL AND basis IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS availability_checks_recommendation
    ON tft.availability_checks (recommendation_id);

-- Stage 5: composition versions. A new proposal is a new version; earlier versions
-- are never overwritten, so the service account cannot update or delete them.
CREATE TABLE IF NOT EXISTS tft.composition_versions (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    version integer NOT NULL CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL
        CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    created_by_user_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    note text,
    UNIQUE (project_id, version)
);

-- One row per role slot in a version. PROPOSED and NEEDS_REINFORCEMENT keep the
-- remaining conditions; CONFIRMED is the responsible user's decision for that role.
-- A null recommendation records the role as an open gap with who acts next.
CREATE TABLE IF NOT EXISTS tft.composition_members (
    id uuid PRIMARY KEY,
    composition_version_id uuid NOT NULL REFERENCES tft.composition_versions(id),
    required_role_id uuid NOT NULL REFERENCES tft.required_roles(id),
    recommendation_id uuid REFERENCES tft.candidate_recommendations(id),
    status text NOT NULL DEFAULT 'PROPOSED'
        CHECK (status IN ('PROPOSED', 'NEEDS_REINFORCEMENT', 'CONFIRMED')),
    rationale text,
    remaining_conditions text,
    gap_note text,
    next_action_organization_id uuid REFERENCES tft.organizations(id),
    follow_up_due_at timestamptz,
    CHECK (recommendation_id IS NOT NULL OR gap_note IS NOT NULL),
    CHECK (status <> 'CONFIRMED' OR recommendation_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS composition_members_recommendation
    ON tft.composition_members (composition_version_id, recommendation_id)
    WHERE recommendation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS composition_members_version
    ON tft.composition_members (composition_version_id);
REVOKE UPDATE, DELETE, TRUNCATE ON tft.composition_versions, tft.composition_members FROM tft_app;

-- Stage 6: responsible-user decision. Assignment and change history for
-- projects.responsible_user_id, written by the managing manager (route checks in T09).
CREATE TABLE IF NOT EXISTS tft.responsible_changes (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    changed_by_user_id uuid NOT NULL,
    previous_user_id uuid,
    -- Null clears the responsible user.
    new_user_id uuid,
    reason text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CHECK (previous_user_id IS DISTINCT FROM new_user_id)
);
CREATE INDEX IF NOT EXISTS responsible_changes_project
    ON tft.responsible_changes (project_id, occurred_at DESC);

-- Final confirmation by the current responsible user, bound to one composition version.
CREATE TABLE IF NOT EXISTS tft.final_confirmations (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    composition_version_id uuid NOT NULL UNIQUE REFERENCES tft.composition_versions(id),
    confirmed_by_user_id uuid NOT NULL,
    confirmed_at timestamptz NOT NULL DEFAULT now(),
    note text
);
CREATE INDEX IF NOT EXISTS final_confirmations_project
    ON tft.final_confirmations (project_id, confirmed_at DESC);
-- History and confirmations are append-only for the service account.
REVOKE UPDATE, DELETE, TRUNCATE ON tft.responsible_changes, tft.final_confirmations FROM tft_app;

COMMIT;
