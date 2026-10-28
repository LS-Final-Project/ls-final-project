"""실제 AEAD와 전용 PostgreSQL 검사. 합성 캐시만 사용하고 Graph는 호출하지 않는다."""

import asyncio
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from secrets import token_bytes
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from pydantic import SecretStr

from backend.api.platform.auth.callback import MicrosoftSubject
from backend.api.platform.auth.mailbox_vault import (
    MailboxCipher,
    StoredMailbox,
    create_mailbox,
    disconnect_mailbox,
    load_mailbox,
    reconnect_mailbox,
    save_mailbox_update,
)
from backend.api.platform.auth.microsoft import (
    PERSONAL_TENANT,
    MailboxAuthorization,
    MailboxTokenUpdate,
)
from backend.api.platform.auth.sessions import issue_session
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


def authorization(owner, subject=None, marker="synthetic-refresh-token"):
    """검증 결과 형식의 합성 단일 계정 캐시를 생성한다.

    Args:
        owner: 합성 서비스 사용자 ID.
        subject: 합성 사서함 식별자. 없으면 새 식별자를 생성한다.
        marker: 암호문·로그 비노출을 확인할 합성 비밀 값.

    Returns:
        MailboxAuthorization: 실제 Microsoft 토큰이 없는 보관 경계 검사 입력.
    """
    subject = subject or MicrosoftSubject(PERSONAL_TENANT, uuid4())
    cache = {
        "Account": {
            "synthetic-account": {
                "environment": "login.microsoftonline.com",
                "realm": "consumers",
                "local_account_id": str(subject.oid),
                "home_account_id": str(subject.oid),
            }
        },
        "RefreshToken": {"synthetic": {"secret": marker}},
    }
    return MailboxAuthorization(owner, subject, SecretStr(json.dumps(cache)))


@pytest.fixture
def cipher():
    """외부에 저장하지 않는 임의의 테스트 전용 키를 준비한다.

    Args:
        없음.

    Returns:
        MailboxCipher: 실제 AES-256-GCM 암호기.
    """
    return MailboxCipher(
        key=SecretStr(base64.b64encode(token_bytes(32)).decode()), key_version="test-v1"
    )


@pytest.fixture
def snapshot():
    """실제 계정 정보가 없는 보관 전용 메모리 스냅샷을 준비한다.

    Args:
        없음.

    Returns:
        StoredMailbox: 합성 계정·캐시·개정.
    """
    value = authorization(uuid4())
    return StoredMailbox(uuid4(), value.owner_user_id, value.subject, 1, value.serialized_cache)


def test_aead_roundtrip_uses_distinct_nonces_and_hides_secrets(cipher, snapshot):
    """같은 캐시도 다른 nonce로 저장하고 메타데이터 결속 후에만 복호화한다.

    Args:
        cipher: 실제 암호기.
        snapshot: 합성 연결 메타데이터와 캐시.

    Returns:
        None: 암호화·복호화·nonce·repr 비노출을 검증한다.
    """
    first, second = cipher.encrypt(snapshot), cipher.encrypt(snapshot)
    assert first != second and first[:12] != second[:12]
    assert b"synthetic-refresh-token" not in first
    assert (
        cipher.decrypt(snapshot, ciphertext=first, key_version="test-v1")
        == snapshot.serialized_cache
    )
    assert "synthetic-refresh-token" not in repr(snapshot) + repr(cipher)


