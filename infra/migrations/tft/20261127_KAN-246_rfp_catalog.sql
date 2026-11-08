-- KAN-246 제품 목록: 자사 제품 등록·수정과 협력사 프로필 등록.
-- 자사 제품 기본 자료는 배포 파일(backend/api/tft/rfp/own_spec_facts.json)이다. 화면에서 고친 제품·새 제품은
-- own_products에 판본으로 쌓고, 비교는 파일 위에 각 제품의 최신 판본을 덮어 쓴다(파일은 그대로 둔다).
-- 협력사는 기존 partner_profiles에 새 판본을 더한다. 과거 등급은 그때 판본을 가리키므로 바뀌지 않는다.
-- 누가 언제 고쳤는지는 catalog_edits에 둔다(rfp_audit_events는 프로젝트 범위라 쓰지 않는다).
-- 추가 전용(앱은 SELECT·INSERT만). 다시 적용해도 깨지지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

CREATE TABLE IF NOT EXISTS tft.own_products (
    product_code text NOT NULL CHECK (length(product_code) BETWEEN 1 AND 64),
    version integer NOT NULL CHECK (version > 0),
    family text NOT NULL,
    lifecycle_status text NOT NULL CHECK (lifecycle_status IN ('ACTIVE', 'MATURE', 'DISCONTINUED')),
    standards text[] NOT NULL DEFAULT '{}',
    certifications jsonb NOT NULL CHECK (jsonb_typeof(certifications) = 'array'),
    spec_facts jsonb NOT NULL CHECK (jsonb_typeof(spec_facts) = 'array'),
    known_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (product_code, version)
);

CREATE TABLE IF NOT EXISTS tft.catalog_edits (
    id uuid PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('own_product', 'partner')),
    code text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    actor_user_id uuid NOT NULL,
    at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, code, version)
);

-- 화면 등록 프로필(registered)은 사용자가 입력한 실제 자료다. 시드 프로필은 그대로 synthetic이다.
-- 20261029_KAN-182는 CREATE TABLE IF NOT EXISTS라 재적용해도 이 제약을 다시 좁히지 않는다.
ALTER TABLE tft.partner_profiles
    DROP CONSTRAINT IF EXISTS partner_profiles_profile_source_check,
    DROP CONSTRAINT IF EXISTS partner_profiles_data_origin_check,
    DROP CONSTRAINT IF EXISTS partner_profiles_origin_by_source;
ALTER TABLE tft.partner_profiles ADD CONSTRAINT partner_profiles_origin_by_source CHECK (
    (profile_source = 'synthetic' AND data_origin = 'synthetic')
    OR (profile_source = 'registered' AND data_origin = 'real')
);

REVOKE ALL ON tft.own_products, tft.catalog_edits FROM PUBLIC;
GRANT SELECT, INSERT ON tft.own_products, tft.catalog_edits TO tft_app;
REVOKE UPDATE, DELETE, TRUNCATE ON tft.own_products, tft.catalog_edits FROM tft_app;
-- worker는 비교할 때 자사 제품 판본을 읽는다.
GRANT SELECT ON tft.own_products TO tft_worker;
COMMIT;
