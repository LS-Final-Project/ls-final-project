-- KAN-235: 고객 연락처(기획서 22쪽 customer_contacts, 추가 제공 가정 데이터). C33, C1 뒤. 재실행 가능.
-- 받은 메일의 보낸 주소가 어느 고객의 것인지 알아 그 고객의 진행 중 문의를 회신 후보로 보인다
-- (contracts/cs/reply-candidates.md). 후보와 근거일 뿐 자동 연결 근거가 아니다.
-- 한 행은 주소 하나(email) 또는 회사 도메인 하나(domain)다. 이름·전화·직책은 받지 않는다.
-- LS가 제공하기 전까지는 가정 자료다(data_origin demo·synthetic). 넣는 것은 API 밖
-- infra/sql/register_customer_contact.sql로만 한다. C/S 실행 계정은 읽기만 한다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;
CREATE TABLE IF NOT EXISTS cs.customer_contacts (
    id uuid PRIMARY KEY,
    customer_external_id text NOT NULL CHECK (customer_external_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
    -- 소문자로 저장한다. 둘 중 하나만 있다.
    email text CHECK (email ~ '^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$' AND length(email) <= 320),
    domain text CHECK (domain ~ '^[a-z0-9-]+(\.[a-z0-9-]+)+$' AND length(domain) <= 253),
    active boolean NOT NULL DEFAULT true,
    data_origin text NOT NULL CHECK (data_origin IN ('synthetic', 'demo', 'real')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((email IS NULL) <> (domain IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS customer_contacts_email
    ON cs.customer_contacts (customer_external_id, email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS customer_contacts_domain
    ON cs.customer_contacts (customer_external_id, domain) WHERE domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS customer_contacts_lookup_email
    ON cs.customer_contacts (email) WHERE active AND email IS NOT NULL;
CREATE INDEX IF NOT EXISTS customer_contacts_lookup_domain
    ON cs.customer_contacts (domain) WHERE active AND domain IS NOT NULL;
REVOKE ALL ON cs.customer_contacts FROM PUBLIC;
GRANT SELECT ON cs.customer_contacts TO cs_app;
COMMIT;
