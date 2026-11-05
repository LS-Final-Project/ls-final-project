-- KAN-186 (KAN-182 storage part): R&D check of the RFP API spec v1.3 §5.2·§5.3·§9.
-- requirement_versions.rnd_check (6 values, server computed), the waiver reason of a partner
-- path chosen while the R&D check was open, internal advisory questions (target, target_dept,
-- three internal issue types), R&D reply fields, and the department kind map.
-- Needs 20261029_KAN-182_rfp_response.sql (T7). Re-runnable; existing rows keep their meaning
-- (rnd_check NOT_REQUIRED, questions CUSTOMER).
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.requirement_versions
    ADD COLUMN IF NOT EXISTS rnd_check text NOT NULL DEFAULT 'NOT_REQUIRED'
        CHECK (rnd_check IN ('NOT_REQUIRED', 'PENDING', 'CONFIRMED_FEASIBLE',
                             'CONFIRMED_NOT_FEASIBLE', 'CONFIRMED_SCHEDULE_MISMATCH', 'WAIVED'));

-- A partner path chosen while an R&D question was open needs a reason of at least 20 characters.
ALTER TABLE tft.supplement_selections
    ADD COLUMN IF NOT EXISTS rnd_check_waiver_reason text
        CHECK (rnd_check_waiver_reason IS NULL
               OR length(rnd_check_waiver_reason) BETWEEN 20 AND 1000);

-- Internal advisory questions use the same table: CUSTOMER is the v1.2 behaviour.
ALTER TABLE tft.customer_questions
    ADD COLUMN IF NOT EXISTS target text NOT NULL DEFAULT 'CUSTOMER'
        CHECK (target IN ('CUSTOMER', 'INTERNAL')),
    ADD COLUMN IF NOT EXISTS target_dept text;
ALTER TABLE tft.customer_questions DROP CONSTRAINT IF EXISTS customer_questions_target_dept_check;
ALTER TABLE tft.customer_questions ADD CONSTRAINT customer_questions_target_dept_check CHECK (
    (target = 'INTERNAL') = (target_dept IS NOT NULL)
);
ALTER TABLE tft.customer_questions DROP CONSTRAINT IF EXISTS customer_questions_issue_type_check;
ALTER TABLE tft.customer_questions ADD CONSTRAINT customer_questions_issue_type_check CHECK (
    issue_type IN ('EMPTY_VALUE', 'CONFLICT', 'SCOPE_UNCLEAR', 'CONDITION_MISSING')
    -- The three internal issue types are for internal questions only.
    OR (target = 'INTERNAL' AND issue_type IN ('RND_FEASIBILITY', 'TECH_ADVICE', 'PURCHASE_TERMS'))
);

-- R&D answer fields. That they come only with an internal question to an R&D department is
-- checked by the API (rnd.check_reply_fields), not by the table; the table only refuses
-- schedule fields without a feasibility value.
ALTER TABLE tft.question_replies
    ADD COLUMN IF NOT EXISTS rnd_feasibility text
        CHECK (rnd_feasibility IN ('FEASIBLE', 'FEASIBLE_WITH_SCHEDULE', 'NOT_FEASIBLE', 'UNDETERMINED')),
    ADD COLUMN IF NOT EXISTS earliest_available_on date,
    ADD COLUMN IF NOT EXISTS schedule_fits boolean;
ALTER TABLE tft.question_replies DROP CONSTRAINT IF EXISTS question_replies_rnd_fields_check;
ALTER TABLE tft.question_replies ADD CONSTRAINT question_replies_rnd_fields_check CHECK (
    rnd_feasibility IS NOT NULL OR (earliest_available_on IS NULL AND schedule_fits IS NULL)
);

-- Department kind map of the TFT service. The platform department master is not changed.
-- A department without a row counts as TECH.
CREATE TABLE IF NOT EXISTS tft.department_kinds (
    dept_code text PRIMARY KEY,
    dept_kind text NOT NULL CHECK (dept_kind IN ('SALES', 'RND', 'TECH', 'PURCHASE', 'OTHER'))
);
-- Only the departments of the demonstration accounts (identity.departments codes).
INSERT INTO tft.department_kinds (dept_code, dept_kind) VALUES
    ('SALES_OPS', 'SALES'),
    ('RND_FW', 'RND'),
    ('TS_TRF', 'TECH'),
    ('TS_MV', 'TECH'),
    ('TS_LV', 'TECH'),
    ('CERT', 'TECH'),
    ('SCM', 'PURCHASE')
ON CONFLICT (dept_code) DO NOTHING;
GRANT SELECT ON tft.department_kinds TO tft_app;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON tft.department_kinds FROM tft_app;

COMMIT;
