-- KAN-235: 고객 연락처 한 줄(주소 하나 또는 회사 도메인 하나)을 API 밖에서 넣는다. C33 뒤.
-- team4에서 postgres로 psql을 쓴다. 주소로 넣을 때는 domain을 빈 값으로, 도메인으로 넣을 때는 email을 빈 값으로 준다.
--
--   psql -X -d team4 -f infra/sql/register_customer_contact.sql \
--     -v customer_id=CUST-0021 -v email='<시연 고객 Outlook 주소>' -v domain= \
--     -v active=true -v data_origin=demo
--
-- 값은 LS 고객 연락처(기획서 22쪽 customer_contacts)가 오기 전까지 가정 자료다. 시연값은 시연용 Outlook 계정
-- 주소를 시나리오 고객 코드에 붙인 합성값이며 data_origin=demo로 둔다. 다시 실행하면 active·data_origin만 바뀐다.
-- 누구나 쓰는 메일 도메인(outlook.kr 등)은 도메인 행으로 넣지 않는다. 그 도메인의 모든 사람이 이 고객이 된다.
\set ON_ERROR_STOP on
BEGIN;

SELECT set_config('contact.customer_id', :'customer_id', true),
       set_config('contact.email', lower(btrim(:'email')), true),
       set_config('contact.domain', lower(btrim(:'domain')), true),
       set_config('contact.active', :'active', true),
       set_config('contact.data_origin', :'data_origin', true);

DO $check$
DECLARE
    v_domain text := nullif(current_setting('contact.domain'), '');
BEGIN
    IF (nullif(current_setting('contact.email'), '') IS NULL) = (v_domain IS NULL) THEN
        RAISE EXCEPTION 'Give exactly one of email or domain';
    END IF;
    IF v_domain IN ('outlook.kr', 'outlook.com', 'hotmail.com', 'live.com', 'gmail.com', 'naver.com',
                    'daum.net', 'hanmail.net', 'kakao.com', 'yahoo.com', 'icloud.com') THEN
        RAISE EXCEPTION 'Public mail domain cannot be a customer domain: %', v_domain;
    END IF;
END
$check$;

SET LOCAL ROLE cs_migrator;
-- 형식은 표 제약(C33)이 확인한다. 같은 고객·같은 주소(또는 도메인) 행이 있으면 그 행을 고친다.
UPDATE cs.customer_contacts
   SET active = current_setting('contact.active')::boolean,
       data_origin = current_setting('contact.data_origin'), updated_at = now()
 WHERE customer_external_id = current_setting('contact.customer_id')
   AND email IS NOT DISTINCT FROM nullif(current_setting('contact.email'), '')
   AND domain IS NOT DISTINCT FROM nullif(current_setting('contact.domain'), '');
INSERT INTO cs.customer_contacts (id, customer_external_id, email, domain, active, data_origin)
SELECT gen_random_uuid(), current_setting('contact.customer_id'),
       nullif(current_setting('contact.email'), ''), nullif(current_setting('contact.domain'), ''),
       current_setting('contact.active')::boolean, current_setting('contact.data_origin')
 WHERE NOT EXISTS (
    SELECT 1 FROM cs.customer_contacts
     WHERE customer_external_id = current_setting('contact.customer_id')
       AND email IS NOT DISTINCT FROM nullif(current_setting('contact.email'), '')
       AND domain IS NOT DISTINCT FROM nullif(current_setting('contact.domain'), ''));

COMMIT;
