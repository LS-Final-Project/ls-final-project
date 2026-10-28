"""개인 Outlook 계정용 MSAL 어댑터. 토큰 영속 보관은 연결하지 않는다.

기존 KAN-76에서 성공한 consumers authority와 서버 시크릿 교환을 사용한다.
동기 네트워크 호출은 로그인 경계에서 스레드로 실행한다.
MSAL이 반환한 디코딩 결과를 신뢰하지 않고 ID 토큰의 서명과 클레임을 검증한다.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import compare_digest
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import jwt
import msal  # type: ignore[import-untyped]  # MSAL은 타입 정보를 배포하지 않는다.
from pydantic import SecretStr
from requests.exceptions import RequestException  # type: ignore[import-untyped]

from backend.api.platform.auth.callback import (
    MicrosoftSubject,
    OAuthPurpose,
    PendingAuthorization,
    reject_callback,
    validate_callback,
)
from backend.shared.errors import ApplicationError

PERSONAL_TENANT = UUID("9188040d-6c67-4c5b-b112-36a304b66dad")
AUTHORITY = "https://login.microsoftonline.com/consumers"
ISSUER = f"https://login.microsoftonline.com/{PERSONAL_TENANT}/v2.0"
JWKS_URI = f"{AUTHORITY}/discovery/v2.0/keys"
MAILBOX_SCOPES = ("Mail.Read", "Mail.Send")


class _MsalTokenLogFilter(logging.Filter):
    """MSAL의 토큰 처리 DEBUG 기록에서 공급자·캐시 원문을 제거한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        """토큰 갱신 실패와 캐시 이벤트만 고정 문장으로 남긴다.

        Args:
            record: MSAL application 또는 token_cache 로거의 기록.

        Returns:
            bool: 안전하게 변환한 기록을 계속 처리한다.
        """
        if isinstance(record.msg, str):
            if record.name == "msal.application" and record.msg.startswith("Refresh failed."):
                record.msg = "Microsoft token refresh failed."
                record.args = ()
            elif record.name == "msal.token_cache" and record.msg.startswith("event="):
                record.msg = "Microsoft token cache updated."
                record.args = ()
        return True


@dataclass(frozen=True, repr=False)
class MailboxTokenUpdate:
    """보관 경계에만 전달할 결과. 토큰과 캐시를 repr로 노출하지 않는다."""

    access_token: SecretStr
    serialized_cache: SecretStr
    cache_changed: bool


@dataclass(frozen=True, repr=False)
class MailboxAuthorization:
    """서비스 소유자와 별도 사서함 식별자에 결속한 보관 전용 메모리 결과.

    캐시는 아직 평문 메모리 상태다. 보관 계층은 소유자와 상태를 다시 확인하고
    무결성 보호 암호화를 적용해야 한다. HTTP 응답이나 소비자에게 전달하지 않는다.
    """

    owner_user_id: UUID
    subject: MicrosoftSubject
    serialized_cache: SecretStr


def _mailbox_error(code: str) -> ApplicationError:
    """사서함 인증·보관 오류를 민감 정보 없는 고정 형식으로 생성한다.

    Args:
        code: 호출 경계에서 분류한 내부 사서함 오류 코드.

    Returns:
        ApplicationError: 공급자 원문을 포함하지 않는 내부 오류.
    """
    status, message = {
        "MAILBOX_ACCESS_DENIED": (403, "Mailbox access is not allowed."),
        "MAILBOX_NOT_CONNECTED": (409, "Mailbox is not connected."),
        "MAILBOX_RECONNECT_REQUIRED": (409, "Mailbox reconnection is required."),
        "MAILBOX_TOKEN_UNAVAILABLE": (503, "Mailbox authentication is unavailable."),
        "MAILBOX_VERSION_CONFLICT": (409, "Mailbox connection has changed."),
    }[code]
    return ApplicationError(status, code, message)