@pytest.mark.parametrize(
    "field", ["mailbox", "owner", "subject", "revision", "version", "tamper", "key"]
)
def test_aead_rejects_wrong_context_or_tampered_ciphertext(cipher, snapshot, field):
    """암호문 복사·메타데이터 교체·다른 키는 안전한 제공 실패가 된다.

    Args:
        cipher: 정상 암호기.
        snapshot: 원래 암호문의 연결 문맥.
        field: 교체할 문맥 또는 암호문 조건.

    Returns:
        None: 실제 인증 태그 검사와 오류 비노출을 검증한다.
    """
    ciphertext = cipher.encrypt(snapshot)
    version = "test-v1"
    if field == "mailbox":
        snapshot = replace(snapshot, mailbox_id=uuid4())
    elif field == "owner":
        snapshot = replace(snapshot, owner_user_id=uuid4())
    elif field == "subject":
        snapshot = replace(snapshot, subject=MicrosoftSubject(PERSONAL_TENANT, uuid4()))
    elif field == "revision":
        snapshot = replace(snapshot, revision=2)
    elif field == "version":
        version = "different-key-version"
    elif field == "tamper":
        ciphertext = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    else:
        cipher = MailboxCipher(
            key=SecretStr(base64.b64encode(token_bytes(32)).decode()), key_version="test-v1"
        )
    with pytest.raises(ApplicationError, match="MAILBOX_TOKEN_UNAVAILABLE") as error:
        cipher.decrypt(snapshot, ciphertext=ciphertext, key_version=version)
    assert error.value.status_code == 503
    assert "synthetic-refresh-token" not in repr(error.value) + error.value.message


@pytest.mark.parametrize("key", ["synthetic-invalid-key", "", base64.b64encode(b"short").decode()])
def test_invalid_key_configuration_fails_without_secret_details(key):
    """누락·형식·길이가 잘못된 키를 폴백 없이 거부한다.

    Args:
        key: 실제 비밀이 아닌 잘못된 합성 키 설정.

    Returns:
        None: 안전한 설정 실패를 검증한다.
    """
    with pytest.raises(ApplicationError, match="MAILBOX_TOKEN_UNAVAILABLE") as error:
        MailboxCipher(key=SecretStr(key), key_version="test-v1")
    if key:
        assert key not in error.value.message


def test_cipher_rejects_cache_for_another_verified_subject(cipher, snapshot):
    """다른 사서함 캐시를 문맥만 바꿔 암호화하지 않는다.

    Args:
        cipher: 실제 암호기.
        snapshot: 원래 합성 캐시와 식별자.

    Returns:
        None: 저장 전 계정 결속 거부를 검증한다.
    """
    wrong = replace(snapshot, subject=MicrosoftSubject(PERSONAL_TENANT, uuid4()))
    with pytest.raises(ApplicationError, match="MAILBOX_ACCESS_DENIED"):
        cipher.encrypt(wrong)


@pytest.fixture
def context(mailbox_database, registered_user, settings):
    """합성 소유자·현재 세션과 이번 연결 행만 정리할 문맥을 준비한다.

    Args:
        mailbox_database: 전용 보관 검사 DB.
        registered_user: 기존 합성 활성 등록 사용자.
        settings: platform_app 권한의 DB 설정.

    Returns:
        SimpleNamespace: 소유자·세션·합성 인가 결과와 테스트 DB.
    """
    issued = asyncio.run(issue_session(settings, registered_user[0]))
    owner = registered_user[1]
    try:
        yield SimpleNamespace(
            owner=owner,
            token=issued.token.get_secret_value(),
            dsn=mailbox_database,
            authorization=authorization(owner),
            settings=settings,
        )
    finally:
        with psycopg.connect(mailbox_database) as conn:
            conn.execute("DELETE FROM mailbox_auth.connections WHERE owner_user_id=%s", (owner,))


async def create(context, cipher):
    """정상 합성 인가 결과를 실제 플랫폼 런타임 권한으로 저장한다.

    Args:
        context: 합성 세션과 DB 문맥.
        cipher: 실제 암호기.

    Returns:
        StoredMailbox: 새 연결과 보관 전용 캐시.
    """
    return await create_mailbox(
        context.settings,
        session_token=context.token,
        authorization=context.authorization,
        cipher=cipher,
    )


async def load(context, value, cipher):
    """서버에서 확인한 소유자로 현재 캐시를 복호화한다.

    Args:
        context: 합성 사용자와 DB 설정.
        value: 연결 ID를 가진 내부 스냅샷.
        cipher: 실제 암호기.

    Returns:
        StoredMailbox: 현재 DB 개정과 복호화 캐시.
    """
    return await load_mailbox(
        context.settings, mailbox_id=value.mailbox_id, owner_user_id=context.owner, cipher=cipher
    )


