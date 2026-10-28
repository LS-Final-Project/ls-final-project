-- KAN-182 (A01): RFP 자사 대응·협력사 보완 저장 구조. 관리자 연결로 한 번 적용한다(KAN-119·122 뒤).
-- 원본 합의: "RFP 자사 대응·협력사 보완 API 명세 v1.1" §6. 상세는 contracts/tft/rfp-data.md.
-- 모든 업무 행에 판본(version), 두 출처 축(data_origin, derivation_kind), known_at을 둔다.
-- 이력·판본 표는 tft_app이 추가만 한다(UPDATE·DELETE 없음). 최신 판본 포인터·상태 열만 UPDATE를 연다.
-- 기존 tft.projects.status와 KAN-119의 인력 구성 표는 손대지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

-- 프로젝트: 산출물 상태와 판단 상태를 따로 둔다(서로 대체하지 않는다).
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS output_status text NOT NULL DEFAULT 'INTAKE'
    CHECK (output_status IN ('INTAKE', 'ANALYZING', 'REVIEW_DRAFT'));
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS judgement_status text NOT NULL DEFAULT 'NEEDS_REVIEW'
    CHECK (judgement_status IN ('NEEDS_REVIEW', 'CONDITION_CHECK', 'OWNER_CONFIRMED'));
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT 'INITIAL_PROPOSAL'
    CHECK (purpose IN ('INITIAL_PROPOSAL'));
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS customer_goal text
    CHECK (length(customer_goal) <= 4000);
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS review_departments text[] NOT NULL DEFAULT '{}';
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS review_version integer CHECK (review_version > 0);

-- 담당 sales·책임자 지정 이력(manager). 추가만.
CREATE TABLE IF NOT EXISTS tft.project_assignments (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    project_version integer NOT NULL CHECK (project_version > 0),
    previous_sales_user_id uuid,
    sales_user_id uuid,
    previous_responsible_user_id uuid,
    responsible_user_id uuid,
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
    changed_by uuid NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now()
);

-- 문서와 판본. 판본은 바뀌지 않고 새 판본만 쌓인다.
CREATE TABLE IF NOT EXISTS tft.documents (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    kind text NOT NULL CHECK (kind IN ('RFP_SPEC', 'DRAWING', 'ADDENDUM', 'DATASHEET', 'ANNOTATED_PDF')),
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    note text CHECK (length(note) <= 2000),
    -- 메모 PDF(ANNOTATED_PDF)가 달린 원문.
    base_document_id uuid REFERENCES tft.documents(id),
    latest_version integer NOT NULL DEFAULT 1 CHECK (latest_version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tft.document_versions (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES tft.documents(id),
    version integer NOT NULL CHECK (version > 0),
    mime text NOT NULL,
    storage_key text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size bigint NOT NULL CHECK (size BETWEEN 0 AND 26214400),
    -- DOCX는 편집 흔적을 읽을 수 있고 PDF는 확인 불가다.
    edit_trace_support boolean NOT NULL,
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    uploaded_by uuid NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, version)
);

-- 조항 트리. 문서 판본마다 추출 결과를 새로 쓴다(DERIVED).
CREATE TABLE IF NOT EXISTS tft.clauses (
    id uuid PRIMARY KEY,
    document_version_id uuid NOT NULL REFERENCES tft.document_versions(id),
    clause_no text NOT NULL CHECK (length(clause_no) BETWEEN 1 AND 64),
    parent_clause_id uuid REFERENCES tft.clauses(id),
    depth integer NOT NULL CHECK (depth >= 0),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    title text,
    -- 현행 텍스트. 취소선 값은 섞지 않는다.
    text text NOT NULL,
    page integer CHECK (page > 0),
    cross_references jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(cross_references) = 'array'),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    UNIQUE (document_version_id, ordinal)
);
CREATE TABLE IF NOT EXISTS tft.clause_deleted_values (
    clause_id uuid NOT NULL REFERENCES tft.clauses(id),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    text text NOT NULL,
    position integer NOT NULL CHECK (position >= 0),
    PRIMARY KEY (clause_id, ordinal)
);
CREATE TABLE IF NOT EXISTS tft.clause_flags (
    clause_id uuid NOT NULL REFERENCES tft.clauses(id),
    flag text NOT NULL CHECK (flag IN ('HIGHLIGHT', 'TEMPLATE_PLACEHOLDER', 'LINE_BREAK_REJOINED')),
    PRIMARY KEY (clause_id, flag)
);

