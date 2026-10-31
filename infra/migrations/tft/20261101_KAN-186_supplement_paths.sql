-- KAN-186 (KAN-182 storage part): supplement paths, deficiency causes and next owner of the RFP
-- API spec v1.3 §2 (proposal final V_1 4.4).
-- path: spec v1.3 adds OWN_DEVELOPMENT (own development check, R&D) and
--   CUSTOMER_SPECIFIED_PROCUREMENT (customer-specified item); OWN_TERMS_REVIEW (own commercial
--   terms review) is requested for the spec (KAN-186 comment).
-- deficiency_cause: DESIGNATED_MODEL, COMMERCIAL, CORE_EQUIPMENT_MISSING.
-- next_owner_field: RND (an R&D advisory question is open).
-- Only widens: every stored value stays valid. Needs 20261029_KAN-182_rfp_response.sql (T7).
-- Re-runnable. The R&D check columns and internal questions come with the next KAN-186 PR.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.requirement_versions DROP CONSTRAINT IF EXISTS requirement_versions_path_check;
ALTER TABLE tft.requirement_versions ADD CONSTRAINT requirement_versions_path_check CHECK (
    path IN ('OTHER_OWN_PRODUCT', 'OWN_DEVELOPMENT', 'OWN_TERMS_REVIEW', 'ALTERNATIVE_SPEC',
             'EXTERNAL_PRODUCT', 'TASK_SUBCONTRACT', 'JOINT_PROPOSAL',
             'CUSTOMER_SPECIFIED_PROCUREMENT', 'NONE')
);
ALTER TABLE tft.requirement_versions DROP CONSTRAINT IF EXISTS requirement_versions_next_owner_field_check;
ALTER TABLE tft.requirement_versions ADD CONSTRAINT requirement_versions_next_owner_field_check CHECK (
    next_owner_field IN ('SALES', 'TECH', 'PURCHASE', 'CUSTOMER', 'RND')
);
ALTER TABLE tft.supplement_options DROP CONSTRAINT IF EXISTS supplement_options_path_check;
ALTER TABLE tft.supplement_options ADD CONSTRAINT supplement_options_path_check CHECK (
    path IN ('OTHER_OWN_PRODUCT', 'OWN_DEVELOPMENT', 'OWN_TERMS_REVIEW', 'ALTERNATIVE_SPEC',
             'EXTERNAL_PRODUCT', 'TASK_SUBCONTRACT', 'JOINT_PROPOSAL',
             'CUSTOMER_SPECIFIED_PROCUREMENT', 'NONE')
);
ALTER TABLE tft.supplement_options DROP CONSTRAINT IF EXISTS supplement_options_deficiency_cause_check;
ALTER TABLE tft.supplement_options ADD CONSTRAINT supplement_options_deficiency_cause_check CHECK (
    deficiency_cause IN ('RATING', 'CERTIFICATION', 'FORM', 'TASK', 'DESIGNATED_MODEL',
                         'COMMERCIAL', 'CORE_EQUIPMENT_MISSING')
);
ALTER TABLE tft.supplement_selections DROP CONSTRAINT IF EXISTS supplement_selections_path_check;
ALTER TABLE tft.supplement_selections ADD CONSTRAINT supplement_selections_path_check CHECK (
    path IN ('OTHER_OWN_PRODUCT', 'OWN_DEVELOPMENT', 'OWN_TERMS_REVIEW', 'ALTERNATIVE_SPEC',
             'EXTERNAL_PRODUCT', 'TASK_SUBCONTRACT', 'JOINT_PROPOSAL',
             'CUSTOMER_SPECIFIED_PROCUREMENT', 'NONE')
);

COMMIT;
