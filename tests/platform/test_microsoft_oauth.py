"""실제 MSAL·RSA 서명 검증과 합성 HTTP 응답을 사용하는 개인 계정 회귀 검사."""

import json
from base64 import urlsafe_b64encode
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from backend.api.platform.auth.callback import OAuthPurpose, complete_callback
from backend.api.platform.auth.microsoft import (
    AUTHORITY,
    ISSUER,
    JWKS_URI,
    PERSONAL_TENANT,
    PersonalMicrosoftOAuth,
)
from backend.shared.errors import ApplicationError

CLIENT_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
OWNER_ID = UUID("33333333-3333-4333-8333-333333333333")
REDIRECT = "http://localhost:8765/callback"
BINDING = "synthetic-browser-binding-for-microsoft"


@pytest.fixture(scope="module")
def signing_key():
    """합성 ID 토큰을 서명할 테스트 전용 RSA 키를 생성한다.

    Args:
        없음.

    Returns:
        RSAPrivateKey: 외부로 전송하거나 저장하지 않는 합성 서명키.
    """
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oauth(monkeypatch, signing_key):
    """실제 MSAL에 연결할 합성 discovery·토큰·JWKS 응답을 준비한다.

    Args:
        monkeypatch: 네트워크 전송을 합성 응답으로 교체하는 pytest 도구.
        signing_key: 합성 ID 토큰을 서명할 RSA 비밀키.

    Returns:
        SimpleNamespace: 어댑터와 호출 기록 및 변경 가능한 합성 응답 설정.
    """
    context = SimpleNamespace(
        calls=[],
        claims={},
        response_overrides={},
        key=signing_key,
        algorithm="RS256",
        jwks_calls=[],
    )
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update(kid="synthetic-key", use="sig", alg="RS256")

    def request(_session, method, url, **kwargs):
        """Microsoft HTTP 전송 대신 합성 discovery 또는 서명 토큰을 반환한다.

        Args:
            _session: 실제 requests 세션.
            method: 요청 HTTP 메서드.
            url: MSAL이 선택한 요청 주소.
            **kwargs: 토큰 교환 본문 등 requests 요청 인자.

        Returns:
            Response: 합성 Microsoft HTTP 응답.
        """
        context.calls.append((method.upper(), url, kwargs))
        if method.upper() == "GET":
            assert url == f"{AUTHORITY}/v2.0/.well-known/openid-configuration"
            body = {
                "authorization_endpoint": f"{AUTHORITY}/oauth2/v2.0/authorize",
                "token_endpoint": f"{AUTHORITY}/oauth2/v2.0/token",
                "issuer": ISSUER,
                "jwks_uri": JWKS_URI,
            }
        else:
            assert method.upper() == "POST"
            assert url == f"{AUTHORITY}/oauth2/v2.0/token"
            body = {
                "id_token": jwt.encode(
                    context.claims,
                    context.key,
                    algorithm=context.algorithm,
                    headers={"kid": "synthetic-key"},
                ),
                "access_token": "synthetic-access-token",
                "refresh_token": "synthetic-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                **context.response_overrides,
            }
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        response.headers["Content-Type"] = "application/json"
        return response

    def fetch_keys(client):
        """고정된 Microsoft JWKS 주소에 대한 합성 공개키를 반환한다.

        Args:
            client: 실제 PyJWT 서명키 조회기.

        Returns:
            dict: 테스트 RSA 공개키를 포함한 JWKS 응답.
        """
        assert client.uri == JWKS_URI
        context.jwks_calls.append(client.uri)
        return {"keys": [jwk]}

    monkeypatch.setattr(requests.Session, "request", request)
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fetch_keys)
    context.adapter = PersonalMicrosoftOAuth(CLIENT_ID, SecretStr("synthetic-secret"), REDIRECT)
    return context


def begin_attempt(oauth, purpose=OAuthPurpose.LOGIN, owner_user_id=None):
    """인가 시도를 시작하고 실제 MSAL nonce에 맞춘 합성 ID 토큰을 준비한다.

    Args:
        oauth: 어댑터와 합성 응답을 포함하는 테스트 문맥.
        purpose: 인가 시도의 목적.
        owner_user_id: 사서함 연결을 시작하는 서비스 사용자 ID.

    Returns:
        tuple: 로그인 URL과 서버에 저장할 인가 시도 정보.
    """
    now = datetime.now(UTC)
    uri, pending = oauth.adapter.begin(
        purpose, browser_binding=BINDING, now=now, owner_user_id=owner_user_id
    )
    oauth.claims = {
        "iss": ISSUER,
        "aud": str(CLIENT_ID),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()) - 1,
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "nonce": parse_qs(urlsplit(uri).query)["nonce"][0],
        "tid": str(PERSONAL_TENANT),
        "oid": str(USER_ID),
        "sub": "synthetic-subject",
    }
    return uri, pending