-- 요구. 내용·판단은 판본 표에만 두고, 요구 행은 식별과 최신 판본 포인터만 가진다.
CREATE TABLE IF NOT EXISTS tft.requirements (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    code text NOT NULL CHECK (length(code) BETWEEN 1 AND 64),
    latest_version integer NOT NULL DEFAULT 1 CHECK (latest_version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, code)
);
CREATE TABLE IF NOT EXISTS tft.requirement_versions (
    requirement_id uuid NOT NULL REFERENCES tft.requirements(id),
    version integer NOT NULL CHECK (version > 0),
    -- 원문 위치
    document_version_id uuid NOT NULL REFERENCES tft.document_versions(id),
    clause_id uuid REFERENCES tft.clauses(id),
    page integer CHECK (page > 0),
    quote text NOT NULL,
    -- 요구 내용
    equipment text,
    task text,
    attribute text,
    value text,
    unit text,
    operator text NOT NULL CHECK (operator IN ('EQ', 'GTE', 'LTE', 'RANGE', 'TEXT')),
    standard_system text NOT NULL CHECK (standard_system IN ('IEC', 'IEEE', 'UL', 'NEMA', 'NONE')),
    necessity text NOT NULL CHECK (necessity IN ('MANDATORY', 'PREFERRED', 'CONDITION_BOUND', 'UNDETERMINED')),
    designated_model text,
    undetermined_items jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(undetermined_items) = 'array'),
    -- 판단: 점수 대신 네 가지 상태
    status text NOT NULL CHECK (status IN ('EVIDENCE_FOUND', 'CONDITIONAL', 'SPEC_GAP', 'INSUFFICIENT_EVIDENCE')),
    reason text NOT NULL,
    unresolved jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(unresolved) = 'array'),
    path text CHECK (path IN ('OTHER_OWN_PRODUCT', 'ALTERNATIVE_SPEC', 'EXTERNAL_PRODUCT',
        'TASK_SUBCONTRACT', 'JOINT_PROPOSAL', 'NONE')),
    next_owner_field text CHECK (next_owner_field IN ('SALES', 'TECH', 'PURCHASE', 'CUSTOMER')),
    provenance_kind text NOT NULL CHECK (provenance_kind IN ('ai_draft', 'human_input', 'system_record')),
    decided_by uuid,
    decided_at timestamptz,
    -- 승인된 변경 제안으로 만든 판본이면 그 제안.
    proposal_id uuid,
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (requirement_id, version),
    -- 사람 판단은 판단자·시각이 있어야 한다.
    CHECK (provenance_kind <> 'human_input' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS tft.requirement_conditions (
    requirement_id uuid NOT NULL,
    version integer NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    name text NOT NULL,
    value text NOT NULL,
    unit text,
    PRIMARY KEY (requirement_id, version, ordinal),
    FOREIGN KEY (requirement_id, version) REFERENCES tft.requirement_versions(requirement_id, version)
);

-- 자사 비교. 요구 판본에 묶인다.
CREATE TABLE IF NOT EXISTS tft.product_comparisons (
    id uuid PRIMARY KEY,
    requirement_id uuid NOT NULL,
    requirement_version integer NOT NULL,
    product_code text NOT NULL,
    variant text,
    scope jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(scope) = 'array'),
    lifecycle_status text,
    matched_attribute text,
    our_value text,
    our_unit text,
    our_conditions jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(our_conditions) = 'array'),
    -- 같은 속성·단위·조건끼리만 비교한다.
    comparable boolean NOT NULL,
    result text NOT NULL CHECK (result IN ('MEETS', 'DIFFERS', 'NOT_COMPARABLE', 'NO_DATA')),
    search_trace jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(search_trace) = 'object'),
    provenance_kind text NOT NULL CHECK (provenance_kind IN ('ai_draft', 'human_input', 'system_record')),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    CHECK (comparable OR result IN ('NOT_COMPARABLE', 'NO_DATA')),
    FOREIGN KEY (requirement_id, requirement_version)
        REFERENCES tft.requirement_versions(requirement_id, version)
);
CREATE TABLE IF NOT EXISTS tft.comparison_evidence (
    comparison_id uuid NOT NULL REFERENCES tft.product_comparisons(id),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    document text NOT NULL,
    clause text,
    document_version text,
    applicability text,
    limits text,
    PRIMARY KEY (comparison_id, ordinal)
);