def update(value, marker):
    """같은 사서함의 합성 캐시 회전을 준비한다.

    Args:
        value: 기존 보관 스냅샷.
        marker: 회전 후 합성 refresh token 표식.

    Returns:
        MailboxTokenUpdate: 공급자가 반환한 형태의 합성 회전 결과.
    """
    auth = authorization(value.owner_user_id, value.subject, marker)
    return MailboxTokenUpdate(SecretStr("synthetic-access"), auth.serialized_cache, True)


def test_database_stores_only_ciphertext_and_runtime_roundtrip(context, cipher, caplog):
    """현재 사용자만 복호화하며 DB와 로그에는 평문 키·토큰을 남기지 않는다.

    Args:
        context: 전용 DB·현재 세션.
        cipher: 실제 암호기.
        caplog: 애플리케이션과 드라이버 DEBUG 기록 수집기.

    Returns:
        None: 실제 저장 바이트·복호화·현재 개정·비노출을 검증한다.
    """
    caplog.set_level("DEBUG")

    async def scenario():
        """저장 후 현재 스냅샷을 읽는다.

        Args:
            없음.

        Returns:
            None: 왕복 캐시와 저장 암호문을 검증한다.
        """
        value = await create(context, cipher)
        loaded = await load(context, value, cipher)
        assert loaded.serialized_cache == context.authorization.serialized_cache
        assert loaded.revision == 1 and loaded.owner_user_id == context.owner
        with psycopg.connect(context.dsn) as conn:
            row = conn.execute(
                "SELECT cache_ciphertext,key_version FROM mailbox_auth.connections WHERE id=%s",
                (value.mailbox_id,),
            ).fetchone()
        assert b"synthetic-refresh-token" not in bytes(row[0])
        assert row[1] == "test-v1"
        assert "synthetic-refresh-token" not in repr(loaded) + caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["disabled", "no_roles", "invalid_dept", "logout"])
def test_create_and_update_recheck_current_rights(context, cipher, change):
    """인가 인계 이후 권한·세션이 바뀌면 저장과 갱신을 거부한다.

    Args:
        context: 전용 DB의 정상 인가 문맥.
        cipher: 실제 암호기.
        change: 저장 직전 사용자·역할·세션 변화.

    Returns:
        None: 현재 조건 재검사와 기존 캐시 불변을 검증한다.
    """

    async def scenario():
        """정상 저장 후 사용자 조건을 바꿔 후속 저장을 거부시킨다.

        Args:
            없음.

        Returns:
            None: 거부와 기존 개정 보존을 검증한다.
        """
        value = await create(context, cipher)
        statements = {
            "disabled": "UPDATE identity.users SET is_active=false WHERE id=%s",
            "no_roles": "DELETE FROM identity.user_roles WHERE user_id=%s",
            "invalid_dept": "UPDATE identity.user_roles SET role='dept_staff' WHERE user_id=%s",
            "logout": "DELETE FROM identity.sessions WHERE user_id=%s",
        }
        with psycopg.connect(context.dsn) as conn:
            if change == "invalid_dept":
                conn.execute("UPDATE identity.users SET dept=NULL WHERE id=%s", (context.owner,))
            conn.execute(statements[change], (context.owner,))
        with pytest.raises(ApplicationError, match="MAILBOX_ACCESS_DENIED"):
            await create(context, cipher)
        if change != "logout":
            with pytest.raises(ApplicationError, match="MAILBOX_ACCESS_DENIED"):
                await save_mailbox_update(
                    context.settings, snapshot=value, update=update(value, "rotated"), cipher=cipher
                )
        # 작업용 조회/갱신은 자체 인증한 서버 작업의 소유자로 호출하며 세션 철회와 별개다.
        with psycopg.connect(context.dsn) as conn:
            assert (
                conn.execute(
                    "SELECT revision FROM mailbox_auth.connections WHERE id=%s", (value.mailbox_id,)
                ).fetchone()[0]
                == 1
            )

    asyncio.run(scenario())