def finish_attempt(oauth, pending):
    """공통 callback 검증을 거쳐 실제 MSAL 어댑터로 합성 code를 교환한다.

    Args:
        oauth: 어댑터와 합성 응답을 포함하는 테스트 문맥.
        pending: 로그인 시작 단계에서 생성한 인가 시도 정보.

    Returns:
        MicrosoftSubject: 합성 RSA 토큰 검증을 통과한 Microsoft 식별자.
    """
    return complete_callback(
        pending,
        [("state", pending.state), ("code", "synthetic-code")],
        purpose=pending.purpose,
        browser_binding=BINDING,
        session_user_id=pending.owner_user_id,
        now=datetime.now(UTC),
        adapter=oauth.adapter,
    )


def test_constructor_does_not_contact_microsoft(oauth):
    """어댑터 초기화만으로 외부 통신이 발생하지 않는지 확인한다.

    Args:
        oauth: 초기화된 개인 계정 어댑터와 호출 기록.

    Returns:
        None: 네트워크 미호출을 검증한다.
    """
    assert oauth.calls == []
    assert oauth.jwks_calls == []


@pytest.mark.parametrize(
    ("purpose", "owner"), [(OAuthPurpose.LOGIN, None), (OAuthPurpose.MAILBOX, OWNER_ID)]
)
def test_personal_flow_uses_consumers_web_redirect_pkce_and_distinct_scopes(oauth, purpose, owner):
    """개인 계정 인가 URL의 목적별 권한과 MSAL PKCE·nonce를 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 응답.
        purpose: 서비스 로그인 또는 사서함 연결 목적.
        owner: 사서함 연결을 시작하는 사용자 또는 None.

    Returns:
        None: consumers 경로·callback·권한·PKCE·nonce 생성을 검증한다.
    """
    uri, pending = begin_attempt(oauth, purpose, owner)
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "login.microsoftonline.com"
    assert parsed.path == "/consumers/oauth2/v2.0/authorize"
    assert query["redirect_uri"] == [REDIRECT]
    assert query["prompt"] == ["select_account"]
    assert query["response_type"] == ["code"]
    assert query["state"] == [pending.state]
    assert query["code_challenge_method"] == ["S256"]
    expected = urlsafe_b64encode(sha256(pending.provider_flow["code_verifier"].encode()).digest())
    assert query["code_challenge"] == [expected.rstrip(b"=").decode()]
    assert query["nonce"] == [sha256(pending.provider_flow["nonce"].encode()).hexdigest()]
    scopes = set(query["scope"][0].split())
    assert {"openid", "profile", "offline_access"} <= scopes
    assert ("Mail.Read" in scopes) == (purpose is OAuthPurpose.MAILBOX)
    assert ("Mail.Send" in scopes) == (purpose is OAuthPurpose.MAILBOX)
    assert pending.owner_user_id == owner
    assert pending.expires_at > datetime.now(UTC)
    assert "synthetic-secret" not in uri
    assert pending.provider_flow["code_verifier"] not in uri


def test_signed_personal_token_returns_subject_and_redeems_with_saved_pkce(oauth):
    """실제 MSAL code 교환과 RSA 검증을 거쳐 개인 계정 식별자를 얻는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 HTTP 응답.

    Returns:
        None: 식별 결과와 토큰 교환의 시크릿·callback·PKCE 일치를 검증한다.
    """
    _, pending = begin_attempt(oauth)
    result = finish_attempt(oauth, pending)
    assert result.tid == PERSONAL_TENANT
    assert result.oid == USER_ID
    assert set(vars(result)) == {"tid", "oid"}
    posts = [call for call in oauth.calls if call[0] == "POST"]
    assert len(posts) == 1
    body = posts[0][2]["data"]
    assert body["grant_type"] == "authorization_code"
    assert body["client_secret"] == "synthetic-secret"
    assert body["redirect_uri"] == REDIRECT
    assert body["code_verifier"] == pending.provider_flow["code_verifier"]
    assert oauth.jwks_calls == [JWKS_URI]