-- 보완 경로 선택지(계산)와 사람의 선택(판본).
CREATE TABLE IF NOT EXISTS tft.supplement_options (
    id uuid PRIMARY KEY,
    requirement_id uuid NOT NULL,
    requirement_version integer NOT NULL,
    deficiency_cause text CHECK (deficiency_cause IN ('RATING', 'CERTIFICATION', 'FORM', 'TASK')),
    customer_permission text NOT NULL CHECK (customer_permission IN ('ALLOWED', 'FORBIDDEN', 'UNSTATED')),
    permission_clause_id uuid REFERENCES tft.clauses(id),
    path text NOT NULL CHECK (path IN ('OTHER_OWN_PRODUCT', 'ALTERNATIVE_SPEC', 'EXTERNAL_PRODUCT',
        'TASK_SUBCONTRACT', 'JOINT_PROPOSAL', 'NONE')),
    -- 고객이 금지한 경로는 false로 두고 추천하지 않는다.
    allowed boolean NOT NULL,
    conditions_to_confirm jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(conditions_to_confirm) = 'array'),
    required_expertise jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(required_expertise) = 'array'),
    candidate_codes text[] NOT NULL DEFAULT '{}',
    blocked_reason text,
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    CHECK (customer_permission <> 'FORBIDDEN' OR NOT allowed),
    CHECK (allowed OR blocked_reason IS NOT NULL),
    FOREIGN KEY (requirement_id, requirement_version)
        REFERENCES tft.requirement_versions(requirement_id, version)
);
CREATE TABLE IF NOT EXISTS tft.supplement_selections (
    requirement_id uuid NOT NULL REFERENCES tft.requirements(id),
    version integer NOT NULL CHECK (version > 0),
    requirement_version integer NOT NULL,
    path text NOT NULL CHECK (path IN ('OTHER_OWN_PRODUCT', 'ALTERNATIVE_SPEC', 'EXTERNAL_PRODUCT',
        'TASK_SUBCONTRACT', 'JOINT_PROPOSAL', 'NONE')),
    candidate_codes text[] NOT NULL DEFAULT '{}',
    role_split text NOT NULL CHECK (length(role_split) BETWEEN 1 AND 2000),
    conditions jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(conditions) = 'array'),
    note text CHECK (length(note) <= 1000),
    provenance_kind text NOT NULL DEFAULT 'human_input' CHECK (provenance_kind = 'human_input'),
    decided_by uuid NOT NULL,
    decided_at timestamptz NOT NULL DEFAULT now(),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    PRIMARY KEY (requirement_id, version),
    FOREIGN KEY (requirement_id, requirement_version)
        REFERENCES tft.requirement_versions(requirement_id, version)
);