def test_another_manager_and_unknown_connection_are_denied_before_decrypt(
    context, cipher, monkeypatch
):
    """manager도 타인의 연결을 알거나 추측한 것만으로 복호화할 수 없다.

    Args:
        context: 정상 소유자와 전용 DB.
        cipher: 음성 요청에서 호출되면 안 되는 암호기.
        monkeypatch: 복호화 진입을 탐지할 검사 도구.

    Returns:
        None: 동일한 안전한 거부와 복호화 미실행을 검증한다.
    """
    manager = uuid4()
    with psycopg.connect(context.dsn) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name) VALUES (%s,%s,%s,%s,%s)",
            (manager, uuid4(), uuid4(), "manager@example.invalid", "합성 관리자"),
        )
        conn.execute("INSERT INTO identity.user_roles VALUES (%s,'manager')", (manager,))

    async def scenario():
        """정상 연결을 만든 뒤 다른 소유자로 두 접근을 시도한다.

        Args:
            없음.

        Returns:
            None: 존재 여부를 드러내지 않는 거부를 검증한다.
        """
        value = await create(context, cipher)

        def forbidden(*args, **kwargs):
            """부적절한 요청의 복호화 진입을 실패로 표시한다.

            Args:
                *args: 사용하지 않는 인자.
                **kwargs: 사용하지 않는 키워드 인자.

            Returns:
                None: 호출되면 검사 실패를 발생시킨다.
            """
            pytest.fail("권한 확인 전 복호화 진입")

        monkeypatch.setattr(cipher, "decrypt", forbidden)
        errors = []
        for mailbox_id in (value.mailbox_id, uuid4()):
            with pytest.raises(ApplicationError) as error:
                await load_mailbox(
                    context.settings, mailbox_id=mailbox_id, owner_user_id=manager, cipher=cipher
                )
            errors.append((error.value.code, error.value.status_code, error.value.message))
        assert errors[0] == errors[1]
        assert errors[0][0] == "MAILBOX_ACCESS_DENIED"

    try:
        asyncio.run(scenario())
    finally:
        with psycopg.connect(context.dsn) as conn:
            conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (manager,))
            conn.execute("DELETE FROM identity.users WHERE id=%s", (manager,))


