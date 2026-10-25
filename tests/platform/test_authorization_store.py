"""전용 PostgreSQL에서 저장·동시 소비·결속·실패 후 재사용을 검증한다."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.sql import SQL, Identifier

from backend.api.platform.auth.attempts import complete_stored_callback, save_authorization
from backend.api.platform.auth.callback import MicrosoftSubject, OAuthPurpose, PendingAuthorization
from backend.api.platform.auth.login import complete_login
from backend.shared.errors import ApplicationError


def new_pending(**overrides):
    """서로 충돌하지 않는 합성 인가 시도를 생성한다.

    Args:
        **overrides: 목적·소유자·만료 등 교체할 필드.

    Returns:
        PendingAuthorization: 실제 토큰이 없는 5분 유효 인가 시도.
    """
    return replace(
        PendingAuthorization(
            purpose=OAuthPurpose.LOGIN,
            state=str(uuid4()),
            browser_binding_hash=sha256(b"test-browser").hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            provider_flow={"nonce": "test-nonce", "code_verifier": "test-verifier"},
        ),
        **overrides,
    )


class RecordingAdapter:
    """Microsoft를 호출하지 않고 교환 횟수와 실패를 기록한다."""

    def __init__(self, error=None, subject=None):
        """합성 교환기를 초기화한다.

        Args:
            error: 지정하면 교환 중 발생시킬 ApplicationError.
            subject: 반환할 합성 식별자. 없으면 기존 고정 식별자를 사용한다.

        Returns:
            None: 호출 기록을 초기화한다.
        """
        self.calls = []
        self.error = error
        self.subject = subject or MicrosoftSubject(UUID(int=1), UUID(int=2))

    def redeem(self, pending, callback):
        """교환 요청을 기록하고 합성 식별자를 반환한다.

        Args:
            pending: DB에서 꺼낸 인가 시도.
            callback: 검증한 프로토콜 입력.

        Returns:
            MicrosoftSubject: 고정된 테스트 식별자.
        """
        self.calls.append((pending, callback))
        if self.error:
            raise self.error
        return self.subject


async def finish(settings, pending, adapter, **overrides):
    """저장된 시도의 콜백을 비동기로 처리한다.

    Args:
        settings: 테스트 DB 연결 설정.
        pending: 저장된 인가 시도.
        adapter: 호출을 기록할 합성 교환기.
        **overrides: 잘못된 결속 등을 검증할 인자 변경.

    Returns:
        MicrosoftSubject: 검증을 통과한 합성 Microsoft 식별자.
    """
    arguments = {
        "purpose": pending.purpose,
        "browser_binding": "test-browser",
        "session_user_id": pending.owner_user_id,
        "now": datetime.now(UTC),
        **overrides,
    }
    return await complete_stored_callback(
        settings, [("state", pending.state), ("code", "test-code")], adapter=adapter, **arguments
    )


def test_concurrent_callbacks_exchange_once_and_replay_is_rejected(settings):
    """동일 콜백 8개 중 하나만 교환하며 이후 재사용도 거부하는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 테스트 DB 설정.

    Returns:
        None: 실제 동시 DELETE와 커밋 이후 재사용 거부를 검증한다.
    """

    async def scenario():
        """동시 소비와 후속 재사용을 실행한다.

        Args:
            없음.

        Returns:
            None: 성공·실패 개수와 교환 횟수를 검증한다.
        """
        pending, adapter = new_pending(), RecordingAdapter()
        await save_authorization(settings, pending)
        results = await asyncio.gather(
            *(finish(settings, pending, adapter) for _ in range(8)), return_exceptions=True
        )
        assert sum(isinstance(result, MicrosoftSubject) for result in results) == 1
        failures = [result for result in results if isinstance(result, ApplicationError)]
        assert len(failures) == 7
        assert all(error.code == "AUTH_INVALID_CALLBACK" for error in failures)
        assert len(adapter.calls) == 1
        assert adapter.calls[0][0].provider_flow == pending.provider_flow
        with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
            await finish(settings, pending, adapter)
        assert len(adapter.calls) == 1

    asyncio.run(scenario())


def test_provider_failure_does_not_restore_consumed_authorization(settings):
    """공급자 장애 후에도 소비가 롤백되지 않아 재교환이 차단되는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 테스트 DB 설정.

    Returns:
        None: 실패 보존과 교환의 일회성을 검증한다.
    """

    async def scenario():
        """소비 후 공급자 장애와 재시도를 실행한다.

        Args:
            없음.

        Returns:
            None: 두 번째 교환이 실행되지 않음을 검증한다.
        """
        pending = new_pending()
        adapter = RecordingAdapter(
            ApplicationError(503, "AUTH_PROVIDER_UNAVAILABLE", "Unavailable")
        )
        await save_authorization(settings, pending)
        with pytest.raises(ApplicationError, match="AUTH_PROVIDER_UNAVAILABLE"):
            await finish(settings, pending, adapter)
        with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
            await finish(settings, pending, adapter)
        assert len(adapter.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", ["browser", "purpose", "owner", "missing_owner"])
def test_wrong_binding_does_not_consume_mailbox_attempt(settings, auth_database, mismatch):
    """다른 브라우저·목적·사용자의 요청이 정상 시도를 소비하지 못하는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 테스트 DB 설정.
        auth_database: 등록 사용자 준비용 관리자 연결.
        mismatch: 거부할 결속 불일치 유형.

    Returns:
        None: 잘못된 요청 거부 후 올바른 연결이 성공함을 검증한다.
    """
    owner = uuid4()
    with psycopg.connect(auth_database) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name) VALUES (%s,%s,%s,%s,%s)",
            (owner, uuid4(), uuid4(), "synthetic@example.invalid", "합성 사용자"),
        )

    async def scenario():
        """잘못된 결속으로 시도한 후 올바른 콜백을 처리한다.

        Args:
            없음.

        Returns:
            None: 정상 시도의 보존과 단일 교환을 검증한다.
        """
        pending = new_pending(purpose=OAuthPurpose.MAILBOX, owner_user_id=owner)
        adapter = RecordingAdapter()
        changes = {
            "browser": {"browser_binding": "other-browser"},
            "purpose": {"purpose": OAuthPurpose.LOGIN},
            "owner": {"session_user_id": uuid4()},
            "missing_owner": {"session_user_id": None},
        }
        await save_authorization(settings, pending)
        with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
            await finish(settings, pending, adapter, **changes[mismatch])
        assert not adapter.calls
        assert isinstance(await finish(settings, pending, adapter), MicrosoftSubject)

    try:
        asyncio.run(scenario())
    finally:
        with psycopg.connect(auth_database) as conn:
            conn.execute(
                "DELETE FROM identity.oauth_authorizations WHERE owner_user_id=%s", (owner,)
            )
            conn.execute("DELETE FROM identity.users WHERE id=%s", (owner,))


def test_expired_attempt_never_reaches_exchange(settings):
    """DB 시각으로 만료된 시도가 과거 클라이언트 시각으로도 복구되지 않는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 테스트 DB 설정.

    Returns:
        None: 만료 시도의 교환 차단을 검증한다.
    """

    async def scenario():
        """만료 시도를 저장하고 과거 시각을 전달한다.

        Args:
            없음.

        Returns:
            None: 공급자 미호출을 검증한다.
        """
        pending = new_pending(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        adapter = RecordingAdapter()
        await save_authorization(settings, pending)
        with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
            await finish(settings, pending, adapter, now=datetime.now(UTC) - timedelta(minutes=2))
        assert not adapter.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["state", "duplicate", "provider_error"])
def test_invalid_callback_preserves_the_valid_attempt(settings, invalid):
    """잘못된 state·중복 키·공급자 오류가 정상 시도를 소비하지 않는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 테스트 DB 설정.
        invalid: 거부할 콜백 입력 유형.

    Returns:
        None: 잘못된 요청 이후에도 정상 콜백이 한 번만 교환됨을 검증한다.
    """

    async def scenario():
        """비정상 콜백과 정상 콜백을 차례로 처리한다.

        Args:
            없음.

        Returns:
            None: 오류 응답과 정상 시도 보존을 검증한다.
        """
        pending, adapter = new_pending(), RecordingAdapter()
        await save_authorization(settings, pending)
        parameters = [("state", pending.state), ("code", "test-code")]
        if invalid == "state":
            parameters[0] = ("state", "wrong-state")
        elif invalid == "duplicate":
            parameters.append(("state", pending.state))
        else:
            parameters.append(("error", "access_denied"))
        with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
            await complete_stored_callback(
                settings,
                parameters,
                purpose=OAuthPurpose.LOGIN,
                browser_binding="test-browser",
                session_user_id=None,
                now=datetime.now(UTC),
                adapter=adapter,
            )
        assert not adapter.calls
        assert isinstance(await finish(settings, pending, adapter), MicrosoftSubject)
        assert len(adapter.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["cs_app", "tft_app"])
def test_business_services_cannot_read_protocol_secrets(auth_database, role):
    """업무 서비스 역할에 MSAL flow 조회 권한이 없는지 확인한다.

    Args:
        auth_database: 전용 DB 관리자 연결.
        role: 접근을 거부할 업무 서비스 역할.

    Returns:
        None: PostgreSQL이 실제 SELECT를 거부함을 검증한다.
    """
    with psycopg.connect(auth_database) as conn:
        conn.execute(SQL("SET ROLE {}").format(Identifier(role)))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT provider_flow FROM identity.oauth_authorizations")


@pytest.mark.parametrize(
    "registration",
    ["allowed", "unregistered", "wrong_tid", "wrong_oid", "inactive", "no_roles", "missing_dept"],
)
def test_login_matches_registered_subject_without_creating_users_or_sessions(
    settings, auth_database, registration
):
    """실제 DB에서 tid+oid 매칭·등록 조건·실패 후 재사용 차단을 검증한다.

    Args:
        settings: 플랫폼 런타임 권한의 테스트 DB 설정.
        auth_database: 합성 등록 사용자 준비용 관리자 연결.
        registration: 정상 등록 또는 거부할 등록 상태.

    Returns:
        None: DB 역할만 사용하며 사용자·역할·세션을 생성하지 않음을 검증한다.
    """
    user_id, tid, oid = uuid4(), uuid4(), uuid4()
    roles = [] if registration == "no_roles" else ["manager", "sales"]
    if registration == "missing_dept":
        roles = ["dept_staff"]
    if registration != "unregistered":
        with psycopg.connect(auth_database) as conn:
            conn.execute(
                "INSERT INTO identity.users(id,tid,oid,email,display_name,is_active) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    user_id,
                    tid,
                    oid,
                    "registered@example.invalid",
                    "합성 등록 사용자",
                    registration != "inactive",
                ),
            )
            for role in roles:
                conn.execute(
                    "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,%s)", (user_id, role)
                )
    subject = MicrosoftSubject(
        uuid4() if registration == "wrong_tid" else tid,
        uuid4() if registration == "wrong_oid" else oid,
    )
    adapter = RecordingAdapter(subject=subject)

    async def scenario():
        """인가 시도를 소비하고 등록 확인 성공·실패 및 재사용 거부를 확인한다.

        Args:
            없음.

        Returns:
            None: 허용 조건과 소비된 시도의 일회성을 검증한다.
        """
        pending = new_pending()
        await save_authorization(settings, pending)
        parameters = [
            ("state", pending.state),
            ("code", "test-code"),
            ("email", "registered@example.invalid"),
            ("roles", "cs_agent"),
            ("user_id", str(user_id)),
        ]
        arguments = {
            "browser_binding": "test-browser",
            "now": datetime.now(UTC),
            "adapter": adapter,
        }
        if registration == "allowed":
            result = await complete_login(settings, parameters, **arguments)
            assert (result.id, result.tid, result.oid) == (user_id, tid, oid)
            assert result.roles == ["manager", "sales"]
        else:
            with pytest.raises(HTTPException) as rejected:
                await complete_login(settings, parameters, **arguments)
            assert rejected.value.status_code == 401
        with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
            await complete_login(settings, parameters, **arguments)
        assert len(adapter.calls) == 1
        assert adapter.calls[0][1] == {"state": pending.state, "code": "test-code"}

    try:
        asyncio.run(scenario())
        with psycopg.connect(auth_database) as conn:
            assert conn.execute(
                "SELECT count(*) FROM identity.users WHERE id=%s OR (tid=%s AND oid=%s)",
                (user_id, subject.tid, subject.oid),
            ).fetchone() == (int(registration != "unregistered"),)
            assert conn.execute(
                "SELECT role FROM identity.user_roles WHERE user_id=%s ORDER BY role", (user_id,)
            ).fetchall() == [(role,) for role in roles if registration != "unregistered"]
            assert conn.execute(
                "SELECT count(*) FROM identity.dev_sessions WHERE user_id=%s", (user_id,)
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(auth_database) as conn:
            conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.users WHERE id=%s", (user_id,))
