-- KAN-122: department staffing requests are reviewed, edited and issued by the
-- responsible sales user (KAN-57 decision 2026-10-21: no intermediate approval step).
-- Adds the request version, the "due date undecided" flag and an append-only revision
-- history, plus two columns requested by KAN-78 (김나영) that link TFT data to identity:
--   * tft.projects.sales_user_id       -- the responsible sales user (identity.users.id)
--   * tft.organizations.dept_code      -- identity.departments.code of the organization
-- Applied after 20261023_KAN-119_projects.sql, which this file never rewrites.
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

-- KAN-78 request: responsible sales user of a project. Login user ID (identity.users),
-- no cross-schema FK, like responsible_user_id in KAN-119. Null means not assigned yet;
-- issuing a staffing request is refused until a sales user is assigned.
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS sales_user_id uuid;
CREATE INDEX IF NOT EXISTS projects_sales_user ON tft.projects (sales_user_id)
    WHERE sales_user_id IS NOT NULL;

-- KAN-78 request: identity.departments.code of the organization (same value, same
-- format). This is the key between a user's department (identity.users.dept) and the
-- TFT organization a request is addressed to. One department per company at most.
ALTER TABLE tft.organizations ADD COLUMN IF NOT EXISTS dept_code text;
DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'organizations_dept_code_format'
    ) THEN
        ALTER TABLE tft.organizations ADD CONSTRAINT organizations_dept_code_format
            CHECK (dept_code IS NULL OR dept_code ~ '^[A-Z][A-Z0-9_]*$');
    END IF;
END
$constraints$;
CREATE UNIQUE INDEX IF NOT EXISTS organizations_company_dept_code
    ON tft.organizations (company_code, dept_code) WHERE dept_code IS NOT NULL;

-- Request version for If-Match, and the explicit "due date undecided" mark. A null
-- due_at alone is "not entered yet"; due_undecided = true is the sales user's decision.
-- Issuing requires one of the two (API 422 EVIDENCE_REQUIRED, enforced here as well).
ALTER TABLE tft.staffing_requests
    ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1 CHECK (version > 0);
ALTER TABLE tft.staffing_requests
    ADD COLUMN IF NOT EXISTS due_undecided boolean NOT NULL DEFAULT false;
DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'staffing_requests_due_exclusive'
    ) THEN
        ALTER TABLE tft.staffing_requests ADD CONSTRAINT staffing_requests_due_exclusive
            CHECK (NOT due_undecided OR due_at IS NULL);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'staffing_requests_issued_due'
    ) THEN
        ALTER TABLE tft.staffing_requests ADD CONSTRAINT staffing_requests_issued_due
            CHECK (status = 'DRAFT' OR due_at IS NOT NULL OR due_undecided);
    END IF;
END
$constraints$;

-- Append-only change history of a request: one row per new version. EDIT keeps
-- {"field": {"from": old, "to": new}}; ISSUE keeps the status transition. The actor is
-- the login user who made the change (resolved by the API, never from the body).
CREATE TABLE IF NOT EXISTS tft.staffing_request_revisions (
    id uuid PRIMARY KEY,
    staffing_request_id uuid NOT NULL REFERENCES tft.staffing_requests(id),
    -- Version of the request after this change.
    version integer NOT NULL CHECK (version > 0),
    action text NOT NULL CHECK (action IN ('EDIT', 'ISSUE')),
    actor_user_id uuid NOT NULL,
    changes jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (staffing_request_id, version)
);
CREATE INDEX IF NOT EXISTS staffing_request_revisions_request
    ON tft.staffing_request_revisions (staffing_request_id, occurred_at);
-- The service may add revisions but never rewrite or remove them.
REVOKE UPDATE, DELETE, TRUNCATE ON tft.staffing_request_revisions FROM tft_app;

COMMIT;
