"""실제 공급자 통신 없이 Google 호출자 서명 검사를 위한 합성 키를 제공한다."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def google_key():
    """검사 프로세스에서만 사용할 RSA 키와 Google 형태의 공개 JWK를 만든다.

    Args:
        없음.

    Returns:
        tuple: 비공개 검사 키와 공개 JWKS 응답.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return key, {"keys": [public | {"kid": "synthetic-google", "use": "sig", "alg": "RS256"}]}


@pytest.fixture
def google_tokens(google_key, monkeypatch):
    """공개키 조회 통신만 대체하고 실제 RSA 검증을 통과하는 합성 토큰을 발급한다.

    Args:
        google_key: 외부에 저장하지 않는 검사 키와 공개키 응답.
        monkeypatch: Google JWKS 네트워크 경계를 대체할 pytest 도구.

    Returns:
        Callable: audience와 덮어쓸 클레임을 받는 합성 ID 토큰 발급 함수.
    """
    key, jwks = google_key
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: jwks)

    def issue(audience, **changes):
        """정상 기본 클레임 또는 명시한 오류 클레임을 실제 RSA 키로 서명한다.

        Args:
            audience: 대상 업무 서비스 URL.
            **changes: 거부 조건을 재현하기 위해 교체할 클레임.

        Returns:
            str: 실제 Google 자격증명이 아닌 합성 서명 토큰.
        """
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": audience,
            "iat": now,
            "exp": now + 3600,
            "sub": "12345678901234567890",
            "email": "platform-runtime@synthetic-project.iam.gserviceaccount.com",
            "email_verified": True,
        } | changes
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "synthetic-google"})

    return issue