def test_mailbox_subject_is_separate_from_the_service_user(oauth):
    """기존 사용자에 결속한 사서함 동의도 별도의 Microsoft 식별자를 반환하는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 HTTP 응답.

    Returns:
        None: 사서함 식별 결과와 서비스 사용자 구분을 검증한다.
    """
    _, pending = begin_attempt(oauth, OAuthPurpose.MAILBOX, OWNER_ID)
    subject = finish_attempt(oauth, pending)
    assert subject.oid == USER_ID
    assert pending.owner_user_id == OWNER_ID
    assert all(url.startswith(AUTHORITY) for _, url, _ in oauth.calls)


@pytest.mark.parametrize("token", [None, "private-malformed-token"])
def test_access_token_alone_or_malformed_id_token_cannot_authenticate(oauth, token):
    """access token 유무와 별개로 유효한 ID 토큰이 없으면 로그인을 거부하는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 HTTP 응답.
        token: 누락 또는 형식이 잘못된 합성 ID 토큰.

    Returns:
        None: ID 토큰 없는 인증이나 손상된 토큰의 거부를 검증한다.
    """
    _, pending = begin_attempt(oauth)
    oauth.response_overrides["id_token"] = token
    with pytest.raises(ApplicationError, match="AUTH_INVALID_TOKEN"):
        finish_attempt(oauth, pending)


@pytest.mark.parametrize("phase", ["begin", "redeem"])
def test_microsoft_timeout_returns_safe_unavailable_error(oauth, monkeypatch, phase):
    """로그인 시작과 토큰 교환 중 연결 장애를 안전한 503 오류로 변환하는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 HTTP 응답.
        monkeypatch: Microsoft 요청을 연결 장애로 교체하는 pytest 도구.
        phase: 연결 장애를 발생시킬 로그인 시작 또는 토큰 교환 단계.

    Returns:
        None: 연결 장애의 구분과 공급자 상세 오류 비노출을 검증한다.
    """
    pending = begin_attempt(oauth)[1] if phase == "redeem" else None
    original_request = requests.Session.request

    def fail(session, method, url, **kwargs):
        """지정한 단계의 요청에 합성 연결 시간 초과를 발생시킨다.

        Args:
            session: 실제 requests 세션.
            method: MSAL이 요청한 HTTP 메서드.
            url: Microsoft 인가 서버 주소.
            **kwargs: MSAL이 구성한 HTTP 요청 인자.

        Returns:
            Response: 장애 대상이 아닌 요청의 합성 응답.

        Raises:
            Timeout: 지정한 단계의 요청인 경우 발생한다.
        """
        if phase == "begin" or method.upper() == "POST":
            raise requests.exceptions.Timeout("private-provider-details")
        return original_request(session, method, url, **kwargs)

    monkeypatch.setattr(requests.Session, "request", fail)
    with pytest.raises(ApplicationError, match="AUTH_PROVIDER_UNAVAILABLE") as error:
        if phase == "begin":
            begin_attempt(oauth)
        else:
            finish_attempt(oauth, pending)
    assert error.value.status_code == 503
    assert error.value.message == "Authentication is unavailable."


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iss", "https://login.microsoftonline.com/another-tenant/v2.0"),
        ("aud", "another-application"),
        ("aud", [str(CLIENT_ID), "another-application"]),
        ("exp", 1),
        ("iat", 9999999999),
        ("nbf", 9999999999),
        ("nonce", "private-wrong-nonce"),
        ("tid", "44444444-4444-4444-8444-444444444444"),
        ("oid", "private-invalid-oid"),
        ("oid", "00000000-0000-0000-0000-000000000000"),
    ],
)
def test_invalid_token_claims_are_rejected_without_private_details(oauth, claim, value):
    """서명이 올바르더라도 발급자·대상·시각·식별자가 잘못되면 거부하는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 응답.
        claim: 변조할 ID 토큰 클레임 이름.
        value: 합성 비정상 클레임 값.

    Returns:
        None: 토큰 거부와 오류 메시지의 민감값 비노출을 검증한다.
    """
    _, pending = begin_attempt(oauth)
    oauth.claims[claim] = value
    with pytest.raises(ApplicationError) as error:
        finish_attempt(oauth, pending)
    assert error.value.code == "AUTH_INVALID_TOKEN"
    assert error.value.message == "Authentication failed."
    assert error.value.__cause__ is None