def _mailbox_account(cache: Any, subject: MicrosoftSubject) -> dict[str, Any]:
    """검증된 사서함 식별자와 캐시의 단일 계정을 이메일 없이 대조한다.

    Args:
        cache: 한 연결에서만 사용하는 MSAL 메모리 캐시.
        subject: 연결 시 서명·클레임 검증을 통과한 Microsoft 식별자.

    Returns:
        dict[str, Any]: 정확히 일치하는 캐시 계정.
    """
    accounts = list(cache.search(msal.TokenCache.CredentialType.ACCOUNT))
    if not accounts:
        raise _mailbox_error("MAILBOX_RECONNECT_REQUIRED")
    if len(accounts) != 1 or not isinstance(accounts[0], dict):
        raise _mailbox_error("MAILBOX_ACCESS_DENIED")
    account = accounts[0]
    if (
        subject.tid != PERSONAL_TENANT
        or subject.oid.int == 0
        or account.get("environment") != "login.microsoftonline.com"
        or account.get("realm") not in {"consumers", str(PERSONAL_TENANT)}
        or account.get("local_account_id") != str(subject.oid)
        or not isinstance(account.get("home_account_id"), str)
        or not account["home_account_id"]
    ):
        raise _mailbox_error("MAILBOX_ACCESS_DENIED")
    return account


def _has_mailbox_scopes(value: object) -> bool:
    """Graph의 짧은 권한명과 고정 리소스 URI 표기를 확인한다.

    Args:
        value: 공급자 응답이나 MSAL 캐시에 저장된 scope 문자열.

    Returns:
        bool: 현재 사서함 연결 권한을 모두 포함하는지 여부.
    """
    return isinstance(value, str) and set(MAILBOX_SCOPES) <= {
        scope.removeprefix("https://graph.microsoft.com/") for scope in value.split()
    }