def test_concurrent_cache_rotation_has_one_winner(context, cipher):
    """같은 개정에서 시작한 두 갱신 중 하나만 저장되고 다른 갱신은 충돌한다.

    Args:
        context: 실제 DB와 정상 소유자 문맥.
        cipher: 실제 암호기.

    Returns:
        None: 실제 행 잠금·현재 개정 비교·암호화된 승자 캐시를 검증한다.
    """

    async def scenario():
        """동시 갱신을 요청하고 저장한 결과를 다시 복호화한다.

        Args:
            없음.

        Returns:
            None: 단일 승자와 안전한 충돌을 검증한다.
        """
        value = await create(context, cipher)
        candidates = [update(value, "rotation-one"), update(value, "rotation-two")]
        results = await asyncio.gather(
            *(
                save_mailbox_update(context.settings, snapshot=value, update=item, cipher=cipher)
                for item in candidates
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(r, StoredMailbox) for r in results) == 1
        failures = [r for r in results if isinstance(r, ApplicationError)]
        assert len(failures) == 1 and failures[0].code == "MAILBOX_VERSION_CONFLICT"
        loaded = await load(context, value, cipher)
        assert loaded.revision == 2
        assert loaded.serialized_cache in [item.serialized_cache for item in candidates]

    asyncio.run(scenario())


def test_disconnect_and_reconnect_cannot_be_overwritten_by_old_refresh(context, cipher):
    """해제는 암호문을 지우며 새 인가 후에도 이전 스냅샷 갱신을 거부한다.

    Args:
        context: 실제 DB와 현재 세션.
        cipher: 실제 암호기.

    Returns:
        None: 해제·재연결·오래된 갱신 차단과 새 캐시 결속을 검증한다.
    """

    async def scenario():
        """저장·해제·새 사서함 인가·이전 갱신 재시도를 실행한다.

        Args:
            없음.

        Returns:
            None: 상태와 개정이 이전 캐시로 복구되지 않는지 검증한다.
        """
        value = await create(context, cipher)
        await disconnect_mailbox(
            context.settings, mailbox_id=value.mailbox_id, session_token=context.token
        )
        await disconnect_mailbox(
            context.settings, mailbox_id=value.mailbox_id, session_token=context.token
        )
        with psycopg.connect(context.dsn) as conn:
            assert conn.execute(
                "SELECT revision,state,cache_ciphertext FROM mailbox_auth.connections WHERE id=%s",
                (value.mailbox_id,),
            ).fetchone() == (2, "disconnected", None)
        with pytest.raises(ApplicationError, match="MAILBOX_NOT_CONNECTED"):
            await load(context, value, cipher)
        with pytest.raises(ApplicationError, match="MAILBOX_NOT_CONNECTED"):
            await save_mailbox_update(
                context.settings, snapshot=value, update=update(value, "stale"), cipher=cipher
            )
        renewed = authorization(context.owner, value.subject, marker="new-consent-refresh")
        current = await reconnect_mailbox(
            context.settings,
            mailbox_id=value.mailbox_id,
            expected_revision=2,
            session_token=context.token,
            authorization=renewed,
            cipher=cipher,
        )
        assert current.revision == 3 and current.subject == renewed.subject
        with pytest.raises(ApplicationError, match="MAILBOX_VERSION_CONFLICT"):
            await save_mailbox_update(
                context.settings, snapshot=value, update=update(value, "stale"), cipher=cipher
            )
        assert (await load(context, current, cipher)).serialized_cache == renewed.serialized_cache

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["user", "role", "new_role", "session"])
def test_storage_transaction_prevents_rights_change_before_commit(
    context, cipher, monkeypatch, target
):
    """저장 트랜잭션 도중 비활성화·역할 변경·로그아웃이 완료되지 못하게 한다.

    Args:
        context: 전용 DB와 정상 세션.
        cipher: 실제 암호기.
        monkeypatch: 암호화 후 커밋 직전 시점을 제어할 도구.
        target: 변경을 시도할 현재 권한 행.

    Returns:
        None: 실제 저장 중 다른 DB 연결의 쓰기 잠금 충돌을 검증한다.
    """
    entered, release = Event(), Event()
    original = cipher.encrypt

    def paused(value):
        """실제 암호화 후 테스트가 행 잠금을 확인할 때까지 기다린다.

        Args:
            value: 현재 연결과 합성 캐시.

        Returns:
            bytes: 실제 AES-GCM 암호문.
        """
        encrypted = original(value)
        entered.set()
        assert release.wait(5), "검사 대기 시간 초과"
        return encrypted

    monkeypatch.setattr(cipher, "encrypt", paused)
    statements = {
        "user": "UPDATE identity.users SET is_active=false WHERE id=%s",
        "role": "UPDATE identity.user_roles SET role='sales' WHERE user_id=%s",
        "new_role": "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'sales')",
        "session": "DELETE FROM identity.sessions WHERE user_id=%s",
    }
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, create(context, cipher))
        try:
            assert entered.wait(3)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with psycopg.connect(context.dsn) as conn:
                    conn.execute("SET LOCAL lock_timeout='150ms'")
                    conn.execute(statements[target], (context.owner,))
        finally:
            release.set()
        assert future.result(timeout=3).revision == 1


