-- KAN-187: T20. T14/T19 뒤. 사람 입력만 보관하며 재실행 가능하다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS supply_region text
    CHECK (supply_region IS NULL OR length(btrim(supply_region)) BETWEEN 1 AND 200);
ALTER TABLE tft.projects ADD COLUMN IF NOT EXISTS customer_manufacturer_list text[]
    CHECK (customer_manufacturer_list IS NULL OR (
        cardinality(customer_manufacturer_list) BETWEEN 1 AND 100
        AND array_position(customer_manufacturer_list, NULL) IS NULL
        AND array_to_string(customer_manufacturer_list, '') <> ''
    ));
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