-- 가상 협력사 프로필(KAN-184 profiles.json과 같은 키). 등급·점수는 두지 않는다.
CREATE TABLE IF NOT EXISTS tft.partner_profiles (
    candidate_code text NOT NULL CHECK (length(candidate_code) BETWEEN 1 AND 16),
    profile_version integer NOT NULL CHECK (profile_version > 0),
    display_name text NOT NULL,
    manufacturer_name text NOT NULL,
    profile_source text NOT NULL CHECK (profile_source = 'synthetic'),
    product_family text NOT NULL,
    products jsonb NOT NULL CHECK (jsonb_typeof(products) = 'array'),
    integrator_capable jsonb NOT NULL CHECK (jsonb_typeof(integrator_capable) = 'object'),
    spec_facts jsonb NOT NULL CHECK (jsonb_typeof(spec_facts) = 'array'),
    certifications jsonb NOT NULL CHECK (jsonb_typeof(certifications) = 'array'),
    similar_tasks text[] NOT NULL DEFAULT '{}',
    supply_regions text[] NOT NULL DEFAULT '{}',
    lead_time jsonb CHECK (lead_time IS NULL OR jsonb_typeof(lead_time) = 'object'),
    warranty text,
    notes text,
    confirmation jsonb NOT NULL CHECK (jsonb_typeof(confirmation) = 'object'),
    -- 가상 후보는 합성 자료 고정이다.
    data_origin text NOT NULL CHECK (data_origin = 'synthetic'),
    derivation_kind text NOT NULL CHECK (derivation_kind = 'PROVIDED'),
    known_at timestamptz NOT NULL,
    PRIMARY KEY (candidate_code, profile_version)
);
-- 후보 × 요구 등급(KAN-187 grading.py 출력). 계산할 때마다 판본을 늘린다.
CREATE TABLE IF NOT EXISTS tft.partner_grades (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    candidate_code text NOT NULL,
    profile_version integer NOT NULL,
    requirement_id uuid NOT NULL,
    requirement_version integer NOT NULL,
    grade text NOT NULL CHECK (grade IN ('MANDATORY_CONFIRMED', 'REVIEW', 'REFERENCE_ONLY', 'EXCLUDED')),
    reasons jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(reasons) = 'array'),
    exclusion_reasons jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(exclusion_reasons) = 'array'),
    mandatory_check jsonb NOT NULL CHECK (jsonb_typeof(mandatory_check) = 'object'),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind = 'DERIVED'),
    known_at timestamptz NOT NULL,
    -- 필수 제약을 어긴 후보는 점수와 무관하게 제외다.
    CHECK (grade = 'EXCLUDED' OR jsonb_array_length(exclusion_reasons) = 0),
    FOREIGN KEY (candidate_code, profile_version) REFERENCES tft.partner_profiles(candidate_code, profile_version),
    FOREIGN KEY (requirement_id, requirement_version)
        REFERENCES tft.requirement_versions(requirement_id, version)
);
CREATE TABLE IF NOT EXISTS tft.partner_grade_summaries (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    candidate_code text NOT NULL,
    profile_version integer NOT NULL,
    on_customer_manufacturer_list boolean,
    overall_grade text NOT NULL CHECK (overall_grade IN ('MANDATORY_CONFIRMED', 'REVIEW', 'REFERENCE_ONLY', 'EXCLUDED')),
    conditions_to_confirm jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(conditions_to_confirm) = 'array'),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind = 'DERIVED'),
    known_at timestamptz NOT NULL,
    FOREIGN KEY (candidate_code, profile_version) REFERENCES tft.partner_profiles(candidate_code, profile_version)
);