def test_vault_schema_runtime_and_service_permissions(context):
    """플랫폼의 최소 DML과 타 서비스·런타임 DDL 거부를 실제 DB로 확인한다.

    Args:
        context: 실제 platform_app 설정과 관리자 검사 연결.

    Returns:
        None: 읽기 전용 운영 점검·서비스 격리·권한 누락 탐지를 검증한다.
    """
    sql = Path("infra/sql/004_check_mailbox_vault.sql").read_text()
    sql = "\n".join(line for line in sql.splitlines() if not line.startswith("\\"))
    with psycopg.connect(context.settings.database_url.get_secret_value()) as conn:
        conn.execute(sql)
    for role in ("cs_app", "tft_app", "platform_app"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(context.dsn) as conn:
                conn.execute("SET LOCAL ROLE " + role)
                statement = (
                    "CREATE TABLE mailbox_auth.forbidden(id int)"
                    if role == "platform_app"
                    else "SELECT * FROM mailbox_auth.connections"
                )
                conn.execute(statement)
    with psycopg.connect(context.dsn) as conn:
        conn.execute("SET LOCAL ROLE platform_migrator")
        conn.execute("REVOKE UPDATE ON mailbox_auth.connections FROM platform_app")
    try:
        with pytest.raises(
            psycopg.errors.RaiseException, match="required mailbox runtime privilege"
        ):
            with psycopg.connect(context.settings.database_url.get_secret_value()) as conn:
                conn.execute(sql)
    finally:
        with psycopg.connect(context.dsn) as conn:
            conn.execute("SET LOCAL ROLE platform_migrator")
            conn.execute("GRANT UPDATE ON mailbox_auth.connections TO platform_app")


def test_unchanged_cache_does_not_rotate_revision_or_ciphertext(context, cipher):
    """캐시 적중은 현재 개정을 검사하되 불필요한 저장 경합을 만들지 않는다.

    Args:
        context: 전용 DB와 정상 세션.
        cipher: 실제 암호기.

    Returns:
        None: 변경 없는 결과의 개정·암호문 보존을 검증한다.
    """

    async def scenario():
        """정상 저장 후 변경 없는 MSAL 결과를 반영한다.

        Args:
            없음.

        Returns:
            None: 이전 암호문과 개정이 유지되는지 검증한다.
        """
        value = await create(context, cipher)
        with psycopg.connect(context.dsn) as conn:
            before = conn.execute(
                "SELECT cache_ciphertext FROM mailbox_auth.connections WHERE id=%s",
                (value.mailbox_id,),
            ).fetchone()[0]
        result = await save_mailbox_update(
            context.settings,
            snapshot=value,
            update=MailboxTokenUpdate(SecretStr("synthetic-access"), value.serialized_cache, False),
            cipher=cipher,
        )
        assert result.revision == 1
        with psycopg.connect(context.dsn) as conn:
            assert conn.execute(
                "SELECT revision,cache_ciphertext FROM mailbox_auth.connections WHERE id=%s",
                (value.mailbox_id,),
            ).fetchone() == (1, before)

    asyncio.run(scenario())


def test_concurrent_refresh_and_disconnect_ends_disconnected(context, cipher):
    """진행 중인 갱신과 해제가 경합해도 최종 해제 상태를 복구하지 않는다.

    Args:
        context: 전용 DB와 현재 소유자 세션.
        cipher: 실제 암호기.

    Returns:
        None: 두 실행 순서에서 암호문 제거와 이후 조회 거부를 검증한다.
    """

    async def scenario():
        """동시에 갱신과 해제를 실행한 뒤 현재 연결을 확인한다.

        Args:
            없음.

        Returns:
            None: 실패를 숨기지 않고 해제의 최종 상태를 검증한다.
        """
        value = await create(context, cipher)
        results = await asyncio.gather(
            save_mailbox_update(
                context.settings,
                snapshot=value,
                update=update(value, "race-refresh"),
                cipher=cipher,
            ),
            disconnect_mailbox(
                context.settings, mailbox_id=value.mailbox_id, session_token=context.token
            ),
            return_exceptions=True,
        )
        assert results[1] is None
        assert isinstance(results[0], StoredMailbox) or (
            isinstance(results[0], ApplicationError) and results[0].code == "MAILBOX_NOT_CONNECTED"
        )
        with pytest.raises(ApplicationError, match="MAILBOX_NOT_CONNECTED"):
            await load(context, value, cipher)
        with psycopg.connect(context.dsn) as conn:
            assert conn.execute(
                "SELECT state,cache_ciphertext FROM mailbox_auth.connections WHERE id=%s",
                (value.mailbox_id,),
            ).fetchone() == ("disconnected", None)

    asyncio.run(scenario())


def test_expiry_after_initial_session_check_cannot_commit_cache(context, cipher, monkeypatch):
    """초기 검사 이후 만료돼도 저장 SQL이 DB 시각으로 세션을 다시 확인한다.

    Args:
        context: 전용 DB와 현재 세션.
        cipher: 실제 암호기.
        monkeypatch: 시각 대기 없이 세션 만료 시점을 합성할 검사 도구.

    Returns:
        None: 만료 세션의 저장 차단·전체 트랜잭션 롤백을 검증한다.
    """
    from backend.api.platform.auth import mailbox_vault

    original = mailbox_vault._lock_owner

    async def expire_after_check(conn, owner, **kwargs):
        """실제 현재 상태 확인 후 같은 검사 트랜잭션에서 만료를 합성한다.

        Args:
            conn: 현재 플랫폼 저장 트랜잭션.
            owner: 현재 소유자.
            **kwargs: 원문 세션 등 기존 검사 인자.

        Returns:
            None: 세션이 쓰기 직전 만료된 조건을 만든다.
        """
        await original(conn, owner, **kwargs)
        await conn.execute(
            "UPDATE identity.sessions SET created_at=clock_timestamp()-interval '1 hour',"
            "expires_at=clock_timestamp()-interval '1s' WHERE user_id=%s",
            (owner,),
        )

    monkeypatch.setattr(mailbox_vault, "_lock_owner", expire_after_check)
    with pytest.raises(ApplicationError, match="MAILBOX_ACCESS_DENIED"):
        asyncio.run(create(context, cipher))
    with psycopg.connect(context.dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM mailbox_auth.connections WHERE owner_user_id=%s",
                (context.owner,),
            ).fetchone()[0]
            == 0
        )


