-- KAN-187: T20. T14/T19 뒤. 사람 입력만 보관하며 재실행 가능하다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
-- API StringConstraints(strip_whitespace=True,min_length=1,max_length=200)와 같은 문자 경계.
-- Pydantic/Rust의 Unicode White_Space 집합이며 단어 내부 공백은 바꾸지 않는다.
CREATE OR REPLACE FUNCTION tft.project_constraint_text_valid(value text)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog, tft
AS $fn$
    SELECT value IS NOT NULL AND char_length(btrim(value,
        U&'\0009\000A\000B\000C\000D\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000')) BETWEEN 1 AND 200;
$fn$;
CREATE OR REPLACE FUNCTION tft.project_manufacturer_list_valid(entries text[])
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog, tft
AS $fn$
    SELECT entries IS NULL OR (
        cardinality(entries) BETWEEN 1 AND 100 AND array_ndims(entries)=1
        AND NOT EXISTS (SELECT 1 FROM unnest(entries) AS vals(item)
            WHERE NOT tft.project_constraint_text_valid(item))
    );
$fn$;
REVOKE ALL ON FUNCTION tft.project_constraint_text_valid(text), tft.project_manufacturer_list_valid(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tft.project_constraint_text_valid(text), tft.project_manufacturer_list_valid(text[]) TO tft_app,tft_worker;
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS supply_region text;
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS customer_manufacturer_list text[];
-- 이전 T20의 약한 동명 CHECK도 재적용으로 교체한다. 값을 고치거나 추측하지 않는다.
ALTER TABLE tft.projects DROP CONSTRAINT IF EXISTS projects_supply_region_check;
ALTER TABLE tft.projects ADD CONSTRAINT projects_supply_region_check
    CHECK (supply_region IS NULL OR tft.project_constraint_text_valid(supply_region));
ALTER TABLE tft.projects DROP CONSTRAINT IF EXISTS projects_customer_manufacturer_list_check;
ALTER TABLE tft.projects ADD CONSTRAINT projects_customer_manufacturer_list_check
    CHECK (tft.project_manufacturer_list_valid(customer_manufacturer_list));
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS off_list_permission text NOT NULL DEFAULT 'UNSTATED'
    CHECK (off_list_permission IN ('ALLOWED','FORBIDDEN','UNSTATED'));
-- 새 추출값만 그대로 저장한다. 과거 NULL은 미보존이며 false나 추정값으로 채우지 않는다.
ALTER TABLE tft.requirement_versions ADD COLUMN IF NOT EXISTS kind text
    CHECK (kind IS NULL OR kind IN ('SPEC','TASK','DOCUMENT','COMPLIANCE','DESIGNATED_PRODUCT'));
ALTER TABLE tft.requirement_versions ADD COLUMN IF NOT EXISTS certification_listed boolean;
ALTER TABLE tft.requirement_versions ADD COLUMN IF NOT EXISTS canonical_value text;
-- 기존 프로젝트 열 권한은 보존한다. 목록·정책을 모델이 만들거나 worker가 변경하지 않는다.
GRANT SELECT(supply_region,customer_manufacturer_list,off_list_permission) ON tft.projects TO tft_worker;
COMMIT;
