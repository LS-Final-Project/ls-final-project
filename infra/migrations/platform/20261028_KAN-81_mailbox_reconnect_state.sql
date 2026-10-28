-- KAN-81 재연결 필요 상태. 20261027_KAN-81_mailbox_vault.sql(과 KAN-71 수집 표) 뒤에 적용한다.
-- 운영자 postgres 연결에서만 적용한다. 공용 DB에는 이 변경으로 적용하지 않는다.
\set ON_ERROR_STOP on
BEGIN;
DO $guard$
BEGIN
    IF current_database() NOT IN ('team4','kan77_auth_test') OR current_user<>'postgres' THEN
        RAISE EXCEPTION 'Use the operator postgres connection in the expected database';
    END IF;
END
$guard$;
SET LOCAL ROLE platform_migrator;

-- 공급자가 갱신을 거부해 사용자가 제품에서 다시 연결해야 하는 연결. state는 그대로 두고
-- 연결 행·FK(수집 표)도 바꾸지 않는다. 사유는 공급자 오류 이름 그대로, 토큰·계정이 캐시에
-- 없으면 token_missing이다. 재연결·해제가 두 열을 비운다.
ALTER TABLE mailbox_auth.connections
    ADD COLUMN reconnect_required_at timestamptz,
    ADD COLUMN reconnect_reason text CHECK (reconnect_reason IN (
        'invalid_grant','interaction_required','login_required','consent_required','token_missing'
    )),
    ADD CONSTRAINT mailbox_connections_reconnect_pair
        CHECK ((reconnect_required_at IS NULL) = (reconnect_reason IS NULL)),
    ADD CONSTRAINT mailbox_connections_reconnect_connected
        CHECK (reconnect_required_at IS NULL OR state='connected');

-- 런타임은 연결 상태·캐시와 재연결 표시만 바꾼다. 소유자·Microsoft 식별자·ID는 바꾸지 못한다.
-- 행 삭제 권한(DELETE)은 이번 범위 밖이라 그대로 둔다(코드는 쓰지 않는다).
REVOKE UPDATE ON mailbox_auth.connections FROM platform_app;
GRANT UPDATE (
    revision, state, key_version, cache_ciphertext, updated_at,
    reconnect_required_at, reconnect_reason
) ON mailbox_auth.connections TO platform_app;
COMMIT;
