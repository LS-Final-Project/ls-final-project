"""Cloud Run IAM 호출과 별개로 컨테이너에서 플랫폼 실행 계정을 확인한다."""

import time

import jwt
import requests
from fastapi import HTTPException
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError
from requests.exceptions import RequestException

CALLER_HEADER = "X-Team4-Caller-Token"
GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"
METADATA_IDENTITY = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)
# KAN-252: ID 토큰(1시간)을 audience별로 만료 5분 전까지 다시 쓴다. 요청마다 메타데이터를 부르면 막 뜬
# 인스턴스에 요청이 몰릴 때 기본 스레드 풀(CPU 1개면 5개)이 이 호출로 차서 중계·내부 접수가 20초씩 밀렸다.
REFRESH_BEFORE_SECONDS = 300
_TOKENS: dict[str, tuple[str, float]] = {}


class PlatformCaller:
    """고정 Google 서명키·수신 서비스·플랫폼 실행 계정만 신뢰하는 검증기."""

    def __init__(self, audience: str, email: str) -> None:
        """수신 서비스의 audience와 허용 실행 계정을 준비한다.

        Args:
            audience: 수신 Cloud Run 서비스의 고정 URL.
            email: 플랫폼 실행 서비스 계정 이메일.

        Returns:
            None: 네트워크 호출 없이 Google 서명키 조회기를 초기화한다.
        """
        self.audience = audience
        self.email = email
        self.keys = jwt.PyJWKClient(GOOGLE_JWKS, timeout=5)

    def verify(self, token: str) -> None:
        """서명·발급자·대상·시간·검증된 실행 계정이 모두 맞는 호출만 허용한다.

        Args:
            token: 플랫폼이 별도 내부 헤더에 넣은 원본 Google ID 토큰.

        Returns:
            None: 신뢰할 수 있는 플랫폼 호출일 때만 반환한다.

        Raises:
            HTTPException: 토큰 불일치는 401, Google 서명키 조회 장애는 503.
        """
        try:
            if len(token) > 16384 or jwt.get_unverified_header(token).get("alg") != "RS256":
                raise HTTPException(401)
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=["https://accounts.google.com", "accounts.google.com"],
                options={"require": ["iss", "aud", "exp", "iat", "sub", "email", "email_verified"]},
            )
            if (
                claims["aud"] != self.audience
                or claims["email"] != self.email
                or claims["email_verified"] is not True
                or not claims["sub"]
            ):
                raise HTTPException(401)
        except PyJWKClientConnectionError:
            raise HTTPException(503) from None
        except (jwt.InvalidTokenError, PyJWKClientError, ValueError, TypeError):
            raise HTTPException(401) from None


def metadata_token(audience: str) -> str:
    """고정 메타데이터 서버에서 현재 실행 계정의 수신 서비스 전용 토큰을 얻는다.

    Args:
        audience: 설정에서 선택한 C/S 또는 TFT 서비스 URL.

    Returns:
        str: IAM 및 컨테이너 호출자 검증에 사용할 원본 ID 토큰.

    Raises:
        HTTPException: 메타데이터 조회 실패 또는 사용할 수 없는 응답이면 503.
    """
    cached = _TOKENS.get(audience)
    if cached and cached[1] - REFRESH_BEFORE_SECONDS > time.time():
        return cached[0]
    try:
        response = requests.get(
            METADATA_IDENTITY,
            params={"audience": audience, "format": "full"},
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
            allow_redirects=False,
        )
        token = response.text
        if (
            response.status_code != 200
            or not token
            or len(token) > 16384
            or any(c.isspace() for c in token)
        ):
            raise HTTPException(503)
    except RequestException:
        raise HTTPException(503) from None
    try:
        # 서명은 받는 쪽이 검증한다. 여기서는 다시 쓸 기한만 읽는다. 읽지 못하면 다시 쓰지 않는다.
        expires = float(jwt.decode(token, options={"verify_signature": False})["exp"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return token
    _TOKENS[audience] = (token, expires)
    return token
