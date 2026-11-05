"""운영 인증 경로에 등록하지 않은 callback 검증 경계 초안.

호출자는 서버에 저장한 인가 시도를 원자적으로 한 번만 소비해야 한다.
공급자 어댑터는 code 교환과 OIDC 토큰 검증을 담당한다.
브라우저가 전달한 클레임·역할·토큰으로 서비스 사용자 정보를 만들지 않는다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
from secrets import compare_digest
from typing import Protocol
from uuid import UUID

from backend.shared.errors import ApplicationError


class OAuthPurpose(Enum):
    """서비스 로그인과 사서함 연결을 구분하는 인가 목적."""

    LOGIN = "login"
    MAILBOX = "mailbox"


@dataclass(frozen=True)
class MicrosoftSubject:
    """공급자 어댑터가 검증한 Microsoft 테넌트 및 사용자 식별자."""

    tid: UUID
    oid: UUID


@dataclass(frozen=True, repr=False)
class PendingAuthorization:
    """브라우저 세션 쿠키로 직렬화하지 않는 서버 전용 인가 시도 정보.

    provider_flow의 MSAL nonce와 PKCE verifier는 서버에만 보관한다.
    사서함 연결은 Microsoft 사서함 식별자와 별개로 기존 서비스 사용자에 결속한다.
    로그인 과정에서 역할을 부여하거나 미등록 사용자를 생성하지 않는다.
    """

    purpose: OAuthPurpose
    state: str
    browser_binding_hash: str
    expires_at: datetime
    provider_flow: Mapping[str, object]
    owner_user_id: UUID | None = None


class OAuthAdapter(Protocol):
    """인가 code 교환과 Microsoft 식별 결과 검증을 담당하는 어댑터 계약."""

    def redeem(
        self, pending: PendingAuthorization, callback: Mapping[str, str]
    ) -> MicrosoftSubject:
        """저장된 flow로 code를 교환하고 검증한 Microsoft 식별자를 반환한다.

        구현체는 nonce·서명·issuer·audience·유효기간·tid/oid를 검증해야 한다.
        공급자 오류는 원시 토큰 정보를 포함하지 않는 ApplicationError로 변환한다.

        Args:
            pending: 서버에 보관한 MSAL flow를 포함하는 인가 시도 정보.
            callback: 사전 검증을 통과한 state와 code.

        Returns:
            MicrosoftSubject: 토큰 검증을 통과한 테넌트 및 사용자 식별자.

        Raises:
            ApplicationError: code 교환 또는 토큰 검증에 실패한 경우.
        """
        ...


def reject_callback() -> ApplicationError:
    """잘못된 callback을 거부할 때 사용할 고정 오류를 생성한다.

    Args:
        없음.

    Returns:
        ApplicationError: 공급자의 상세 오류를 포함하지 않는 HTTP 400 오류.
    """
    return ApplicationError(400, "AUTH_INVALID_CALLBACK", "The authorization callback is invalid.")


def validate_callback(
    pending: PendingAuthorization,
    parameters: Sequence[tuple[str, str]],
    *,
    purpose: OAuthPurpose,
    browser_binding: str,
    session_user_id: UUID | None,
    now: datetime,
) -> dict[str, str]:
    """callback 형식과 인가 시도의 결속을 검증해 프로토콜 입력만 반환한다.

    parameters는 QueryParams.multi_items()처럼 중복 query 키를 보존해야 한다.
    호출자는 서버의 인가 시도를 원자적으로 한 번만 소비해야 하며,
    이 함수는 인가 시도를 저장하거나 사용자 세션을 생성하지 않는다.

    Args:
        pending: 서버에 보관한 인가 시도 정보.
        parameters: 중복 키를 보존한 callback query의 키와 값 목록.
        purpose: 해당 callback 경로가 처리할 인가 목적.
        browser_binding: 요청 브라우저에서 전달받은 인가 시도 결속값.
        session_user_id: 현재 서비스 세션의 사용자 ID. 사서함 연결 시 필수다.
        now: 인가 시도의 만료 여부를 판단할 시간대 정보가 있는 현재 시각.

    Returns:
        dict[str, str]: 검증한 state와 code. 토큰 교환은 실행하지 않는다.

    Raises:
        ApplicationError: callback 형식·결속·만료 검증에 실패한 경우.
    """
    if (
        pending.purpose != purpose
        or pending.expires_at.tzinfo is None
        or now.tzinfo is None
        or pending.expires_at <= now
        or not pending.state
        or not browser_binding
        or not compare_digest(
            pending.browser_binding_hash.encode(),
            sha256(browser_binding.encode()).hexdigest().encode(),
        )
    ):
        raise reject_callback()
    if purpose is OAuthPurpose.MAILBOX:
        if pending.owner_user_id is None or pending.owner_user_id != session_user_id:
            raise reject_callback()
    elif pending.owner_user_id is not None:
        raise reject_callback()

    callback = dict(parameters)
    if len(callback) != len(parameters):
        raise reject_callback()
    if (
        "error" in callback
        or not callback.get("code", "").strip()
        or not compare_digest(callback.get("state", "").encode(), pending.state.encode())
    ):
        raise reject_callback()
    # 브라우저의 클레임이나 역할 대신 검증된 프로토콜 입력만 어댑터에 전달한다.
    return {key: callback[key] for key in ("state", "code")}


def complete_callback(
    pending: PendingAuthorization,
    parameters: Sequence[tuple[str, str]],
    *,
    purpose: OAuthPurpose,
    browser_binding: str,
    session_user_id: UUID | None,
    now: datetime,
    adapter: OAuthAdapter,
) -> MicrosoftSubject:
    """공통 callback 검증을 통과한 입력만 기존 식별자 어댑터에 전달한다.

    Args:
        pending: 서버에 보관하고 원자적으로 일회 소비한 인가 시도.
        parameters: 중복 키를 보존한 callback 입력.
        purpose: callback 경로의 인가 목적.
        browser_binding: 시작 브라우저의 결속값.
        session_user_id: 현재 서비스 사용자. 사서함 연결 시 시작 사용자와 같아야 한다.
        now: 시간대 정보가 있는 현재 시각.
        adapter: code 교환과 Microsoft 식별자 검증 어댑터.

    Returns:
        MicrosoftSubject: 검증된 Microsoft 식별자. 토큰 캐시는 반환하지 않는다.
    """
    callback = validate_callback(
        pending,
        parameters,
        purpose=purpose,
        browser_binding=browser_binding,
        session_user_id=session_user_id,
        now=now,
    )
    return adapter.redeem(pending, callback)
