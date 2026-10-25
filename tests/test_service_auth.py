"""Google 호출자 토큰의 실제 서명·클레임과 고정 메타데이터 경계를 검사한다."""

import time
from unittest.mock import Mock

import jwt
import pytest
import requests
from fastapi import HTTPException
from jwt.exceptions import PyJWKClientConnectionError
from pydantic import ValidationError

from backend.api.platform.main import create_app
from backend.shared.service_auth import METADATA_IDENTITY, PlatformCaller, metadata_token
from backend.shared.settings import Settings

AUDIENCE = "https://cs.example.invalid"
EMAIL = "platform-runtime@synthetic-project.iam.gserviceaccount.com"
RELAY = {
    "business_relay_enabled": True,
    "business_cs_url": AUDIENCE,
    "business_tft_url": "https://tft.example.invalid",
    "business_platform_email": EMAIL,
}


@pytest.mark.parametrize("issuer", ["https://accounts.google.com", "accounts.google.com"])
def test_accepts_google_signature_and_exact_receiver(google_tokens, issuer):
    """유효한 Google 형식 토큰의 서명과 수신 대상을 실제로 검사한다.

    Args:
        google_tokens: 합성 RSA 서명 토큰 발급기.
        issuer: Google이 사용하는 허용 issuer.

    Returns:
        None: 발급자·서명·수신 서비스·플랫폼 실행 계정이 일치함을 확인한다.
    """
    PlatformCaller(AUDIENCE, EMAIL).verify(google_tokens(AUDIENCE, iss=issuer))


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "https://tft.example.invalid"},
        {"aud": [AUDIENCE]},
        {"iss": "https://evil.invalid"},
        {"email": "another@synthetic-project.iam.gserviceaccount.com"},
        {"email_verified": False},
        {"email_verified": "true"},
        {"sub": ""},
        {"exp": int(time.time()) - 60},
        {"iat": int(time.time()) + 3600},
        {"email": None},
    ],
)
def test_rejects_wrong_identity_time_and_audience(google_tokens, claims):
    """다른 내부 서비스와 다른 실행 계정, 만료·미래 토큰을 거부한다.

    Args:
        google_tokens: 실제 RSA 서명 검사용 발급기.
        claims: 잘못된 서명 외 클레임 값.

    Returns:
        None: 모두 401로 거부하고 인증을 넓히지 않음을 확인한다.
    """
    with pytest.raises(HTTPException) as result:
        PlatformCaller(AUDIENCE, EMAIL).verify(google_tokens(AUDIENCE, **claims))
    assert result.value.status_code == 401


@pytest.mark.parametrize("kind", ["unsigned", "signature", "missing", "oversize"])
def test_rejects_unsigned_tampered_and_missing_claims(google_tokens, kind):
    """원본 서명과 필수 클레임을 검사하고 과도한 헤더를 차단한다.

    Args:
        google_tokens: 정상 합성 토큰 발급기.
        kind: 서명 제거·변조·필수 클레임 누락·크기 초과 조건.

    Returns:
        None: 토큰을 디코딩한 값만으로 신뢰하지 않음을 확인한다.
    """
    token = google_tokens(AUDIENCE)
    head, payload, signature = token.split(".")
    if kind == "unsigned":
        token = head + "." + payload + "."
    elif kind == "signature":
        token = head + "." + payload + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
    elif kind == "missing":
        token = google_tokens(AUDIENCE, exp=None)
    else:
        token = "x" * 16385
    with pytest.raises(HTTPException) as result:
        PlatformCaller(AUDIENCE, EMAIL).verify(token)
    assert result.value.status_code == 401


def test_jwks_failure_is_unavailable_not_auth_success(google_tokens, monkeypatch):
    """Google 공개키 장애를 인증 성공이나 빈 사용자로 숨기지 않는다.

    Args:
        google_tokens: 정상 합성 토큰 발급기.
        monkeypatch: 공개키 조회 장애 재현 도구.

    Returns:
        None: 공급자 원문을 노출하지 않고 503을 반환함을 확인한다.
    """
    monkeypatch.setattr(
        jwt.PyJWKClient, "fetch_data", Mock(side_effect=PyJWKClientConnectionError("private"))
    )
    with pytest.raises(HTTPException) as result:
        PlatformCaller(AUDIENCE, EMAIL).verify(google_tokens(AUDIENCE))
    assert result.value.status_code == 503
    assert "private" not in str(result.value)


def test_metadata_uses_fixed_endpoint_without_redirects(monkeypatch):
    """토큰 발급은 고정 메타데이터 서버와 정확한 업무 audience만 사용한다.

    Args:
        monkeypatch: 외부 네트워크 호출 대체 도구.

    Returns:
        None: 메타데이터 헤더·대상·시간 제한과 리디렉션 금지를 확인한다.
    """
    getter = Mock(return_value=Mock(status_code=200, text="synthetic-id-token"))
    monkeypatch.setattr(requests, "get", getter)
    assert metadata_token(AUDIENCE) == "synthetic-id-token"
    getter.assert_called_once_with(
        METADATA_IDENTITY,
        params={"audience": AUDIENCE, "format": "full"},
        headers={"Metadata-Flavor": "Google"},
        timeout=5,
        allow_redirects=False,
    )


@pytest.mark.parametrize(
    "response",
    [
        Mock(status_code=302, text="private"),
        Mock(status_code=200, text="bad\nheader"),
        requests.ConnectionError("private"),
    ],
)
def test_metadata_failure_does_not_disclose_provider(monkeypatch, response):
    """발급 실패와 헤더 주입을 정적인 의존 서비스 오류로 변환한다.

    Args:
        monkeypatch: 네트워크 대체 도구.
        response: 잘못된 응답 또는 연결 예외.

    Returns:
        None: 503과 공급자 내용 비노출을 확인한다.
    """
    getter = (
        Mock(side_effect=response)
        if isinstance(response, Exception)
        else Mock(return_value=response)
    )
    monkeypatch.setattr(requests, "get", getter)
    with pytest.raises(HTTPException) as result:
        metadata_token(AUDIENCE)
    assert result.value.status_code == 503 and "private" not in str(result.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"dev_auth_enabled": True},
        {"business_cs_url": "http://cs.example.invalid"},
        {"business_cs_url": "https://cs.example.invalid/path"},
        {"business_cs_url": "https://user:pass@cs.example.invalid"},
        {"business_cs_url": "https://cs.example.invalid?target=evil"},
        {"business_cs_url": "https://cs.example.invalid:8443"},
        {"business_tft_url": AUDIENCE},
        {"business_platform_email": "user@example.invalid"},
    ],
)
def test_enabled_settings_reject_unsafe_or_ambiguous_targets(changes):
    """명시 활성화 시 고정 HTTPS 출처와 플랫폼 실행 계정 설정을 강제한다.

    Args:
        changes: 거부할 설정 변경.

    Returns:
        None: 설정 단계에서 잘못된 중계 대상을 거부함을 확인한다.
    """
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **(RELAY | changes))


def test_platform_relay_requires_operational_login():
    """개발 인증이나 비활성 Microsoft 설정으로 업무 중계를 켤 수 없다.

    Args:
        없음.

    Returns:
        None: 플랫폼 시작 단계에서 불완전한 활성화를 거부함을 확인한다.
    """
    with pytest.raises(ValueError, match="Microsoft operational"):
        create_app(Settings(_env_file=None, **RELAY))