def test_database_failure_is_safe_token_unavailable(cipher):
    """연결 장애를 미연결·접근 거부나 빈 캐시로 숨기지 않는다.

    Args:
        cipher: 실제 암호기.

    Returns:
        None: 로컬 연결 거부의 고정503 변환과 원문 비노출을 검증한다.
    """
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(
            "postgresql://postgres:synthetic-dsn-secret@127.0.0.1:1/kan77_auth_test"
        ),
    )
    with pytest.raises(ApplicationError, match="MAILBOX_TOKEN_UNAVAILABLE") as error:
        asyncio.run(
            load_mailbox(settings, mailbox_id=uuid4(), owner_user_id=uuid4(), cipher=cipher)
        )
    assert error.value.status_code == 503
    assert "synthetic-dsn-secret" not in repr(error.value) + error.value.message


def test_reconnect_rejects_another_microsoft_account_without_storing(context, cipher):
    """기존 연결과 다른 Microsoft 계정의 인계는 저장 없이 거부하고 같은 계정만 재연결한다.

    Args:
        context: 실제 DB와 현재 세션.
        cipher: 실제 암호기.

    Returns:
        None: 거부 시 행·암호문 불변과 같은 계정 재연결 성공을 확인한다.
    """

    def stored(value):
        """관리자 연결로 보관 행의 계정·개정·상태·암호문·변경 시각을 읽는다.

        Args:
            value: 이번 합성 연결.

        Returns:
            tuple: 비교용 보관 행.
        """
        with psycopg.connect(context.dsn) as conn:
            return conn.execute(
                "SELECT subject_tid,subject_oid,revision,state,key_version,cache_ciphertext,"
                "updated_at FROM mailbox_auth.connections WHERE id=%s",
                (value.mailbox_id,),
            ).fetchone()

    async def scenario():
        """연결 중인 행에 다른 계정·같은 계정 재연결을 차례로 시도한다.

        Args:
            없음.

        Returns:
            None: 다른 계정 거부와 같은 계정 개정 증가를 검증한다.
        """
        value = await create(context, cipher)
        before = stored(value)
        with pytest.raises(ApplicationError, match="MAILBOX_ACCESS_DENIED") as error:
            await reconnect_mailbox(
                context.settings,
                mailbox_id=value.mailbox_id,
                expected_revision=1,
                session_token=context.token,
                authorization=authorization(context.owner, marker="other-account-refresh"),
                cipher=cipher,
            )
        assert error.value.status_code == 403
        assert stored(value) == before
        current = await reconnect_mailbox(
            context.settings,
            mailbox_id=value.mailbox_id,
            expected_revision=1,
            session_token=context.token,
            authorization=authorization(context.owner, value.subject, "same-account-refresh"),
            cipher=cipher,
        )
        assert (current.mailbox_id, current.revision) == (value.mailbox_id, 2)
        after = stored(value)
        assert after[:4] == (value.subject.tid, value.subject.oid, 2, "connected")
        assert after[5] != before[5]
        assert (await load(context, current, cipher)).serialized_cache == current.serialized_cache

    asyncio.run(scenario())
