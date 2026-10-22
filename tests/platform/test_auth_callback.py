"""합성 callback 회귀 검사. fake 어댑터는 실제 Microsoft 토큰을 검증하지 않는다."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

import pytest

from backend.api.platform.auth.callback import (
    MicrosoftSubject,
    OAuthPurpose,
    PendingAuthorization,
    complete_callback,
)
from backend.shared.errors import ApplicationError

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
OWNER = UUID("00000000-0000-0000-0000-000000000001")
OTHER_OWNER = UUID("00000000-0000-0000-0000-000000000002")
SUBJECT = MicrosoftSubject(
    tid=UUID("00000000-0000-0000-0000-000000000003"),
    oid=UUID("00000000-0000-0000-0000-000000000004"),
)
BINDING = "synthetic-browser-binding"
CALLBACK = [("code", "synthetic-code"), ("state", "synthetic-state")]


class FakeOAuthAdapter:
    """호출 내용을 기록하고 합성 식별 결과 또는 지정한 오류를 반환하는 어댑터."""

    def __init__(self, error=None):
        """fake 어댑터의 호출 기록과 발생시킬 오류를 초기화한다.

        Args:
            error: 토큰 교환 대신 발생시킬 예외. 기본값은 None이다.

        Returns:
            None: 인스턴스 초기화만 수행한다.
        """
        self.calls = []
        self.error = error

    def redeem(self, pending, callback):
        """호출을 기록하고 합성 식별자를 반환하거나 지정된 예외를 발생시킨다.

        Args:
            pending: 검증 경계에서 전달한 인가 시도 정보.
            callback: 검증 경계에서 전달한 state와 code.

        Returns:
            MicrosoftSubject: 테스트에서 고정한 합성 Microsoft 식별자.

        Raises:
            Exception: 초기화 시 지정한 예외가 있는 경우 해당 예외를 발생시킨다.
        """
        self.calls.append((pending, callback))
        if self.error:
            raise self.error
        return SUBJECT


def pending_authorization(**overrides):
    """테스트에서 사용할 합성 인가 시도 정보를 생성한다.

    Args:
        **overrides: 기본 합성 인가 시도에서 교체할 필드와 값.

    Returns:
        PendingAuthorization: 지정한 필드 변경을 적용한 합성 인가 시도 정보.
    """
    return replace(
        PendingAuthorization(
            purpose=OAuthPurpose.LOGIN,
            state="synthetic-state",
            browser_binding_hash=sha256(BINDING.encode()).hexdigest(),
            expires_at=NOW + timedelta(minutes=5),
            provider_flow={"nonce": "synthetic-nonce", "code_verifier": "synthetic-verifier"},
        ),
        **overrides,
    )


def finish(pending, parameters=CALLBACK, **overrides):
    """기본 합성 인자를 적용해 callback 검증 함수를 실행한다.

    Args:
        pending: 검증할 인가 시도 정보.
        parameters: 중복 키를 보존한 callback query 목록.
        **overrides: 기본 callback 실행 인자에서 교체할 항목과 값.

    Returns:
        MicrosoftSubject: callback 검증과 fake 어댑터를 통과한 합성 식별자.

    Raises:
        ApplicationError: callback 검증이나 fake 어댑터가 요청을 거부한 경우.
    """
    arguments = {
        "purpose": OAuthPurpose.LOGIN,
        "browser_binding": BINDING,
        "session_user_id": None,
        "now": NOW,
        "adapter": FakeOAuthAdapter(),
        **overrides,
    }
    return complete_callback(pending, parameters, **arguments)


def test_login_returns_only_provider_subject_and_passes_the_saved_flow():
    """브라우저의 역할·클레임을 제외하고 저장된 flow를 전달하는지 확인한다.

    Args:
        없음.

    Returns:
        None: 합성 식별 결과와 어댑터 호출 내용을 검증한다.
    """
    pending = pending_authorization()
    adapter = FakeOAuthAdapter()
    subject = finish(
        pending,
        CALLBACK + [("role", "manager"), ("tid", str(OWNER)), ("id_token", "untrusted-token")],
        adapter=adapter,
    )
    assert subject == SUBJECT
    assert vars(subject) == {"tid": SUBJECT.tid, "oid": SUBJECT.oid}
    assert adapter.calls == [(pending, dict(CALLBACK))]
    assert adapter.calls[0][0].provider_flow is pending.provider_flow


@pytest.mark.parametrize(
    "parameters",
    [
        [],
        [("code", "synthetic-code")],
        [("state", "synthetic-state")],
        [("code", ""), ("state", "synthetic-state")],
        [("code", "  "), ("state", "synthetic-state")],
        [("code", "synthetic-code"), ("state", "wrong-state")],
        [("code", "synthetic-code"), ("state", "한글-state")],
        CALLBACK + [("state", "synthetic-state")],
        CALLBACK + [("code", "other-code")],
        CALLBACK + [("error", "access_denied"), ("error_description", "private-provider-error")],
    ],
)
def test_invalid_callback_never_reaches_token_exchange(parameters):
    """잘못된 callback이 토큰 교환 전에 거부되는지 확인한다.

    Args:
        parameters: 누락·중복·state 불일치·공급자 오류를 포함한 합성 query 목록.

    Returns:
        None: 안전한 오류 반환과 어댑터 미호출을 검증한다.
    """
    adapter = FakeOAuthAdapter()
    with pytest.raises(ApplicationError) as error:
        finish(pending_authorization(), parameters, adapter=adapter)
    assert error.value.code == "AUTH_INVALID_CALLBACK"
    assert error.value.status_code == 400
    assert adapter.calls == []
    assert "private-provider-error" not in str(error.value)
    assert "private-provider-error" not in error.value.message


@pytest.mark.parametrize(
    ("pending_changes", "arguments"),
    [
        ({"expires_at": NOW}, {}),
        ({"expires_at": NOW - timedelta(seconds=1)}, {}),
        ({"expires_at": NOW.replace(tzinfo=None)}, {}),
        ({}, {"now": NOW.replace(tzinfo=None)}),
        ({"state": ""}, {}),
        ({}, {"browser_binding": ""}),
        ({}, {"browser_binding": "another-browser"}),
        ({"purpose": OAuthPurpose.MAILBOX, "owner_user_id": OWNER}, {}),
        ({"owner_user_id": OWNER}, {}),
        ({}, {"purpose": OAuthPurpose.MAILBOX, "session_user_id": OWNER}),
    ],
)
def test_expired_or_misbound_attempt_is_rejected_before_exchange(pending_changes, arguments):
    """만료되거나 결속이 맞지 않는 인가 시도가 사전에 거부되는지 확인한다.

    Args:
        pending_changes: 인가 시도 정보에 적용할 비정상 필드와 값.
        arguments: callback 실행 인자에 적용할 변경 항목과 값.

    Returns:
        None: 인가 시도 거부와 어댑터 미호출을 검증한다.
    """
    adapter = FakeOAuthAdapter()
    with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
        finish(pending_authorization(**pending_changes), adapter=adapter, **arguments)
    assert adapter.calls == []


@pytest.mark.parametrize("session_user_id", [None, OTHER_OWNER])
def test_mailbox_callback_requires_the_user_who_started_linking(session_user_id):
    """사서함 연결을 시작한 사용자와 다른 세션의 callback을 거부하는지 확인한다.

    Args:
        session_user_id: 세션 없음 또는 다른 사용자를 나타내는 합성 ID.

    Returns:
        None: 사용자 결속 검사와 어댑터 미호출을 검증한다.
    """
    adapter = FakeOAuthAdapter()
    with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
        finish(
            pending_authorization(purpose=OAuthPurpose.MAILBOX, owner_user_id=OWNER),
            purpose=OAuthPurpose.MAILBOX,
            session_user_id=session_user_id,
            adapter=adapter,
        )
    assert adapter.calls == []


def test_mailbox_callback_without_an_owner_is_rejected():
    """연결을 시작한 사용자 정보가 없는 사서함 callback을 거부하는지 확인한다.

    Args:
        없음.

    Returns:
        None: 소유자 없는 연결의 거부와 어댑터 미호출을 검증한다.
    """
    adapter = FakeOAuthAdapter()
    with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
        finish(
            pending_authorization(purpose=OAuthPurpose.MAILBOX),
            purpose=OAuthPurpose.MAILBOX,
            session_user_id=OWNER,
            adapter=adapter,
        )
    assert adapter.calls == []


def test_mailbox_callback_returns_subject_without_replacing_service_identity():
    """서비스 사용자에 결속한 사서함 연결이 별도의 Microsoft 식별자를 반환하는지 확인한다.

    Args:
        없음.

    Returns:
        None: 기존 서비스 사용자와 구분한 합성 사서함 식별 결과를 검증한다.
    """
    assert (
        finish(
            pending_authorization(purpose=OAuthPurpose.MAILBOX, owner_user_id=OWNER),
            purpose=OAuthPurpose.MAILBOX,
            session_user_id=OWNER,
        )
        == SUBJECT
    )


def test_provider_verification_failure_is_not_a_success_or_fallback():
    """공급자 검증 실패가 성공 또는 폴백 결과로 바뀌지 않는지 확인한다.

    Args:
        없음.

    Returns:
        None: 공급자 어댑터의 오류가 그대로 전파되는지 검증한다.
    """
    expected = ApplicationError(401, "AUTH_INVALID_TOKEN", "Authentication failed.")
    adapter = FakeOAuthAdapter(error=expected)
    with pytest.raises(ApplicationError) as error:
        finish(pending_authorization(), adapter=adapter)
    assert error.value is expected
    assert len(adapter.calls) == 1


def test_pending_authorization_repr_does_not_expose_protocol_secrets():
    """인가 시도의 문자열 표현에 프로토콜 비밀값이 노출되지 않는지 확인한다.

    Args:
        없음.

    Returns:
        None: state·브라우저 결속값·nonce·PKCE verifier의 비노출을 검증한다.
    """
    representation = repr(pending_authorization())
    for secret in ("synthetic-state", BINDING, "synthetic-nonce", "synthetic-verifier"):
        assert secret not in representation