-- 고객 질문과 회신. 질문 내용은 바꾸지 않고 상태·판본 포인터만 바꾼다.
CREATE TABLE IF NOT EXISTS tft.customer_questions (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    requirement_id uuid REFERENCES tft.requirements(id),
    clause_id uuid REFERENCES tft.clauses(id),
    page integer CHECK (page > 0),
    issue_type text NOT NULL CHECK (issue_type IN ('EMPTY_VALUE', 'CONFLICT', 'SCOPE_UNCLEAR', 'CONDITION_MISSING')),
    question_text text NOT NULL CHECK (length(question_text) BETWEEN 1 AND 4000),
    -- 회신 전 내부 가정. ASSUMED이며 충족 판정에 쓰지 않는다.
    internal_assumption jsonb CHECK (internal_assumption IS NULL OR jsonb_typeof(internal_assumption) = 'object'),
    status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'REPLIED')),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tft.question_replies (
    id uuid PRIMARY KEY,
    question_id uuid NOT NULL UNIQUE REFERENCES tft.customer_questions(id),
    reply_text text NOT NULL CHECK (length(reply_text) BETWEEN 1 AND 20000),
    received_at timestamptz NOT NULL,
    -- 메일 ID 등 참조만. 메일 본문은 저장하지 않는다.
    source_ref text CHECK (length(source_ref) <= 512),
    recorded_by uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL
);

-- 변경 제안. 세 입구(web·customer_reply·pdf_annot)가 같은 승인 대기 목록을 쓴다.
CREATE TABLE IF NOT EXISTS tft.change_proposals (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    -- 조항에 맞출 수 없는 메모는 요구 없이 남긴다.
    requirement_id uuid REFERENCES tft.requirements(id),
    base_requirement_version integer,
    clause_unresolved boolean NOT NULL DEFAULT false,
    origin text NOT NULL CHECK (origin IN ('web', 'customer_reply', 'pdf_annot')),
    source_ref jsonb CHECK (source_ref IS NULL OR jsonb_typeof(source_ref) = 'object'),
    current jsonb NOT NULL CHECK (jsonb_typeof(current) = 'object'),
    proposed jsonb NOT NULL CHECK (jsonb_typeof(proposed) = 'object'),
    rationale text NOT NULL CHECK (length(rationale) BETWEEN 1 AND 2000),
    evidence_refs jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(evidence_refs) = 'array'),
    proposer_kind text NOT NULL CHECK (proposer_kind IN ('user', 'system')),
    proposer_user_id uuid,
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
    decided_by uuid,
    decided_at timestamptz,
    decision_note text CHECK (length(decision_note) <= 1000),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((requirement_id IS NULL) = clause_unresolved),
    CHECK ((proposer_kind = 'user') = (proposer_user_id IS NOT NULL)),
    CHECK ((status = 'PENDING') = (decided_by IS NULL AND decided_at IS NULL)),
    CHECK (status <> 'REJECTED' OR decision_note IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS change_proposals_pending ON tft.change_proposals (project_id, status, origin);

-- 초안과 문장. 판본마다 문장을 새로 쓰고 이전 판본을 고치지 않는다.
CREATE TABLE IF NOT EXISTS tft.drafts (
    project_id uuid PRIMARY KEY REFERENCES tft.projects(id),
    latest_version integer NOT NULL DEFAULT 1 CHECK (latest_version > 0),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS tft.draft_versions (
    project_id uuid NOT NULL REFERENCES tft.drafts(project_id),
    version integer NOT NULL CHECK (version > 0),
    provenance_kind text NOT NULL CHECK (provenance_kind IN ('ai_draft', 'human_input', 'system_record')),
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    derivation_kind text NOT NULL CHECK (derivation_kind IN ('PROVIDED', 'DERIVED', 'SYSTEM', 'ASSUMED')),
    known_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, version)
);
CREATE TABLE IF NOT EXISTS tft.draft_sentences (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    draft_version integer NOT NULL,
    section text NOT NULL CHECK (section IN ('CUSTOMER_GOAL_SCOPE', 'OUR_RESPONSE', 'DEVIATIONS',
        'PARTNER_SUPPLEMENT', 'ASSUMPTIONS_EXCLUSIONS', 'QUESTIONS_NEXT_ACTIONS')),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    -- 판본이 바뀌어도 같은 문장을 가리키는 식별자.
    sentence_key uuid NOT NULL,
    text text NOT NULL CHECK (length(text) BETWEEN 1 AND 4000),
    conditions jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(conditions) = 'array'),
    unconfirmed boolean NOT NULL DEFAULT false,
    evidence_missing boolean NOT NULL,
    provenance_kind text NOT NULL CHECK (provenance_kind IN ('ai_draft', 'human_input', 'system_record')),
    edit_reason text CHECK (length(edit_reason) <= 500),
    UNIQUE (project_id, draft_version, section, ordinal),
    UNIQUE (project_id, draft_version, sentence_key),
    FOREIGN KEY (project_id, draft_version) REFERENCES tft.draft_versions(project_id, version)
);
CREATE TABLE IF NOT EXISTS tft.draft_sentence_evidence (
    sentence_id uuid NOT NULL REFERENCES tft.draft_sentences(id),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    evidence_type text NOT NULL CHECK (evidence_type IN ('requirement', 'product', 'partner', 'clause', 'question')),
    evidence_id text NOT NULL,
    PRIMARY KEY (sentence_id, ordinal)
);
CREATE TABLE IF NOT EXISTS tft.review_versions (
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    review_version integer NOT NULL CHECK (review_version > 0),
    draft_version integer NOT NULL,
    judgement_status text NOT NULL CHECK (judgement_status IN ('NEEDS_REVIEW', 'CONDITION_CHECK', 'OWNER_CONFIRMED')),
    note text CHECK (length(note) <= 1000),
    saved_by uuid NOT NULL,
    saved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, review_version),
    FOREIGN KEY (project_id, draft_version) REFERENCES tft.draft_versions(project_id, version)
);
CREATE TABLE IF NOT EXISTS tft.exports (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    review_version integer NOT NULL,
    format text NOT NULL CHECK (format IN ('DOCX', 'PDF')),
    job_id uuid,
    storage_key text,
    sha256 text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size bigint CHECK (size >= 0),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK ((storage_key IS NULL) = (sha256 IS NULL) AND (sha256 IS NULL) = (size IS NULL)),
    FOREIGN KEY (project_id, review_version) REFERENCES tft.review_versions(project_id, review_version)
);

