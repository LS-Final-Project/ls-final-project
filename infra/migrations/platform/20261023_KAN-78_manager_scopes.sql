-- KAN-78: department codes and manager scopes (contracts/platform/permissions.md 1.2).
-- Local bootstrap applies this as postgres. Runtime users never execute DDL.
-- Rows are written only by infra/sql/register_user.sql or the local seed, never by an API.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;

-- One code set for C/S routing, transfer and manager scope. Codes follow the synthetic
-- department reference (ref/departments.csv). GCS_L1 is the C/S department.
CREATE TABLE IF NOT EXISTS identity.departments (
    code text PRIMARY KEY CHECK (code ~ '^[A-Z][A-Z0-9_]*$'),
    name_ko text NOT NULL,
    name_en text NOT NULL
);
INSERT INTO identity.departments (code, name_ko, name_en) VALUES
    ('GCS_L1', '글로벌 C/S 센터', 'Global CS Center'),
    ('TS_LV', '저압기기 기술지원', 'LV Distribution Technical Support'),
    ('TS_MV', '고압·배전반 기술지원', 'MV & Switchgear Technical Support'),
    ('TS_TRF', '변압기 기술지원', 'Transformer Engineering Support'),
    ('TS_AUTO', '자동화 기술지원', 'Automation Technical Support'),
    ('TS_ENERGY', '에너지솔루션 기술지원', 'Energy Solutions Technical Support'),
    ('RND_FW', '펌웨어 개발', 'Firmware R&D'),
    ('QA_CLAIM', '품질보증/클레임', 'Quality Assurance & Claim'),
    ('FIELD_SVC', '현장 서비스', 'Field Service'),
    ('CERT', '인증·규격', 'Certification & Compliance'),
    ('SALES_OPS', '해외영업 운영', 'Overseas Sales Operations'),
    ('SCM', '주문·물류', 'Order & Logistics (SCM)'),
    ('WARRANTY_DESK', '보증·RMA 데스크', 'Warranty & RMA Desk')
ON CONFLICT (code) DO NOTHING;

-- Departments a manager may see in detail and, for GCS_L1, reassign C/S owners in.
CREATE TABLE IF NOT EXISTS identity.manager_departments (
    user_id uuid NOT NULL REFERENCES identity.users(id),
    dept_code text NOT NULL REFERENCES identity.departments(code),
    PRIMARY KEY (user_id, dept_code)
);

-- TFT projects a manager may see in detail. project_id points at tft.projects, which
-- another service owns, so no cross-schema foreign key. The registration script
-- checks the project exists.
CREATE TABLE IF NOT EXISTS identity.manager_projects (
    user_id uuid NOT NULL REFERENCES identity.users(id),
    project_id uuid NOT NULL,
    PRIMARY KEY (user_id, project_id)
);

-- The platform reads scopes for the session; no login role can grant them.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON identity.departments, identity.manager_departments, identity.manager_projects
    FROM platform_app;

COMMIT;