class PersonalMicrosoftOAuth:
    """로그인과 사서함 동의를 구분하는 개인 Microsoft 계정 전용 어댑터."""

    def __init__(self, client_id: UUID, client_secret: SecretStr, redirect_uri: str) -> None:
        """서버 앱 설정과 Microsoft 서명키 조회기를 준비한다.

        이 단계에서는 외부 요청을 보내지 않는다. redirect_uri는 Entra의 Web
        플랫폼에 등록해야 하며, 로컬 점검 외에는 HTTPS 주소를 사용한다.

        Args:
            client_id: 기존 Entra 앱의 클라이언트 ID.
            client_secret: 서버에서만 사용하는 유효한 클라이언트 시크릿.
            redirect_uri: Web 플랫폼에 등록한 서버 callback의 정확한 주소.

        Returns:
            None: 어댑터의 설정을 초기화한다.

        Raises:
            ValueError: 시크릿이 비어 있거나 callback 주소가 허용되지 않는 경우.
        """
        parsed = urlsplit(redirect_uri)
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not (
                parsed.scheme == "https"
                or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})
            )
            or not client_secret.get_secret_value().strip()
        ):
            raise ValueError("Microsoft 서버 앱의 시크릿과 Web callback 설정을 확인하세요.")
        self._client_id = str(client_id)
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._keys = jwt.PyJWKClient(JWKS_URI, timeout=10)
        for name in ("msal.application", "msal.token_cache"):
            msal_logger = logging.getLogger(name)
            if not any(isinstance(item, _MsalTokenLogFilter) for item in msal_logger.filters):
                msal_logger.addFilter(_MsalTokenLogFilter())

    def _application(self, *, token_cache: Any = None) -> Any:
        """요청 또는 사서함 연결 전용 캐시를 사용하는 MSAL 서버 앱을 생성한다.

        Args:
            token_cache: 사서함 호출 전용 캐시. 없으면 로그인용 새 메모리 캐시.

        Returns:
            ConfidentialClientApplication: 요청별 메모리 토큰 캐시를 사용하는 MSAL 앱.
        """
        return msal.ConfidentialClientApplication(
            self._client_id,
            authority=AUTHORITY,
            client_credential=self._client_secret.get_secret_value(),
            token_cache=token_cache if token_cache is not None else msal.TokenCache(),
            instance_discovery=False,
            enable_pii_log=False,
            timeout=10,
        )

    def begin(
        self,
        purpose: OAuthPurpose,
        *,
        browser_binding: str,
        now: datetime,
        owner_user_id: UUID | None = None,
    ) -> tuple[str, PendingAuthorization]:
        """개인 계정 로그인 URL과 서버에 보관할 MSAL flow를 생성한다.

        로그인에는 메일 권한을 추가하지 않고, 사서함 연결에만 Mail.Read·Mail.Send를
        요청한다. openid·profile·offline_access와 PKCE·nonce는 MSAL이 추가한다.
        반환한 인가 시도는 서버 저장 전용이며 브라우저에는 URL만 전달해야 한다.

        Args:
            purpose: 서비스 로그인 또는 사서함 연결 목적.
            browser_binding: 호출자가 생성한 예측 불가능한 브라우저 결속값.
            now: 시간대 정보가 있는 인가 시작 시각.
            owner_user_id: 사서함 연결을 시작하는 기존 서비스 사용자의 ID.

        Returns:
            tuple[str, PendingAuthorization]: 로그인 URL과 5분간 유효한 인가 시도 정보.

        Raises:
            ApplicationError: 인가 입력이 잘못됐거나 Microsoft 연결에 실패한 경우.
        """
        if (
            not browser_binding
            or now.tzinfo is None
            or purpose not in {OAuthPurpose.LOGIN, OAuthPurpose.MAILBOX}
            or (purpose is OAuthPurpose.MAILBOX) != (owner_user_id is not None)
        ):
            raise reject_callback()
        scopes = list(MAILBOX_SCOPES) if purpose is OAuthPurpose.MAILBOX else []
        try:
            flow = self._application().initiate_auth_code_flow(
                scopes=scopes,
                redirect_uri=self._redirect_uri,
                prompt="select_account",
                response_mode="form_post",
            )
        except (RequestException, ValueError):
            raise ApplicationError(
                503, "AUTH_PROVIDER_UNAVAILABLE", "Authentication is unavailable."
            ) from None
        if "error" in flow:
            raise ApplicationError(
                503, "AUTH_PROVIDER_UNAVAILABLE", "Authentication is unavailable."
            )
        return flow["auth_uri"], PendingAuthorization(
            purpose=purpose,
            state=flow["state"],
            browser_binding_hash=sha256(browser_binding.encode()).hexdigest(),
            expires_at=now + timedelta(minutes=5),
            provider_flow=flow,
            owner_user_id=owner_user_id,
        )

    def redeem(
        self, pending: PendingAuthorization, callback: Mapping[str, str]
    ) -> MicrosoftSubject:
        """저장된 flow로 code를 교환하고 개인 계정 ID 토큰을 검증한다.

        호출자는 먼저 complete_callback()으로 요청을 검사하고 인가 시도를
        원자적으로 한 번만 소비해야 한다. access/refresh token은 반환하거나
        영속 저장하지 않으며 이번 단계에서는 사서함 접근을 연결하지 않는다.

        Args:
            pending: MSAL이 생성한 원본 flow를 포함하는 서버 인가 시도 정보.
            callback: 사전 검증을 통과한 state와 code.

        Returns:
            MicrosoftSubject: 서명과 클레임 검증을 통과한 개인 계정의 tid·oid.

        Raises:
            ApplicationError: flow 불일치, 공급자 장애 또는 토큰 검증 실패 시 발생한다.
        """
        subject, _ = self._redeem(pending, callback)
        return subject

    def _redeem(
        self,
        pending: PendingAuthorization,
        callback: Mapping[str, str],
        *,
        token_cache: Any = None,
    ) -> tuple[MicrosoftSubject, dict[str, Any]]:
        """로그인과 사서함에서 동일한 code 교환·초기 ID 토큰 검증을 사용한다.

        Args:
            pending: 원본 flow를 가진 일회 소비된 서버 인가 시도.
            callback: 공통 결속 검증을 통과한 state와 code.
            token_cache: 이번 사서함 연결 전용 캐시. 로그인은 별도 메모리 캐시.

        Returns:
            tuple[MicrosoftSubject, dict[str, Any]]: 검증 식별자와 서버 메모리 전용 응답.
        """
        flow = dict(pending.provider_flow)
        if (
            flow.get("redirect_uri") != self._redirect_uri
            or flow.get("state") != pending.state
            or callback.get("state") != pending.state
            or not isinstance(flow.get("nonce"), str)
            or not flow["nonce"]
            or not isinstance(flow.get("code_verifier"), str)
            or not flow["code_verifier"]
        ):
            raise reject_callback()
        try:
            result = self._application(token_cache=token_cache).acquire_token_by_auth_code_flow(
                flow, dict(callback)
            )
        except RequestException:
            raise ApplicationError(
                503, "AUTH_PROVIDER_UNAVAILABLE", "Authentication is unavailable."
            ) from None
        # MSAL 1.39는 손상된 ID 토큰을 디코딩할 때 아래 파싱 예외도 발생시킨다.
        except (ValueError, RuntimeError, AttributeError, IndexError):
            raise ApplicationError(401, "AUTH_INVALID_TOKEN", "Authentication failed.") from None
        token = result.get("id_token")
        if "error" in result or not isinstance(token, str) or not token:
            raise ApplicationError(401, "AUTH_INVALID_TOKEN", "Authentication failed.")
        return self._validated_subject(token, nonce=str(flow["nonce"])), result

    def redeem_mailbox(
        self,
        pending: PendingAuthorization,
        parameters: Sequence[tuple[str, str]],
        *,
        browser_binding: str,
        session_user_id: UUID,
        now: datetime,
    ) -> MailboxAuthorization:
        """사서함 callback의 결속·서명·캐시를 확인해 보관 전용 결과를 만든다.

        호출자는 유효한 현재 세션을 확인하고 서버의 인가 시도를 원자적으로
        일회 소비해야 한다. 이 메서드는 소유자 DB 조회나 암호화 저장을 대신하지 않는다.

        Args:
            pending: 일회 소비한 사서함 연결 인가 시도.
            parameters: 중복 키를 보존한 callback 입력.
            browser_binding: 연결을 시작한 브라우저의 결속값.
            session_user_id: 신뢰할 세션 조회로 확인한 현재 서비스 사용자 ID.
            now: 시간대 정보가 있는 현재 시각.

        Returns:
            MailboxAuthorization: 서비스 소유자·검증된 사서함 식별자·격리된 캐시.
        """
        callback = validate_callback(
            pending,
            parameters,
            purpose=OAuthPurpose.MAILBOX,
            browser_binding=browser_binding,
            session_user_id=session_user_id,
            now=now,
        )
        cache = msal.SerializableTokenCache()
        subject, result = self._redeem(pending, callback, token_cache=cache)
        account = _mailbox_account(cache, subject)
        self._mailbox_access_token(cache=cache, account=account, result=result)
        refresh_token = result.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise _mailbox_error("MAILBOX_RECONNECT_REQUIRED")
        credentials = cache.search(
            msal.TokenCache.CredentialType.REFRESH_TOKEN,
            query={
                "home_account_id": account["home_account_id"],
                "environment": account["environment"],
                "client_id": self._client_id,
            },
        )
        if not any(entry.get("secret") == refresh_token for entry in credentials):
            raise _mailbox_error("MAILBOX_ACCESS_DENIED")
        return MailboxAuthorization(
            owner_user_id=session_user_id,
            subject=subject,
            serialized_cache=SecretStr(cache.serialize()),
        )

    def _mailbox_access_token(
        self, *, cache: Any, account: dict[str, Any], result: dict[str, Any]
    ) -> SecretStr:
        """초기 교환과 갱신의 opaque 토큰을 동일한 캐시·앱·권한으로 검증한다.

        Args:
            cache: 현재 연결 전용 MSAL 캐시.
            account: 검증된 단일 사서함 계정.
            result: 공급자가 반환했거나 MSAL 캐시에 적중한 토큰 응답.

        Returns:
            SecretStr: 현재 계정·앱·권한과 대조한 유효 access token.
        """
        token = result.get("access_token")
        token_type = result.get("token_type")
        expires_in = result.get("expires_in")
        scope = result.get("scope")
        try:
            valid_expiry = (
                not isinstance(expires_in, bool)
                and isinstance(expires_in, (int, str))
                and int(expires_in) > 0
            )
        except ValueError:
            valid_expiry = False
        if (
            not isinstance(token, str)
            or not token.strip()
            or not isinstance(token_type, str)
            or token_type.lower() != "bearer"
            or not valid_expiry
            or ("scope" in result and not isinstance(scope, str))
        ):
            raise _mailbox_error("MAILBOX_TOKEN_UNAVAILABLE")
        if "scope" in result and not _has_mailbox_scopes(scope):
            raise _mailbox_error("MAILBOX_RECONNECT_REQUIRED")
        credentials = cache.search(
            msal.TokenCache.CredentialType.ACCESS_TOKEN,
            query={
                "home_account_id": account["home_account_id"],
                "environment": account["environment"],
                "client_id": self._client_id,
                "realm": "consumers",
            },
        )
        if not any(
            entry.get("secret") == token and _has_mailbox_scopes(entry.get("target"))
            for entry in credentials
        ):
            raise _mailbox_error("MAILBOX_ACCESS_DENIED")
        return SecretStr(token)

    def _validated_subject(self, token: str, *, nonce: str | None) -> MicrosoftSubject:
        """고정 Microsoft 서명·클레임 검증을 로그인과 사서함 갱신에서 재사용한다.

        Args:
            token: 외부로 출력하지 않을 ID 토큰.
            nonce: 인가 시도의 원본 nonce. 새 nonce를 요청하지 않은 갱신은 None.

        Returns:
            MicrosoftSubject: 서명과 개인 계정 클레임을 검증한 식별자.
        """
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise ValueError("Unsupported signing algorithm")
            key = self._keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                leeway=60,
                audience=self._client_id,
                issuer=ISSUER,
                options={
                    "require": ["iss", "aud", "exp", "iat", "tid", "oid"]
                    + (["nonce"] if nonce is not None else []),
                    "strict_aud": True,
                },
            )
            # MSAL은 flow의 원본 nonce를 SHA-256으로 변환해 인가 요청에 넣는다.
            expected_nonce = (
                sha256(nonce.encode("ascii")).hexdigest() if nonce is not None else None
            )
            if (
                (
                    expected_nonce is not None
                    and (
                        not isinstance(claims["nonce"], str)
                        or not compare_digest(claims["nonce"].encode(), expected_nonce.encode())
                    )
                )
                or claims["tid"] != str(PERSONAL_TENANT)
                or not isinstance(claims["oid"], str)
            ):
                raise ValueError("Invalid Microsoft subject")
            oid = UUID(claims["oid"])
            if oid.int == 0:
                raise ValueError("Empty Microsoft subject")
        except jwt.PyJWKClientConnectionError:
            raise ApplicationError(
                503, "AUTH_PROVIDER_UNAVAILABLE", "Authentication is unavailable."
            ) from None
        except (jwt.PyJWTError, ValueError, TypeError):
            raise ApplicationError(401, "AUTH_INVALID_TOKEN", "Authentication failed.") from None
        return MicrosoftSubject(tid=PERSONAL_TENANT, oid=oid)

    def acquire_mailbox_token(
        self, *, subject: MicrosoftSubject, serialized_cache: SecretStr, force_refresh: bool = False
    ) -> MailboxTokenUpdate:
        """연결 전용 캐시에서 유효 토큰을 얻고 필요하면 MSAL로 갱신한다.

        호출자는 DB에서 현재 연결 소유자·활성을 먼저 검사하고 무결성이 보호된
        캐시를 복호화해야 한다. 이 공급자 어댑터가 그 검사를 대신하지 않는다.
        동기 네트워크 경계이므로 비동기 서비스에서는 스레드로 실행한다. 결과의
        캐시는 보관 계층 전용이며 메일 소비자에게 반환하거나 자동 저장하지 않는다.

        Args:
            subject: 연결 시 검증해 서버에 저장한 사서함의 Microsoft 식별자.
            serialized_cache: 해당 연결에서만 사용하는 복호화된 MSAL 캐시.
            force_refresh: True면 캐시의 access token을 쓰지 않고 refresh token으로 새로 받는다.

        Returns:
            MailboxTokenUpdate: 유효 access token과 보관 경계에 전달할 새 캐시 상태.

        Raises:
            ApplicationError: 계정 불일치, 재연결 필요 또는 토큰 제공 실패.
        """
        if subject.tid != PERSONAL_TENANT or subject.oid.int == 0:
            raise _mailbox_error("MAILBOX_ACCESS_DENIED")
        cache = msal.SerializableTokenCache()
        try:
            cache.deserialize(serialized_cache.get_secret_value())
            account = _mailbox_account(cache, subject)
            identity_fields = ("home_account_id", "local_account_id", "environment")
            expected_account = tuple(account.get(field) for field in identity_fields)
            result = self._application(token_cache=cache).acquire_token_silent_with_error(
                list(MAILBOX_SCOPES), account=account, force_refresh=force_refresh
            )
            if result is None:
                raise _mailbox_error("MAILBOX_RECONNECT_REQUIRED")
            if not isinstance(result, dict):
                raise _mailbox_error("MAILBOX_TOKEN_UNAVAILABLE")
            if "error" in result:
                code = (
                    "MAILBOX_RECONNECT_REQUIRED"
                    if result["error"]
                    in {
                        "invalid_grant",
                        "interaction_required",
                        "login_required",
                        "consent_required",
                    }
                    else "MAILBOX_TOKEN_UNAVAILABLE"
                )
                raise _mailbox_error(code)
            token = self._mailbox_access_token(cache=cache, account=account, result=result)
            if "id_token" in result:
                id_token = result["id_token"]
                if not isinstance(id_token, str) or not id_token:
                    raise _mailbox_error("MAILBOX_TOKEN_UNAVAILABLE")
                try:
                    refreshed_subject = self._validated_subject(id_token, nonce=None)
                except ApplicationError as error:
                    code = (
                        "MAILBOX_TOKEN_UNAVAILABLE"
                        if error.status_code == 503
                        else "MAILBOX_ACCESS_DENIED"
                    )
                    raise _mailbox_error(code) from None
                if refreshed_subject != subject:
                    raise _mailbox_error("MAILBOX_ACCESS_DENIED")
            refreshed_account = _mailbox_account(cache, subject)
            if tuple(refreshed_account.get(field) for field in identity_fields) != expected_account:
                raise _mailbox_error("MAILBOX_ACCESS_DENIED")
            changed = cache.has_state_changed
            return MailboxTokenUpdate(
                access_token=token,
                serialized_cache=SecretStr(cache.serialize()),
                cache_changed=changed,
            )
        except (
            RequestException,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            RuntimeError,
            IndexError,
        ):
            raise _mailbox_error("MAILBOX_TOKEN_UNAVAILABLE") from None