@pytest.mark.parametrize("claim", ["iss", "aud", "exp", "iat", "nonce", "tid", "oid"])
def test_required_id_token_claims_cannot_be_omitted(oauth, claim):
    """필수 ID 토큰 클레임 누락을 거부하는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 응답.
        claim: 합성 ID 토큰에서 제거할 클레임 이름.

    Returns:
        None: 필수 클레임 누락이 인증 성공으로 처리되지 않는지 검증한다.
    """
    _, pending = begin_attempt(oauth)
    del oauth.claims[claim]
    with pytest.raises(ApplicationError, match="AUTH_INVALID_TOKEN"):
        finish_attempt(oauth, pending)


def test_token_signed_by_another_key_is_rejected(oauth):
    """공개키와 일치하지 않는 RSA 서명을 거부하는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 응답.

    Returns:
        None: 실제 서명 검증 실패를 확인한다.
    """
    _, pending = begin_attempt(oauth)
    oauth.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ApplicationError, match="AUTH_INVALID_TOKEN"):
        finish_attempt(oauth, pending)


def test_hmac_token_is_rejected_without_fetching_signing_keys(oauth):
    """허용하지 않는 서명 알고리즘의 토큰을 거부하는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 응답.

    Returns:
        None: RS256 이외 알고리즘 거부와 JWKS 미호출을 검증한다.
    """
    _, pending = begin_attempt(oauth)
    oauth.algorithm = "HS256"
    oauth.key = "synthetic-hmac-key-with-at-least-32-characters"
    with pytest.raises(ApplicationError, match="AUTH_INVALID_TOKEN"):
        finish_attempt(oauth, pending)
    assert oauth.jwks_calls == []


@pytest.mark.parametrize("field", ["redirect_uri", "state", "nonce", "code_verifier"])
def test_saved_flow_mismatch_is_rejected_before_token_exchange(oauth, field):
    """저장된 flow의 필수값 손상이나 callback 설정 불일치를 사전에 거부하는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 응답.
        field: 제거하거나 변경할 MSAL flow 필드 이름.

    Returns:
        None: 잘못된 flow로 토큰 교환을 실행하지 않는지 검증한다.
    """
    _, pending = begin_attempt(oauth)
    flow = dict(pending.provider_flow)
    flow[field] = "http://localhost:9999/another" if field == "redirect_uri" else ""
    with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
        finish_attempt(oauth, replace(pending, provider_flow=flow))
    assert not [call for call in oauth.calls if call[0] == "POST"]


def test_provider_error_is_not_replaced_by_a_successful_identity(oauth):
    """공급자가 오류를 반환하면 인증 성공으로 처리하지 않는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 응답.

    Returns:
        None: 원시 공급자 설명을 포함하지 않는 고정 오류를 검증한다.
    """
    _, pending = begin_attempt(oauth)
    oauth.response_overrides = {"error": "invalid_grant", "error_description": "private-details"}
    with pytest.raises(ApplicationError, match="AUTH_INVALID_TOKEN") as error:
        finish_attempt(oauth, pending)
    assert error.value.message == "Authentication failed."


def test_jwks_outage_is_reported_as_unavailable_without_accepting_token(oauth, monkeypatch):
    """서명키 조회 장애가 인증 성공이나 잘못된 토큰 판정으로 숨겨지지 않는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 응답.
        monkeypatch: 공개키 조회를 장애 응답으로 교체하는 pytest 도구.

    Returns:
        None: 키 조회 장애의 HTTP 503 변환을 검증한다.
    """
    _, pending = begin_attempt(oauth)

    def fail(_client):
        """합성 서명키 조회 장애를 발생시킨다.

        Args:
            _client: 실제 PyJWT 서명키 조회기.

        Returns:
            None: 정상 반환 없이 조회 예외를 발생시킨다.
        """
        raise jwt.PyJWKClientConnectionError("private-provider-details")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fail)
    with pytest.raises(ApplicationError, match="AUTH_PROVIDER_UNAVAILABLE") as error:
        finish_attempt(oauth, pending)
    assert error.value.status_code == 503
    assert "private" not in error.value.message


@pytest.mark.parametrize(
    "redirect",
    [
        "http://example.com/callback",
        "https://user:pass@example.com/cb",
        "https://example.com/cb#fragment",
    ],
)
def test_unsafe_server_redirect_is_rejected(redirect):
    """평문 외부 주소나 사용자 정보·fragment가 있는 callback 설정을 거부하는지 확인한다.

    Args:
        redirect: 허용되지 않는 합성 callback 주소.

    Returns:
        None: 안전하지 않은 서버 callback 설정의 거부를 검증한다.
    """
    with pytest.raises(ValueError):
        PersonalMicrosoftOAuth(CLIENT_ID, SecretStr("synthetic-secret"), redirect)