-- 재검토 표시. 판본 행은 고치지 않으므로 표시를 따로 쌓는다. 제안 승인이 연결 대상마다 한 행을
-- 넣고, 사람이 다시 확인하면 cleared_at·cleared_by만 채운다. needs_rereview = 해소 안 된 행이 있음.
CREATE TABLE IF NOT EXISTS tft.rereview_marks (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    -- requirement·comparison·supplement는 요구 ID, draft_sentence는 문장 sentence_key.
    target_kind text NOT NULL CHECK (target_kind IN ('requirement', 'comparison', 'supplement', 'draft_sentence')),
    target_id uuid NOT NULL,
    cause_proposal_id uuid NOT NULL REFERENCES tft.change_proposals(id),
    marked_at timestamptz NOT NULL DEFAULT now(),
    cleared_by uuid,
    cleared_at timestamptz,
    CHECK ((cleared_by IS NULL) = (cleared_at IS NULL)),
    UNIQUE (cause_proposal_id, target_kind, target_id)
);
CREATE INDEX IF NOT EXISTS rereview_marks_open ON tft.rereview_marks (project_id, target_kind, target_id)
    WHERE cleared_at IS NULL;

-- 상태를 만드는 POST의 멱등 기록. 같은 사용자·동작·키의 재전송은 첫 결과를 돌려주고,
-- 본문이 다르면 409 IDEMPOTENCY_CONFLICT다. 접수(경로 2)는 프로젝트가 아직 없어 project_id가 비어 있다.
CREATE TABLE IF NOT EXISTS tft.rfp_commands (
    actor_user_id uuid NOT NULL,
    operation text NOT NULL CHECK (operation IN ('project.create', 'document.upload', 'project.process',
        'supplement.select', 'question.create', 'question.reply', 'proposal.create', 'proposal.approve',
        'proposal.reject', 'review_version.create', 'export.create', 'annotation.process')),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    project_id uuid REFERENCES tft.projects(id),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    status_code integer NOT NULL CHECK (status_code BETWEEN 200 AND 299),
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_user_id, operation, idempotency_key)
);

