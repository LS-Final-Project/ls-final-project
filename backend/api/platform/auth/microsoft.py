"""개인 Outlook 계정용 MSAL 어댑터. 공개 API·DB 세션·토큰 보관은 연결하지 않는다.

기존 KAN-76에서 성공한 consumers authority와 서버 시크릿 교환을 사용한다.
동기 네트워크 호출을 수행하므로 비동기 라우터 연결 시 스레드에서 실행해야 한다.
MSAL이 반환한 디코딩 결과를 신뢰하지 않고 ID 토큰의 서명과 클레임을 검증한다.
"""

from collections.abc import Mapping
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
)
from backend.shared.errors import ApplicationError

PERSONAL_TENANT = UUID("9188040d-6c67-4c5b-b112-36a304b66dad")
AUTHORITY = "https://login.microsoftonline.com/consumers"
ISSUER = f"https://login.microsoftonline.com/{PERSONAL_TENANT}/v2.0"
JWKS_URI = f"{AUTHORITY}/discovery/v2.0/keys"


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

    def _application(self) -> Any:
        """토큰 캐시를 공유하지 않는 MSAL 서버 앱을 생성한다.

        Args:
            없음.

        Returns:
            ConfidentialClientApplication: 요청별 메모리 토큰 캐시를 사용하는 MSAL 앱.
        """
        return msal.ConfidentialClientApplication(
            self._client_id,
            authority=AUTHORITY,
            client_credential=self._client_secret.get_secret_value(),
            token_cache=msal.TokenCache(),
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
        scopes = ["Mail.Read", "Mail.Send"] if purpose is OAuthPurpose.MAILBOX else []
        try:
            flow = self._application().initiate_auth_code_flow(
                scopes=scopes, redirect_uri=self._redirect_uri, prompt="select_account"
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
            result = self._application().acquire_token_by_auth_code_flow(flow, dict(callback))
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
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise ValueError("Unsupported signing algorithm")
            key = self._keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=ISSUER,
                options={
                    "require": ["iss", "aud", "exp", "iat", "nonce", "tid", "oid"],
                    "strict_aud": True,
                },
            )
            # MSAL은 flow의 원본 nonce를 SHA-256으로 변환해 인가 요청에 넣는다.
            expected_nonce = sha256(str(flow["nonce"]).encode("ascii")).hexdigest()
            if (
                not isinstance(claims["nonce"], str)
                or not compare_digest(claims["nonce"].encode(), expected_nonce.encode())
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