-- 모든 쓰기의 감사. 변경 제안 승인은 제안자·승인자를 모두 남긴다.
CREATE TABLE IF NOT EXISTS tft.rfp_audit_events (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES tft.projects(id),
    entity text NOT NULL CHECK (entity IN ('project', 'assignment', 'document', 'requirement',
        'supplement_selection', 'question', 'proposal', 'draft', 'review_version', 'export', 'job')),
    entity_id text NOT NULL,
    action text NOT NULL CHECK (length(action) BETWEEN 1 AND 64),
    actor_user_id uuid,
    proposer_user_id uuid,
    approver_user_id uuid,
    detail jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(detail) = 'object'),
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rfp_audit_events_project ON tft.rfp_audit_events (project_id, at);

-- 권한: 런타임은 추가만. 포인터·상태 열만 UPDATE한다. 지우지 않는다.
REVOKE ALL ON
    tft.project_assignments, tft.documents, tft.document_versions, tft.clauses,
    tft.clause_deleted_values, tft.clause_flags, tft.requirements, tft.requirement_versions,
    tft.requirement_conditions, tft.product_comparisons, tft.comparison_evidence,
    tft.supplement_options, tft.supplement_selections, tft.partner_profiles, tft.partner_grades,
    tft.partner_grade_summaries, tft.customer_questions, tft.question_replies, tft.change_proposals,
    tft.drafts, tft.draft_versions, tft.draft_sentences, tft.draft_sentence_evidence,
    tft.review_versions, tft.exports, tft.rereview_marks, tft.rfp_commands, tft.rfp_audit_events
    FROM PUBLIC, cs_app, platform_app;
GRANT SELECT, INSERT ON
    tft.project_assignments, tft.documents, tft.document_versions, tft.clauses,
    tft.clause_deleted_values, tft.clause_flags, tft.requirements, tft.requirement_versions,
    tft.requirement_conditions, tft.product_comparisons, tft.comparison_evidence,
    tft.supplement_options, tft.supplement_selections, tft.partner_profiles, tft.partner_grades,
    tft.partner_grade_summaries, tft.customer_questions, tft.question_replies, tft.change_proposals,
    tft.drafts, tft.draft_versions, tft.draft_sentences, tft.draft_sentence_evidence,
    tft.review_versions, tft.exports, tft.rereview_marks, tft.rfp_commands, tft.rfp_audit_events
    TO tft_app;
REVOKE UPDATE, DELETE, TRUNCATE ON
    tft.project_assignments, tft.documents, tft.document_versions, tft.clauses,
    tft.clause_deleted_values, tft.clause_flags, tft.requirements, tft.requirement_versions,
    tft.requirement_conditions, tft.product_comparisons, tft.comparison_evidence,
    tft.supplement_options, tft.supplement_selections, tft.partner_profiles, tft.partner_grades,
    tft.partner_grade_summaries, tft.customer_questions, tft.question_replies, tft.change_proposals,
    tft.drafts, tft.draft_versions, tft.draft_sentences, tft.draft_sentence_evidence,
    tft.review_versions, tft.exports, tft.rereview_marks, tft.rfp_commands, tft.rfp_audit_events
    FROM tft_app;
GRANT UPDATE (latest_version) ON tft.documents, tft.requirements, tft.drafts TO tft_app;
GRANT UPDATE (status, version, updated_at) ON tft.customer_questions TO tft_app;
GRANT UPDATE (status, decided_by, decided_at, decision_note, version) ON tft.change_proposals TO tft_app;
GRANT UPDATE (storage_key, sha256, size, completed_at) ON tft.exports TO tft_app;
GRANT UPDATE (cleared_by, cleared_at) ON tft.rereview_marks TO tft_app;
GRANT UPDATE (output_status, judgement_status, customer_goal, review_departments, review_version)
    ON tft.projects TO tft_app;
COMMIT;
