"""실제 MSAL·RSA 서명 검증과 합성 HTTP 응답을 사용하는 개인 계정 회귀 검사."""

import asyncio
import json
from base64 import b64encode, urlsafe_b64encode
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_bytes
from threading import Barrier, get_ident
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import jwt
import msal
import psycopg
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.api.platform.auth.callback import MicrosoftSubject, OAuthPurpose, complete_callback
from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
from backend.api.platform.auth.mailbox import (
    complete_and_store_mailbox_link,
    start_mailbox_link,
)
from backend.api.platform.auth.mailbox_settings import MAILBOX_CALLBACK, MailboxAuthSettings
from backend.api.platform.auth.mailbox_tokens import MailboxTokenProvider
from backend.api.platform.auth.mailbox_vault import (
    MailboxCipher,
    create_mailbox,
    disconnect_mailbox,
    load_mailbox,
    reconnect_mailbox,
)
from backend.api.platform.auth.microsoft import (
    AUTHORITY,
    ISSUER,
    JWKS_URI,
    PERSONAL_TENANT,
    MailboxAuthorization,
    PersonalMicrosoftOAuth,
)
from backend.api.platform.auth.sessions import issue_session, load_session
from backend.api.platform.auth.settings import CALLBACK_PATH, MicrosoftAuthSettings
from backend.api.platform.mail.storage import LocalObjectStore
from backend.api.platform.main import create_app
from backend.shared.errors import ApplicationError
from tests.platform import test_mail_sync_db as mail_sync_db
from tests.platform.test_mail_sync_db import mail_database as mail_database

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
            if getattr(context, "token_error", None):
                raise context.token_error
            body = {
                "id_token": jwt.encode(
                    context.claims,
                    context.key,
                    algorithm=context.algorithm,
                    headers={"kid": getattr(context, "kid", "synthetic-key")},
                ),
                "access_token": "synthetic-access-token",
                "refresh_token": "synthetic-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                **context.response_overrides,
            }
            if getattr(context, "token_response", None) is not None:
                body = context.token_response
        response = requests.Response()
        response.status_code = getattr(context, "response_status", 200) if method == "POST" else 200
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
    assert query["response_mode"] == ["form_post"]
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


@pytest.mark.parametrize(
    ("claim", "offset", "accepted"),
    [
        ("iat", 59, True),
        ("iat", 61, False),
        ("nbf", 59, True),
        ("nbf", 61, False),
        ("exp", -59, True),
        ("exp", -61, False),
    ],
)
def test_id_token_clock_skew_has_a_sixty_second_limit(oauth, monkeypatch, claim, offset, accepted):
    """서명 토큰의 시각 오차는 60초 이내에서만 허용하는지 확인한다.

    Args:
        oauth: 개인 계정 어댑터와 합성 HTTP 응답.
        monkeypatch: PyJWT의 검증 시각을 고정하는 pytest 도구.
        claim: 발급·사용 시작·만료 시각 중 변경할 클레임.
        offset: 고정된 검증 시각과 클레임의 차이(초).
        accepted: 해당 오차에서 인증을 허용해야 하는지 여부.

    Returns:
        None: 실제 MSAL 교환과 RSA 검증 후 오차 범위 안팎의 결과를 확인한다.
    """
    _, pending = begin_attempt(oauth)
    checked_at = datetime.now(UTC).replace(microsecond=0)

    class ValidationClock(datetime):
        """토큰 검증만 고정된 UTC 시각을 사용하도록 하는 테스트 시계."""

        @classmethod
        def now(cls, tz=None):
            """검증 시각을 요청한 시간대에 맞춰 반환한다.

            Args:
                tz: 반환할 datetime의 시간대. 없으면 시간대 정보를 제거한다.

            Returns:
                datetime: 테스트 시작 때 고정한 검증 시각.
            """
            return checked_at.astimezone(tz) if tz else checked_at.replace(tzinfo=None)

    monkeypatch.setattr(jwt.api_jwt, "datetime", ValidationClock)
    oauth.claims.update(
        iat=int(checked_at.timestamp()) - 3600,
        nbf=int(checked_at.timestamp()) - 3600,
    )
    oauth.claims[claim] = int(checked_at.timestamp()) + offset
    if accepted:
        assert finish_attempt(oauth, pending).oid == USER_ID
    else:
        with pytest.raises(ApplicationError, match="AUTH_INVALID_TOKEN"):
            finish_attempt(oauth, pending)


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


@pytest.fixture
def mailbox_cache(oauth, monkeypatch):
    """실제 MSAL code 교환·RSA 검증을 거친 합성 사서함 캐시를 준비한다.

    Args:
        oauth: Microsoft HTTP 응답을 합성한 기존 어댑터 문맥.
        monkeypatch: 이번 합성 연결의 MSAL 캐시를 지정할 도구.

    Returns:
        SimpleNamespace: 검증 식별자와 외부로 저장하지 않을 합성 캐시.
    """
    cache = msal.SerializableTokenCache()
    factory = oauth.adapter._application

    def application(*, token_cache=None):
        """현재 합성 연결에만 캐시를 주입한다.

        Args:
            token_cache: 호출자가 명시한 캐시 또는 None.

        Returns:
            ConfidentialClientApplication: 실제 MSAL 서버 앱.
        """
        return factory(token_cache=cache if token_cache is None else token_cache)

    client_info = json.dumps({"uid": str(USER_ID), "utid": str(PERSONAL_TENANT)}).encode()
    oauth.response_overrides.update(
        client_info=urlsafe_b64encode(client_info).rstrip(b"=").decode(),
        scope="Mail.Read Mail.Send",
    )
    with monkeypatch.context() as patch:
        patch.setattr(oauth.adapter, "_application", application)
        _, pending = begin_attempt(oauth, OAuthPurpose.MAILBOX, OWNER_ID)
        subject = finish_attempt(oauth, pending)
    return SimpleNamespace(subject=subject, serialized=SecretStr(cache.serialize()))


def expired_mailbox_cache(mailbox_cache, *, remove_refresh=False):
    """실제 MSAL이 생성한 캐시의 유효기간만 만료시킨다.

    Args:
        mailbox_cache: 합성 사서함 연결 캐시.
        remove_refresh: 갱신 자격증명이 없는 경우를 검사할지 여부.

    Returns:
        SecretStr: 메모리에서만 사용하는 만료 상태의 캐시.
    """
    data = json.loads(mailbox_cache.serialized.get_secret_value())
    for token in data["AccessToken"].values():
        token.update(expires_on="1", extended_expires_on="1")
    if remove_refresh:
        data.pop("RefreshToken", None)
    return SecretStr(json.dumps(data))


def test_mailbox_cache_hit_returns_opaque_token_without_refresh(oauth, mailbox_cache):
    """유효한 연결 캐시는 갱신 POST 없이 사용하고 민감 결과를 repr에 숨긴다.

    Args:
        oauth: 합성 공급자 HTTP 문맥.
        mailbox_cache: 실제 MSAL로 생성한 합성 연결 캐시.

    Returns:
        None: 캐시 적중·입력 불변·결과 마스킹을 검증한다.
    """
    before = len([c for c in oauth.calls if c[0] == "POST"])
    original = mailbox_cache.serialized.get_secret_value()
    result = oauth.adapter.acquire_mailbox_token(
        subject=mailbox_cache.subject, serialized_cache=mailbox_cache.serialized
    )
    assert result.access_token.get_secret_value() == "synthetic-access-token"
    assert len([c for c in oauth.calls if c[0] == "POST"]) == before
    assert mailbox_cache.serialized.get_secret_value() == original
    for secret in ("synthetic-access-token", "synthetic-refresh-token", str(USER_ID)):
        assert secret not in repr(result)


def test_expired_mailbox_cache_uses_sdk_refresh_and_returns_rotated_cache(
    oauth, mailbox_cache, caplog
):
    """만료 캐시를 실제 MSAL로 갱신하고 새 보관 상태만 반환하는지 확인한다.

    Args:
        oauth: 합성 Microsoft 토큰 응답 문맥.
        mailbox_cache: 기존 access/refresh token을 포함한 합성 캐시.
        caplog: 실제 SDK의 DEBUG 기록까지 확인할 수집기.

    Returns:
        None: 갱신 grant·회전된 캐시·표시 이메일 비결속을 검증한다.
    """
    caplog.set_level("DEBUG", logger="msal")
    oauth.response_overrides.update(
        access_token="synthetic-refreshed-access", refresh_token="synthetic-rotated-refresh"
    )
    oauth.claims["preferred_username"] = "changed-display@example.invalid"
    oauth.claims.pop("nonce")
    result = oauth.adapter.acquire_mailbox_token(
        subject=mailbox_cache.subject, serialized_cache=expired_mailbox_cache(mailbox_cache)
    )
    post = [c for c in oauth.calls if c[0] == "POST"][-1]
    assert post[2]["data"]["grant_type"] == "refresh_token"
    assert post[2]["data"]["refresh_token"] == "synthetic-refresh-token"
    assert result.access_token.get_secret_value() == "synthetic-refreshed-access"
    assert result.cache_changed
    stored = result.serialized_cache.get_secret_value()
    assert "synthetic-rotated-refresh" in stored
    assert "synthetic-refresh-token" not in stored
    assert "synthetic-rotated-refresh" not in repr(result)
    old_id = next(iter(json.loads(mailbox_cache.serialized.get_secret_value())["IdToken"].values()))
    new_id = next(iter(json.loads(stored)["IdToken"].values()))
    assert new_id["secret"] != old_id["secret"]
    for secret in (
        "synthetic-refreshed-access",
        "synthetic-rotated-refresh",
        "synthetic-secret",
        oauth.response_overrides["client_info"],
        str(USER_ID),
    ):
        assert secret not in caplog.text


@pytest.mark.parametrize("case", ["subject", "realm", "environment", "multiple"])
def test_mailbox_account_mismatch_is_rejected_before_provider_call(oauth, mailbox_cache, case):
    """이메일을 공유해도 다른 식별자·계정 묶음은 공급자 호출 전에 거부한다.

    Args:
        oauth: 합성 HTTP 호출 기록.
        mailbox_cache: 정상 연결의 합성 캐시.
        case: 변경할 계정 식별 조건.

    Returns:
        None: 계정 불일치의 접근 거부와 외부 미호출을 검증한다.
    """
    data = json.loads(mailbox_cache.serialized.get_secret_value())
    account = next(iter(data["Account"].values()))
    subject = mailbox_cache.subject
    if case == "subject":
        subject = MicrosoftSubject(PERSONAL_TENANT, OWNER_ID)
    elif case == "multiple":
        data["Account"]["another-synthetic-account"] = {
            **account,
            "home_account_id": str(OWNER_ID),
            "local_account_id": str(OWNER_ID),
        }
    else:
        account[case] = "untrusted.example.invalid"
    before = len(oauth.calls)
    with pytest.raises(ApplicationError, match="MAILBOX_ACCESS_DENIED") as error:
        oauth.adapter.acquire_mailbox_token(
            subject=subject, serialized_cache=SecretStr(json.dumps(data))
        )
    assert error.value.status_code == 403
    assert len(oauth.calls) == before


@pytest.mark.parametrize("serialized", ["{", "[]", "null"])
def test_malformed_mailbox_cache_is_safe_unavailable_without_http(oauth, serialized):
    """손상된 보관 상태를 계정 부재나 성공으로 숨기지 않는다.

    Args:
        oauth: 아직 외부 요청이 없는 공급자 문맥.
        serialized: 손상된 합성 캐시 본문.

    Returns:
        None: 안전한 제공 장애와 외부 미호출을 검증한다.
    """
    with pytest.raises(ApplicationError, match="MAILBOX_TOKEN_UNAVAILABLE") as error:
        oauth.adapter.acquire_mailbox_token(
            subject=MicrosoftSubject(PERSONAL_TENANT, USER_ID),
            serialized_cache=SecretStr(serialized),
        )
    assert error.value.status_code == 503
    assert oauth.calls == []


def test_missing_refresh_credential_requires_reconnection(oauth, mailbox_cache):
    """만료 토큰만 남으면 빈 토큰을 반환하지 않고 재연결을 요구한다.

    Args:
        oauth: 합성 HTTP 공급자 문맥.
        mailbox_cache: 갱신 자격증명을 제거할 합성 캐시.

    Returns:
        None: MSAL 미획득 결과의 재연결 분류를 검증한다.
    """
    before = len([c for c in oauth.calls if c[0] == "POST"])
    with pytest.raises(ApplicationError, match="MAILBOX_RECONNECT_REQUIRED") as error:
        oauth.adapter.acquire_mailbox_token(
            subject=mailbox_cache.subject,
            serialized_cache=expired_mailbox_cache(mailbox_cache, remove_refresh=True),
        )
    assert error.value.status_code == 409
    assert len([c for c in oauth.calls if c[0] == "POST"]) == before


@pytest.mark.parametrize(
    ("provider_error", "expected", "status"),
    [
        ("invalid_grant", "MAILBOX_RECONNECT_REQUIRED", 409),
        ("interaction_required", "MAILBOX_RECONNECT_REQUIRED", 409),
        ("consent_required", "MAILBOX_RECONNECT_REQUIRED", 409),
        ("login_required", "MAILBOX_RECONNECT_REQUIRED", 409),
        ("temporarily_unavailable", "MAILBOX_TOKEN_UNAVAILABLE", 503),
        ("invalid_client", "MAILBOX_TOKEN_UNAVAILABLE", 503),
        ("unknown_error", "MAILBOX_TOKEN_UNAVAILABLE", 503),
    ],
)
def test_mailbox_refresh_error_is_classified_without_provider_details(
    oauth, mailbox_cache, provider_error, expected, status, caplog
):
    """공급자 상세를 노출하지 않고 재연결과 일시 장애를 구분한다.

    Args:
        oauth: 합성 HTTP 공급자 문맥.
        mailbox_cache: 만료 상태로 바꿀 합성 캐시.
        provider_error: 합성 OAuth 오류 코드.
        expected: 기대하는 내부 오류 코드.
        status: 기대하는 상태 값.
        caplog: 민감 로그 노출을 검사할 수집기.

    Returns:
        None: 오류 분류와 민감 값 비노출을 검증한다.
    """
    caplog.set_level("DEBUG", logger="msal")
    oauth.token_response = {
        "error": provider_error,
        "error_description": "synthetic-access-token synthetic-refresh-token synthetic-secret",
    }
    oauth.response_status = 400
    with pytest.raises(ApplicationError, match=expected) as error:
        oauth.adapter.acquire_mailbox_token(
            subject=mailbox_cache.subject, serialized_cache=expired_mailbox_cache(mailbox_cache)
        )
    assert error.value.status_code == status
    for secret in ("synthetic-access-token", "synthetic-refresh-token", "synthetic-secret"):
        assert secret not in repr(error.value) + error.value.message + caplog.text


def test_mailbox_refresh_transport_failure_does_not_become_reconnect(oauth, mailbox_cache):
    """공급자 통신 장애를 재연결 필요로 잘못 분류하지 않는다.

    Args:
        oauth: 토큰 요청에 실패할 합성 공급자 문맥.
        mailbox_cache: 갱신이 필요한 합성 캐시.

    Returns:
        None: 통신 실패의 안전한 일시 장애 변환을 검증한다.
    """
    oauth.token_error = requests.ConnectionError("synthetic-refresh-token private-details")
    with pytest.raises(ApplicationError, match="MAILBOX_TOKEN_UNAVAILABLE") as error:
        oauth.adapter.acquire_mailbox_token(
            subject=mailbox_cache.subject, serialized_cache=expired_mailbox_cache(mailbox_cache)
        )
    assert error.value.status_code == 503
    assert "private" not in str(error.value) + error.value.message


@pytest.mark.parametrize("changed", ["oid", "home_account"])
def test_refresh_cannot_replace_verified_mailbox_subject(oauth, mailbox_cache, changed):
    """갱신 결과가 다른 사용자 계정으로 바뀌면 토큰과 캐시를 반환하지 않는다.

    Args:
        oauth: 다른 oid를 반환하도록 변경할 합성 공급자 문맥.
        mailbox_cache: 원래 식별자에 결속된 합성 캐시.
        changed: 다른 계정으로 바꿀 공급자 식별 정보.

    Returns:
        None: 갱신 후 계정 식별자 보존을 검증한다.
    """
    if changed == "oid":
        oauth.claims["oid"] = str(OWNER_ID)
    else:
        info = json.dumps({"uid": str(OWNER_ID), "utid": str(PERSONAL_TENANT)}).encode()
        oauth.response_overrides["client_info"] = urlsafe_b64encode(info).rstrip(b"=").decode()
    with pytest.raises(ApplicationError, match="MAILBOX_ACCESS_DENIED"):
        oauth.adapter.acquire_mailbox_token(
            subject=mailbox_cache.subject, serialized_cache=expired_mailbox_cache(mailbox_cache)
        )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("access_token", "", "MAILBOX_TOKEN_UNAVAILABLE"),
        ("expires_in", 0, "MAILBOX_TOKEN_UNAVAILABLE"),
        ("expires_in", True, "MAILBOX_TOKEN_UNAVAILABLE"),
        ("expires_in", "invalid", "MAILBOX_TOKEN_UNAVAILABLE"),
        ("token_type", "PoP", "MAILBOX_TOKEN_UNAVAILABLE"),
        ("scope", "Mail.Read", "MAILBOX_RECONNECT_REQUIRED"),
    ],
)
def test_unusable_mailbox_token_is_not_returned(oauth, mailbox_cache, field, value, expected):
    """만료·형식·동의 범위가 올바르지 않은 토큰을 성공으로 반환하지 않는다.

    Args:
        oauth: 합성 토큰 응답 문맥.
        mailbox_cache: 갱신이 필요한 합성 연결 캐시.
        field: 변경할 토큰 응답 필드.
        value: 성공으로 취급하면 안 되는 값.
        expected: 기대하는 안전한 오류 코드.

    Returns:
        None: 잘못된 공급자 응답의 실패 분류를 검증한다.
    """
    oauth.response_overrides[field] = value
    with pytest.raises(ApplicationError, match=expected):
        oauth.adapter.acquire_mailbox_token(
            subject=mailbox_cache.subject, serialized_cache=expired_mailbox_cache(mailbox_cache)
        )


def test_refresh_without_id_token_accepts_fixed_graph_scope_uri(oauth, mailbox_cache):
    """새 ID 토큰이 없는 갱신도 캐시 계정과 고정 Graph 권한을 대조한다.

    Args:
        oauth: 합성 토큰 응답 문맥.
        mailbox_cache: 갱신이 필요한 합성 연결 캐시.

    Returns:
        None: 새 신원 주장을 요구하지 않는 정상 갱신을 검증한다.
    """
    oauth.token_response = {
        "access_token": "synthetic-without-id-token",
        "refresh_token": "synthetic-rotated-refresh",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.Send",
        "client_info": oauth.response_overrides["client_info"],
    }
    result = oauth.adapter.acquire_mailbox_token(
        subject=mailbox_cache.subject, serialized_cache=expired_mailbox_cache(mailbox_cache)
    )
    assert result.access_token.get_secret_value() == "synthetic-without-id-token"
    assert result.cache_changed


def test_refresh_id_token_signature_is_checked(oauth, mailbox_cache):
    """MSAL이 디코딩만 한 갱신 ID 토큰도 실제 서명을 검사한다.

    Args:
        oauth: 다른 키로 토큰을 서명할 합성 공급자 문맥.
        mailbox_cache: 원래 서명 검증으로 만든 연결 캐시.

    Returns:
        None: 위조 ID 토큰이 토큰 결과로 전달되지 않음을 검증한다.
    """
    oauth.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ApplicationError, match="MAILBOX_ACCESS_DENIED"):
        oauth.adapter.acquire_mailbox_token(
            subject=mailbox_cache.subject, serialized_cache=expired_mailbox_cache(mailbox_cache)
        )


def test_refresh_signing_keys_outage_is_not_reconnection(oauth, mailbox_cache, monkeypatch):
    """캐시에 없는 새 서명키 조회 실패는 재연결 필요와 구분한다.

    Args:
        oauth: 합성 토큰 응답 문맥.
        mailbox_cache: 원래 서명 검증으로 만든 연결 캐시.
        monkeypatch: 공개키 조회를 장애로 바꾸는 도구.

    Returns:
        None: 서명키 장애의 안전한 제공 실패를 검증한다.
    """

    def fail(_client):
        """공급자 원문을 포함할 수 있는 키 조회 실패를 합성한다.

        Args:
            _client: 사용하지 않을 서명키 조회기.

        Returns:
            None: 반환하지 않고 조회 장애를 발생시킨다.
        """
        raise jwt.PyJWKClientConnectionError("private-provider-details")

    oauth.kid = "synthetic-new-key"
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fail)
    with pytest.raises(ApplicationError, match="MAILBOX_TOKEN_UNAVAILABLE") as error:
        oauth.adapter.acquire_mailbox_token(
            subject=mailbox_cache.subject, serialized_cache=expired_mailbox_cache(mailbox_cache)
        )
    assert error.value.status_code == 503
    assert "private" not in error.value.message


def test_sdk_aging_refresh_failure_can_only_return_still_valid_cache(oauth, mailbox_cache):
    """선제 갱신 장애에도 SDK가 반환한 아직 유효한 캐시는 검증 후 사용한다.

    Args:
        oauth: 갱신 통신 장애를 반환할 합성 공급자 문맥.
        mailbox_cache: 만료 전 선제 갱신 대상으로 바꿀 합성 캐시.

    Returns:
        None: SDK의 기본 유효 캐시 동작을 만료 토큰 성공과 구분한다.
    """
    data = json.loads(mailbox_cache.serialized.get_secret_value())
    for entry in data["AccessToken"].values():
        entry["refresh_on"] = "1"
    oauth.token_error = requests.ConnectionError("private-provider-details")
    result = oauth.adapter.acquire_mailbox_token(
        subject=mailbox_cache.subject, serialized_cache=SecretStr(json.dumps(data))
    )
    assert result.access_token.get_secret_value() == "synthetic-access-token"
    assert [c for c in oauth.calls if c[0] == "POST"][-1][2]["data"][
        "grant_type"
    ] == "refresh_token"


@pytest.fixture
def mailbox_attempt(oauth):
    """캐시를 별도로 주입하지 않고 초기 사서함 인가 입력을 준비한다.

    Args:
        oauth: 실제 MSAL에 합성 HTTP를 연결한 문맥.

    Returns:
        PendingAuthorization: 아직 code를 교환하지 않은 사서함 인가 시도.
    """
    info = json.dumps({"uid": str(USER_ID), "utid": str(PERSONAL_TENANT)}).encode()
    oauth.response_overrides.update(
        client_info=urlsafe_b64encode(info).rstrip(b"=").decode(),
        scope="Mail.Read Mail.Send",
    )
    return begin_attempt(oauth, OAuthPurpose.MAILBOX, OWNER_ID)[1]


def finish_mailbox(oauth, pending, **overrides):
    """소유자·브라우저·목적 검증을 포함한 실제 초기 캐시 전달을 실행한다.

    Args:
        oauth: 실제 MSAL과 합성 Microsoft 응답 문맥.
        pending: 사서함 인가 시도.
        **overrides: 정상 결속·입력을 교체할 검사 인자.

    Returns:
        MailboxAuthorization: 서명과 캐시 검증을 통과한 서버 내부 결과.
    """
    arguments = {
        "browser_binding": BINDING,
        "session_user_id": OWNER_ID,
        "now": datetime.now(UTC),
        **overrides,
    }
    parameters = arguments.pop("parameters", [("state", pending.state), ("code", "synthetic-code")])
    return oauth.adapter.redeem_mailbox(pending, parameters, **arguments)


def test_initial_mailbox_result_binds_owner_and_can_feed_existing_refresh(
    oauth, mailbox_attempt, caplog
):
    """서명 검증된 초기 캐시가 기존 조회·갱신 경계로 이어지고 비밀은 숨겨진다.

    Args:
        oauth: 합성 토큰과 실제 MSAL 문맥.
        mailbox_attempt: 서버 소유자가 정해진 초기 인가 시도.
        caplog: SDK DEBUG 기록의 민감 값 비노출 수집기.

    Returns:
        None: 소유자 분리·일회 POST·캐시 호환·로그와 repr 비노출을 검증한다.
    """
    caplog.set_level("DEBUG", logger="msal")
    result = finish_mailbox(
        oauth,
        mailbox_attempt,
        parameters=[
            ("state", mailbox_attempt.state),
            ("code", "synthetic-code"),
            ("owner_user_id", str(USER_ID)),
            ("role", "manager"),
        ],
    )
    assert result.owner_user_id == OWNER_ID
    assert result.subject == MicrosoftSubject(PERSONAL_TENANT, USER_ID)
    assert result.subject.oid != result.owner_user_id
    assert not hasattr(result, "access_token")
    cache = result.serialized_cache.get_secret_value()
    assert "synthetic-refresh-token" in cache
    assert len([c for c in oauth.calls if c[0] == "POST"]) == 1
    post = [c for c in oauth.calls if c[0] == "POST"][0]
    assert "owner_user_id" not in post[2]["data"]
    update = oauth.adapter.acquire_mailbox_token(
        subject=result.subject, serialized_cache=result.serialized_cache
    )
    assert update.access_token.get_secret_value() == "synthetic-access-token"
    assert len([c for c in oauth.calls if c[0] == "POST"]) == 1
    for secret in (
        "synthetic-access-token",
        "synthetic-refresh-token",
        "synthetic-code",
        "synthetic-secret",
        oauth.response_overrides["client_info"],
        str(USER_ID),
        str(OWNER_ID),
    ):
        assert secret not in repr(result) + caplog.text


@pytest.mark.parametrize("invalid", ["purpose", "owner", "browser", "expired", "duplicate"])
def test_initial_mailbox_bad_binding_never_reaches_exchange(oauth, mailbox_attempt, invalid):
    """초기 캐시를 반환하는 경로도 기존 결속·만료·중복 입력을 거부한다.

    Args:
        oauth: HTTP 호출 기록이 있는 합성 공급자.
        mailbox_attempt: 정상 시작된 사서함 인가 시도.
        invalid: 변조할 인가 결속 조건.

    Returns:
        None: 잘못된 입력의 교환 전 거부를 검증한다.
    """
    changes = {}
    if invalid == "purpose":
        mailbox_attempt = replace(mailbox_attempt, purpose=OAuthPurpose.LOGIN, owner_user_id=None)
    elif invalid == "owner":
        changes["session_user_id"] = USER_ID
    elif invalid == "browser":
        changes["browser_binding"] = "another-browser"
    elif invalid == "expired":
        changes["now"] = mailbox_attempt.expires_at
    else:
        changes["parameters"] = [
            ("state", mailbox_attempt.state),
            ("code", "synthetic-code"),
            ("code", "another-code"),
        ]
    with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
        finish_mailbox(oauth, mailbox_attempt, **changes)
    assert not [c for c in oauth.calls if c[0] == "POST"]


@pytest.mark.parametrize("invalid", ["nonce", "signature", "tenant", "jwks"])
def test_initial_mailbox_rejects_unverified_identity(oauth, mailbox_attempt, invalid, monkeypatch):
    """캐시가 만들어져도 초기 nonce·서명·사서함 식별 검증 실패 시 반환하지 않는다.

    Args:
        oauth: 서명·식별자를 바꿀 합성 공급자.
        mailbox_attempt: 아직 교환하지 않은 인가 시도.
        invalid: 잘못된 nonce·서명·개인 계정 테넌트 또는 키 조회 장애.
        monkeypatch: 합성 JWKS 장애를 주입할 도구.

    Returns:
        None: 미검증 캐시가 보관 경계에 인계되지 않음을 검증한다.
    """
    expected = "AUTH_INVALID_TOKEN"
    if invalid == "nonce":
        oauth.claims["nonce"] = "another-nonce"
    elif invalid == "signature":
        oauth.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    elif invalid == "tenant":
        oauth.claims["tid"] = str(OWNER_ID)
    else:

        def fail(_client):
            """키 조회 장애를 합성한다.

            Args:
                _client: 공개키 조회기.

            Returns:
                None: 반환하지 않고 조회 장애를 발생시킨다.
            """
            raise jwt.PyJWKClientConnectionError("private-provider-details")

        monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fail)
        expected = "AUTH_PROVIDER_UNAVAILABLE"
    with pytest.raises(ApplicationError, match=expected):
        finish_mailbox(oauth, mailbox_attempt)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("refresh_token", None, "MAILBOX_RECONNECT_REQUIRED"),
        ("refresh_token", "", "MAILBOX_RECONNECT_REQUIRED"),
        ("scope", "Mail.Read", "MAILBOX_RECONNECT_REQUIRED"),
        ("access_token", "", "MAILBOX_TOKEN_UNAVAILABLE"),
        ("token_type", "PoP", "MAILBOX_TOKEN_UNAVAILABLE"),
        ("expires_in", 0, "MAILBOX_TOKEN_UNAVAILABLE"),
    ],
)
def test_initial_mailbox_incomplete_grant_is_not_returned(
    oauth, mailbox_attempt, field, value, expected
):
    """초기 access/refresh 자격과 동의 범위가 부족하면 연결 성공으로 숨기지 않는다.

    Args:
        oauth: 토큰 응답을 변경할 합성 공급자.
        mailbox_attempt: 정상 시작된 인가 시도.
        field: 변경할 공급자 응답 필드.
        value: 부족하거나 잘못된 합성 값.
        expected: 기대하는 고정 오류 코드.

    Returns:
        None: 사용할 수 없는 초기 캐시의 반환 차단을 검증한다.
    """
    oauth.response_overrides[field] = value
    with pytest.raises(ApplicationError, match=expected):
        finish_mailbox(oauth, mailbox_attempt)


@pytest.fixture
def persistence_context(mailbox_database, registered_user, settings):
    """실제 MSAL 연결 검사를 위한 전용 DB·합성 운영 세션·키를 준비한다.

    Args:
        mailbox_database: 한 번 보관 마이그레이션을 적용한 전용 DB.
        registered_user: 실제 식별자가 없는 합성 등록 사용자.
        settings: platform_app 권한의 전용 DB 설정.

    Returns:
        SimpleNamespace: 서버 소유자·세션·키와 이번 검사 자료만 정리할 문맥.
    """
    issued = asyncio.run(issue_session(settings, registered_user[0]))
    owner = registered_user[1]
    try:
        yield SimpleNamespace(
            settings=settings,
            owner=owner,
            token=issued.token.get_secret_value(),
            dsn=mailbox_database,
            cipher=MailboxCipher(
                key=SecretStr(b64encode(token_bytes(32)).decode()),
                key_version="test-persistence-v1",
            ),
        )
    finally:
        with psycopg.connect(mailbox_database) as conn:
            conn.execute("DELETE FROM mailbox_auth.connections WHERE owner_user_id=%s", (owner,))
            conn.execute(
                "DELETE FROM identity.oauth_authorizations WHERE owner_user_id=%s", (owner,)
            )


async def start_persistence_attempt(oauth, context):
    """실제 MSAL로 URL·서버 인가 저장을 실행하고 합성 Microsoft 응답을 준비한다.

    Args:
        oauth: 기존 실제 MSAL/RSA 및 합성 HTTP 문맥.
        context: 현재 서비스 세션과 실제 보관 검사 DB.

    Returns:
        list[tuple[str, str]]: 실제 생성한 state와 합성 code의 callback 입력.
    """
    now = datetime.now(UTC)
    uri = await start_mailbox_link(
        context.settings,
        session_token=context.token,
        browser_binding=BINDING,
        now=now,
        adapter=oauth.adapter,
    )
    query = parse_qs(urlsplit(uri).query)
    oauth.claims = {
        "iss": ISSUER,
        "aud": str(CLIENT_ID),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()) - 1,
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "nonce": query["nonce"][0],
        "tid": str(PERSONAL_TENANT),
        "oid": str(USER_ID),
        "sub": "synthetic-persistence-subject",
    }
    info = json.dumps({"uid": str(USER_ID), "utid": str(PERSONAL_TENANT)}).encode()
    oauth.response_overrides.update(
        client_info=urlsafe_b64encode(info).rstrip(b"=").decode(), scope="Mail.Read Mail.Send"
    )
    return [("state", query["state"][0]), ("code", "synthetic-code")]


async def finish_persistence(oauth, context, parameters):
    """실제 보관 연결 경계를 실행한다.

    Args:
        oauth: 실제 MSAL 어댑터와 합성 공급자 응답.
        context: 현재 세션·실제 DB·암호기.
        parameters: 중복 키를 보존한 callback 입력.

    Returns:
        MailboxConnectionReceipt: 암호화 저장이 커밋된 연결 확인 값.
    """
    return await complete_and_store_mailbox_link(
        context.settings,
        parameters,
        session_token=context.token,
        browser_binding=BINDING,
        now=datetime.now(UTC),
        adapter=oauth.adapter,
        cipher=context.cipher,
    )


def test_real_msal_callback_stores_ciphertext_and_returns_only_receipt(
    oauth, persistence_context, caplog
):
    """실제 MSAL·RSA·DB·AEAD가 연결되고 캐시 없는 확인 값만 반환되는지 확인한다.

    Args:
        oauth: 합성 HTTP 응답을 사용하는 실제 MSAL 문맥.
        persistence_context: 실제 PostgreSQL과 합성 세션·암호기.
        caplog: 실제 SDK DEBUG 기록의 민감 값 수집기.

    Returns:
        None: 전체 내부 연결·암호문·기존 세션 보존·재사용 차단을 검증한다.
    """
    caplog.set_level("DEBUG", logger="msal")
    context = persistence_context

    async def scenario():
        """URL부터 저장·복호화·캐시 조회까지 실제 내부 경계를 연결한다.

        Args:
            없음.

        Returns:
            None: 저장 커밋 후 확인 값과 검증된 캐시 호환을 확인한다.
        """
        before = await load_session(context.settings, context.token)
        parameters = await start_persistence_attempt(oauth, context)
        receipt = await finish_persistence(
            oauth, context, parameters + [("owner_user_id", str(USER_ID))]
        )
        assert set(asdict(receipt)) == {"mailbox_id", "revision"}
        assert receipt.revision == 1
        assert await load_session(context.settings, context.token) == before
        stored = await load_mailbox(
            context.settings,
            mailbox_id=receipt.mailbox_id,
            owner_user_id=context.owner,
            cipher=context.cipher,
        )
        assert stored.subject == MicrosoftSubject(PERSONAL_TENANT, USER_ID)
        assert stored.owner_user_id == context.owner and stored.subject.oid != context.owner
        assert "synthetic-refresh-token" in stored.serialized_cache.get_secret_value()
        token = await asyncio.to_thread(
            oauth.adapter.acquire_mailbox_token,
            subject=stored.subject,
            serialized_cache=stored.serialized_cache,
        )
        assert token.access_token.get_secret_value() == "synthetic-access-token"
        assert len([c for c in oauth.calls if c[0] == "POST"]) == 1
        with psycopg.connect(context.dsn) as conn:
            row = conn.execute(
                "SELECT cache_ciphertext,state,owner_user_id FROM mailbox_auth.connections WHERE id=%s",
                (receipt.mailbox_id,),
            ).fetchone()
        assert b"synthetic-refresh-token" not in bytes(row[0])
        assert row[1:] == ("connected", context.owner)
        for secret in (
            "synthetic-access-token",
            "synthetic-refresh-token",
            "synthetic-secret",
            oauth.response_overrides["client_info"],
            str(USER_ID),
            str(context.owner),
        ):
            assert secret not in repr(receipt) + caplog.text
        with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
            await finish_persistence(oauth, context, parameters)
        with psycopg.connect(context.dsn) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM mailbox_auth.connections WHERE owner_user_id=%s",
                    (context.owner,),
                ).fetchone()[0]
                == 1
            )
        assert len([c for c in oauth.calls if c[0] == "POST"]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["nonce", "signature"])
def test_unverified_callback_never_stores_cache(oauth, persistence_context, invalid):
    """검증 실패한 초기 캐시를 보관 성공으로 인계하지 않는다.

    Args:
        oauth: nonce·서명을 바꿀 합성 Microsoft 응답.
        persistence_context: 실제 PostgreSQL·세션·암호기.
        invalid: 잘못된 nonce 또는 위조 서명.

    Returns:
        None: 실제 교환 검증 실패와 보관 행 부재를 확인한다.
    """
    context = persistence_context

    async def scenario():
        """정상 시작 후 미검증 응답으로 저장 완료를 시도한다.

        Args:
            없음.

        Returns:
            None: 실패와 보관 행 부재를 확인한다.
        """
        parameters = await start_persistence_attempt(oauth, context)
        if invalid == "nonce":
            oauth.claims["nonce"] = "synthetic-wrong-nonce"
        else:
            oauth.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(ApplicationError, match="AUTH_INVALID_TOKEN"):
            await finish_persistence(oauth, context, parameters)
        with psycopg.connect(context.dsn) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM mailbox_auth.connections WHERE owner_user_id=%s",
                    (context.owner,),
                ).fetchone()[0]
                == 0
            )

    asyncio.run(scenario())


def test_storage_failure_after_oauth_is_not_success_or_reusable_attempt(oauth, persistence_context):
    """실제 INSERT 권한 실패를 고정503으로 전달하고 소비한 인가를 복구하지 않는다.

    Args:
        oauth: 정상 교환을 수행할 실제 MSAL 문맥.
        persistence_context: 권한 실패를 합성할 전용 DB·세션·암호기.

    Returns:
        None: 실제 SQL 실패·롤백·callback 재사용 차단을 검증한다.
    """
    context = persistence_context

    async def scenario():
        """인가 교환 이후 저장만 실패시킨 뒤 같은 callback을 다시 시도한다.

        Args:
            없음.

        Returns:
            None: 실패 뒤 보관 행과 두 번째 공급자 교환이 없는지 확인한다.
        """
        parameters = await start_persistence_attempt(oauth, context)
        with psycopg.connect(context.dsn) as conn:
            conn.execute("SET LOCAL ROLE platform_migrator")
            conn.execute("REVOKE INSERT ON mailbox_auth.connections FROM platform_app")
        try:
            with pytest.raises(ApplicationError, match="MAILBOX_TOKEN_UNAVAILABLE") as error:
                await finish_persistence(oauth, context, parameters)
            assert error.value.status_code == 503
        finally:
            with psycopg.connect(context.dsn) as conn:
                conn.execute("SET LOCAL ROLE platform_migrator")
                conn.execute("GRANT INSERT ON mailbox_auth.connections TO platform_app")
        with pytest.raises(ApplicationError, match="AUTH_INVALID_CALLBACK"):
            await finish_persistence(oauth, context, parameters)
        assert len([c for c in oauth.calls if c[0] == "POST"]) == 1
        with psycopg.connect(context.dsn) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM mailbox_auth.connections WHERE owner_user_id=%s",
                    (context.owner,),
                ).fetchone()[0]
                == 0
            )

    asyncio.run(scenario())


def test_session_revoked_between_callback_and_store_cannot_persist(
    oauth, persistence_context, monkeypatch
):
    """교환 후 세션 검사와 저장 사이의 철회도 저장 권한 재검사에서 차단한다.

    Args:
        oauth: 정상 합성 Microsoft 응답과 실제 MSAL.
        persistence_context: 실제 DB·세션·암호기.
        monkeypatch: 교환 후 저장 경계 직전 시점을 제어할 도구.

    Returns:
        None: 두 내부 경계 사이의 인증 상태 변화가 저장되지 않음을 확인한다.
    """
    from backend.api.platform.auth import mailbox

    context = persistence_context
    original = mailbox.create_mailbox

    async def revoke_then_store(settings, **kwargs):
        """현재 세션을 전용 DB에서 철회한 뒤 실제 저장을 호출한다.

        Args:
            settings: 현재 플랫폼 런타임 DB 설정.
            **kwargs: 검증된 초기 인계·세션·암호기.

        Returns:
            StoredMailbox: 실제 저장이 허용되는 경우에만 반환하는 내부 값.
        """
        with psycopg.connect(context.dsn) as conn:
            conn.execute("DELETE FROM identity.sessions WHERE user_id=%s", (context.owner,))
        return await original(settings, **kwargs)

    monkeypatch.setattr(mailbox, "create_mailbox", revoke_then_store)

    async def scenario():
        """정상 교환 후 철회된 세션으로 저장을 시도한다.

        Args:
            없음.

        Returns:
            None: 저장 거부와 행 부재를 확인한다.
        """
        parameters = await start_persistence_attempt(oauth, context)
        with pytest.raises(ApplicationError, match="MAILBOX_ACCESS_DENIED"):
            await finish_persistence(oauth, context, parameters)
        with psycopg.connect(context.dsn) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM mailbox_auth.connections WHERE owner_user_id=%s",
                    (context.owner,),
                ).fetchone()[0]
                == 0
            )

    asyncio.run(scenario())


async def persist_token_cache(context, mailbox_cache, *, expired=False):
    """실제 MSAL이 만든 합성 캐시를 현재 세션의 연결로 암호화 보관한다.

    Args:
        context: 전용 DB·현재 서비스 소유자·세션·암호기.
        mailbox_cache: 기존 실제 MSAL/RSA가 만든 합성 캐시.
        expired: access token 만료로 실제 MSAL 갱신을 유도할지 여부.

    Returns:
        StoredMailbox: 실제 DB에 저장한 연결 스냅샷.
    """
    return await create_mailbox(
        context.settings,
        session_token=context.token,
        authorization=MailboxAuthorization(
            context.owner,
            mailbox_cache.subject,
            expired_mailbox_cache(mailbox_cache) if expired else mailbox_cache.serialized,
        ),
        cipher=context.cipher,
    )


async def acquire_persisted_token(oauth, context, value, **overrides):
    """소비자용 제공자의 두 ID 호출로 실제 조회·갱신·저장 경계를 실행한다.

    Args:
        oauth: 실제 MSAL과 합성 HTTP를 사용하는 기존 어댑터.
        context: 전용 DB·신뢰할 소유자·암호기.
        value: 보관한 연결 ID.
        **overrides: 접근 거부 검사를 위한 ID 교체.

    Returns:
        SecretStr: 저장 또는 현재 상태 확인 뒤의 access token 하나.
    """
    provider = MailboxTokenProvider(
        settings=context.settings,
        adapter=oauth.adapter,
        cipher=context.cipher,
    )
    return await provider.get_mailbox_access_token(
        **{"mailbox_id": value.mailbox_id, "owner_user_id": context.owner, **overrides}
    )


def persisted_token_row(context, value):
    """전용 검사 DB에서 암호문·개정·상태와 수정 시각을 독립 조회한다.

    Args:
        context: 자체 PostgreSQL의 관리자 검사 연결.
        value: 이번 합성 보관 연결.

    Returns:
        tuple: 개정·암호문·키 버전·상태·수정 시각. 평문 비밀은 포함하지 않는다.
    """
    with psycopg.connect(context.dsn) as conn:
        return conn.execute(
            "SELECT revision,cache_ciphertext,key_version,state,updated_at "
            "FROM mailbox_auth.connections WHERE id=%s",
            (value.mailbox_id,),
        ).fetchone()


def test_stored_cache_hit_returns_only_token_without_rewriting(
    oauth, mailbox_cache, persistence_context
):
    """암호화 캐시 적중은 현재 상태 확인 뒤 토큰만 반환하고 DB를 다시 쓰지 않는다.

    Args:
        oauth: 실제 MSAL·합성 HTTP 문맥.
        mailbox_cache: 유효한 합성 MSAL 캐시.
        persistence_context: 실제 DB·세션·암호기.

    Returns:
        None: 반환형·캐시 적중·DB 불변을 확인한다.
    """

    async def scenario():
        """실제 보관 캐시를 읽고 갱신 POST나 저장 변경이 없는지 확인한다.

        Args:
            없음.

        Returns:
            None: 유효 토큰 하나와 불변 보관 행을 검증한다.
        """
        value = await persist_token_cache(persistence_context, mailbox_cache)
        before = persisted_token_row(persistence_context, value)
        posts = len([c for c in oauth.calls if c[0] == "POST"])
        token = await acquire_persisted_token(oauth, persistence_context, value)
        assert isinstance(token, SecretStr)
        assert token.get_secret_value() == "synthetic-access-token"
        assert len([c for c in oauth.calls if c[0] == "POST"]) == posts
        assert persisted_token_row(persistence_context, value) == before
        assert "synthetic-access-token" not in repr(token)

    asyncio.run(scenario())


def test_stored_refresh_commits_rotated_cache_before_return(
    oauth, mailbox_cache, persistence_context, monkeypatch, caplog
):
    """실제 갱신은 스레드에서 실행하고 새 캐시 커밋 후에만 access token을 반환한다.

    Args:
        oauth: 실제 MSAL과 합성 갱신 응답.
        mailbox_cache: 만료시킬 합성 캐시.
        persistence_context: 실제 DB·세션·암호기.
        monkeypatch: 스레드 경계를 관찰할 도구.
        caplog: 실제 MSAL DEBUG 기록 수집기.

    Returns:
        None: 회전 캐시 커밋·재조회·로그 비노출을 검증한다.
    """
    context = persistence_context
    caplog.set_level("DEBUG", logger="msal")
    original = oauth.adapter.acquire_mailbox_token
    main_thread = get_ident()

    def observed_acquire(**kwargs):
        """실제 SDK가 비동기 루프의 스레드를 막지 않는지 확인한다.

        Args:
            **kwargs: 검증된 사서함 식별자와 복호화 캐시.

        Returns:
            MailboxTokenUpdate: 기존 SDK의 실제 갱신 결과.
        """
        assert get_ident() != main_thread
        return original(**kwargs)

    monkeypatch.setattr(oauth.adapter, "acquire_mailbox_token", observed_acquire)
    oauth.response_overrides.update(
        access_token="synthetic-stored-access", refresh_token="synthetic-stored-refresh"
    )

    async def scenario():
        """만료 캐시 저장부터 갱신·커밋·두 번째 캐시 적중까지 실행한다.

        Args:
            없음.

        Returns:
            None: DB의 새 개정·회전 캐시와 반환 토큰을 대조한다.
        """
        value = await persist_token_cache(context, mailbox_cache, expired=True)
        posts = len([c for c in oauth.calls if c[0] == "POST"])
        token = await acquire_persisted_token(oauth, context, value)
        assert token.get_secret_value() == "synthetic-stored-access"
        assert persisted_token_row(context, value)[0] == 2
        stored = await load_mailbox(
            context.settings,
            mailbox_id=value.mailbox_id,
            owner_user_id=context.owner,
            cipher=context.cipher,
        )
        cache = stored.serialized_cache.get_secret_value()
        assert "synthetic-stored-refresh" in cache and "synthetic-refresh-token" not in cache
        assert b"synthetic-stored-refresh" not in bytes(persisted_token_row(context, value)[1])
        assert len([c for c in oauth.calls if c[0] == "POST"]) == posts + 1
        assert await acquire_persisted_token(oauth, context, value) == token
        assert len([c for c in oauth.calls if c[0] == "POST"]) == posts + 1
        for secret in (
            "synthetic-stored-access",
            "synthetic-stored-refresh",
            "synthetic-secret",
            str(USER_ID),
        ):
            assert secret not in repr(token) + caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize("denied", ["mailbox", "owner", "inactive", "role", "disconnected"])
def test_stored_token_denied_before_sdk(oauth, mailbox_cache, persistence_context, denied):
    """현재 소유자·권한·연결이 유효하지 않으면 SDK에 복호화 캐시를 전달하지 않는다.

    Args:
        oauth: 실제 MSAL과 합성 요청 기록.
        mailbox_cache: 갱신이 필요한 합성 캐시.
        persistence_context: 실제 DB·세션·암호기.
        denied: 거부할 ID·활성·역할·연결 조건.

    Returns:
        None: 공급자 POST 전에 고정 오류로 거부하고 보관을 바꾸지 않는다.
    """
    context = persistence_context

    async def scenario():
        """현재 접근 조건을 바꾼 뒤 내부 토큰 제공을 시도한다.

        Args:
            없음.

        Returns:
            None: 공급자 미호출과 안전한 접근 거부를 확인한다.
        """
        value = await persist_token_cache(context, mailbox_cache, expired=True)
        overrides = {}
        if denied in {"mailbox", "owner"}:
            overrides["mailbox_id" if denied == "mailbox" else "owner_user_id"] = uuid4()
        elif denied == "disconnected":
            await disconnect_mailbox(
                context.settings, mailbox_id=value.mailbox_id, session_token=context.token
            )
        else:
            with psycopg.connect(context.dsn) as conn:
                if denied == "inactive":
                    conn.execute(
                        "UPDATE identity.users SET is_active=false WHERE id=%s", (context.owner,)
                    )
                else:
                    conn.execute(
                        "DELETE FROM identity.user_roles WHERE user_id=%s", (context.owner,)
                    )
        before = persisted_token_row(context, value)
        posts = len([c for c in oauth.calls if c[0] == "POST"])
        expected = "MAILBOX_NOT_CONNECTED" if denied == "disconnected" else "MAILBOX_ACCESS_DENIED"
        with pytest.raises(ApplicationError, match=expected):
            await acquire_persisted_token(oauth, context, value, **overrides)
        assert len([c for c in oauth.calls if c[0] == "POST"]) == posts
        assert persisted_token_row(context, value) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("change", ["inactive", "role", "disconnect", "reconnect"])
def test_stored_token_rechecks_current_state_after_sdk(
    oauth, mailbox_cache, persistence_context, monkeypatch, expired, change
):
    """캐시 적중과 갱신 모두 반환 전에 현재 권한·연결·개정 변화를 거부한다.

    Args:
        oauth: 실제 MSAL과 합성 공급자 응답.
        mailbox_cache: 유효 또는 만료시킬 합성 캐시.
        persistence_context: 실제 DB·세션·암호기.
        monkeypatch: 실제 SDK 완료와 저장 사이의 시점을 제어할 도구.
        expired: 실제 refresh POST를 실행할지 여부.
        change: 제공 중 변경할 권한·연결 상태.

    Returns:
        None: 토큰 미반환·현재 보관 보호와 자동 재시도 부재를 검증한다.
    """
    context = persistence_context
    original = oauth.adapter.acquire_mailbox_token
    changed_rows = []
    oauth.response_overrides.update(
        access_token="synthetic-stale-access", refresh_token="synthetic-stale-refresh"
    )

    async def scenario():
        """실제 SDK가 끝난 직후 상태를 바꿔 반환 전 재검사를 확인한다.

        Args:
            없음.

        Returns:
            None: 캐시 변경 여부와 무관하게 안전하게 실패하는지 확인한다.
        """
        value = await persist_token_cache(context, mailbox_cache, expired=expired)
        posts = len([c for c in oauth.calls if c[0] == "POST"])

        def changed_acquire(**kwargs):
            """SDK 호출 뒤 실제 DB 상태를 변경하며 네트워크 중 잠금 부재도 확인한다.

            Args:
                **kwargs: 기존 어댑터에 전달할 캐시·식별자.

            Returns:
                MailboxTokenUpdate: 변경된 현재 상태에 대해 재검사가 필요한 SDK 결과.
            """
            result = original(**kwargs)
            if not expired:
                assert not result.cache_changed
            if change in {"inactive", "role"}:
                with psycopg.connect(context.dsn) as conn:
                    conn.execute("SET LOCAL lock_timeout='1s'")
                    if change == "inactive":
                        conn.execute(
                            "UPDATE identity.users SET is_active=false WHERE id=%s",
                            (context.owner,),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM identity.user_roles WHERE user_id=%s", (context.owner,)
                        )
            elif change == "disconnect":
                asyncio.run(
                    disconnect_mailbox(
                        context.settings, mailbox_id=value.mailbox_id, session_token=context.token
                    )
                )
            else:
                asyncio.run(
                    reconnect_mailbox(
                        context.settings,
                        mailbox_id=value.mailbox_id,
                        expected_revision=value.revision,
                        session_token=context.token,
                        cipher=context.cipher,
                        authorization=MailboxAuthorization(
                            context.owner, mailbox_cache.subject, mailbox_cache.serialized
                        ),
                    )
                )
            changed_rows.append(persisted_token_row(context, value))
            return result

        monkeypatch.setattr(oauth.adapter, "acquire_mailbox_token", changed_acquire)
        expected = {
            "inactive": "MAILBOX_ACCESS_DENIED",
            "role": "MAILBOX_ACCESS_DENIED",
            "disconnect": "MAILBOX_NOT_CONNECTED",
            "reconnect": "MAILBOX_TOKEN_UNAVAILABLE",
        }[change]
        with pytest.raises(ApplicationError, match=expected) as error:
            await acquire_persisted_token(oauth, context, value)
        assert error.value.code != "MAILBOX_VERSION_CONFLICT"
        assert persisted_token_row(context, value) == changed_rows[0]
        assert len([c for c in oauth.calls if c[0] == "POST"]) == posts + int(expired)

    asyncio.run(scenario())


def test_stored_refresh_sql_failure_never_returns_new_token(
    oauth, mailbox_cache, persistence_context, monkeypatch
):
    """성공한 실제 갱신 뒤 저장 권한 실패는 새 토큰 미반환·기존 암호문 보존으로 끝난다.

    Args:
        oauth: 실제 SDK와 합성 공급자 응답.
        mailbox_cache: 갱신이 필요한 합성 캐시.
        persistence_context: 권한 실패를 만드는 자체 DB.
        monkeypatch: SDK 완료 후 SQL 실패 시점을 제어할 도구.

    Returns:
        None: 실제 SQL 실패·롤백·자동 재시도 부재를 확인한다.
    """
    context = persistence_context
    original = oauth.adapter.acquire_mailbox_token

    def denied_update(**kwargs):
        """실제 갱신 후 이번 전용 스키마의 UPDATE 권한만 잠시 제거한다.

        Args:
            **kwargs: 원래 SDK 인자.

        Returns:
            MailboxTokenUpdate: 저장에 실패해야 하는 실제 SDK 결과.
        """
        result = original(**kwargs)
        assert result.cache_changed
        with psycopg.connect(context.dsn) as conn:
            conn.execute("SET LOCAL ROLE platform_migrator")
            conn.execute("REVOKE UPDATE ON mailbox_auth.connections FROM platform_app")
        return result

    monkeypatch.setattr(oauth.adapter, "acquire_mailbox_token", denied_update)

    async def scenario():
        """실제 공급자 성공 뒤 DB 실패를 처리하고 권한을 반드시 복구한다.

        Args:
            없음.

        Returns:
            None: 저장 실패의 고정503과 DB 불변을 확인한다.
        """
        value = await persist_token_cache(context, mailbox_cache, expired=True)
        before = persisted_token_row(context, value)
        posts = len([c for c in oauth.calls if c[0] == "POST"])
        try:
            with pytest.raises(ApplicationError, match="MAILBOX_TOKEN_UNAVAILABLE") as error:
                await acquire_persisted_token(oauth, context, value)
            assert error.value.status_code == 503
        finally:
            with psycopg.connect(context.dsn) as conn:
                conn.execute("SET LOCAL ROLE platform_migrator")
                conn.execute("GRANT UPDATE ON mailbox_auth.connections TO platform_app")
        assert persisted_token_row(context, value) == before
        assert len([c for c in oauth.calls if c[0] == "POST"]) == posts + 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "provider_error,expected",
    [
        ("invalid_grant", "MAILBOX_RECONNECT_REQUIRED"),
        ("temporarily_unavailable", "MAILBOX_TOKEN_UNAVAILABLE"),
    ],
)
def test_stored_provider_failure_does_not_save_or_return_token(
    oauth, mailbox_cache, persistence_context, provider_error, expected
):
    """실제 SDK의 재연결·일시 장애 분류를 보존하고 실패 캐시를 저장하지 않는다.

    Args:
        oauth: 합성 Microsoft 오류 응답을 사용하는 실제 MSAL.
        mailbox_cache: 갱신이 필요한 합성 캐시.
        persistence_context: 실제 DB·세션·암호기.
        provider_error: 재연결 또는 일시 장애 공급자 코드.
        expected: 기존 어댑터의 고정 오류 코드.

    Returns:
        None: 고정 오류 전달·기존 보관 보존·자동 재시도 부재를 확인한다.
    """
    context = persistence_context
    oauth.token_response = {"error": provider_error, "error_description": "synthetic-refresh-token"}
    oauth.response_status = 400

    async def scenario():
        """기존 만료 캐시에 대해 SDK 오류를 전달한다.

        Args:
            없음.

        Returns:
            None: 실패를 성공·캐시 갱신으로 숨기지 않는지 확인한다.
        """
        value = await persist_token_cache(context, mailbox_cache, expired=True)
        before = persisted_token_row(context, value)
        posts = len([c for c in oauth.calls if c[0] == "POST"])
        with pytest.raises(ApplicationError, match=expected) as error:
            await acquire_persisted_token(oauth, context, value)
        assert "synthetic-refresh-token" not in error.value.message
        assert persisted_token_row(context, value) == before
        assert len([c for c in oauth.calls if c[0] == "POST"]) == posts + 1

    asyncio.run(scenario())


def test_consumer_database_failure_is_not_access_denied(oauth, persistence_context):
    """소비자 호출에서도 DB 장애를 접근 거부·빈 토큰으로 숨기지 않는다.

    Args:
        oauth: 생성 과정에서 네트워크를 호출하지 않는 검사 어댑터.
        persistence_context: 전용 DB 문맥의 서버 설정과 실제 암호기.

    Returns:
        None: 고정503·비밀 비노출·공급자 미호출을 검증한다.
    """
    context = persistence_context
    settings = context.settings.model_copy(
        update={
            "database_url": SecretStr(
                "postgresql://postgres:synthetic-db-secret@127.0.0.1:1/kan77_auth_test"
            )
        }
    )
    provider = MailboxTokenProvider(settings=settings, adapter=oauth.adapter, cipher=context.cipher)
    calls = list(oauth.calls)
    with pytest.raises(ApplicationError, match="MAILBOX_TOKEN_UNAVAILABLE") as error:
        asyncio.run(
            provider.get_mailbox_access_token(mailbox_id=uuid4(), owner_user_id=context.owner)
        )
    assert error.value.status_code == 503
    assert "synthetic-db-secret" not in error.value.message + repr(error.value) + repr(provider)
    assert oauth.calls == calls


def test_concurrent_stored_refresh_only_returns_committed_winner(
    oauth, mailbox_cache, persistence_context, monkeypatch
):
    """실제 동시 갱신 두 개 중 커밋된 캐시의 토큰만 반환하고 패자는503으로 종료한다.

    Args:
        oauth: 실제 MSAL·합성 HTTP 문맥.
        mailbox_cache: 두 작업이 읽을 같은 만료 캐시.
        persistence_context: 실제 DB·세션·암호기.
        monkeypatch: 두 실제 HTTP 응답이 겹치도록 제어할 도구.

    Returns:
        None: 실제 동시 저장·개정 충돌·캐시 승자·재시도 부재를 확인한다.
    """
    context = persistence_context
    barrier = Barrier(2)
    original = requests.Session.request

    def overlapping_refresh(session, method, url, **kwargs):
        """두 합성 토큰 응답을 서로 다른 회전 값으로 동시에 반환한다.

        Args:
            session: 실제 MSAL requests 세션.
            method: discovery 또는 갱신 메서드.
            url: 고정 Microsoft 주소.
            **kwargs: 실제 SDK 요청 인자.

        Returns:
            Response: 서로 다른 합성 access/refresh 값을 가진 응답.
        """
        response = original(session, method, url, **kwargs)
        if method.upper() == "POST":
            index = barrier.wait(timeout=10)
            body = response.json()
            body.update(
                access_token=f"synthetic-race-access-{index}",
                refresh_token=f"synthetic-race-refresh-{index}",
            )
            response._content = json.dumps(body).encode()
        return response

    async def scenario():
        """같은 개정의 만료 캐시에서 두 실제 MSAL 갱신을 병렬 실행한다.

        Args:
            없음.

        Returns:
            None: 반환 토큰이 실제 저장 승자의 캐시와 같은지 확인한다.
        """
        value = await persist_token_cache(context, mailbox_cache, expired=True)
        posts = len([c for c in oauth.calls if c[0] == "POST"])
        monkeypatch.setattr(requests.Session, "request", overlapping_refresh)
        results = await asyncio.gather(
            acquire_persisted_token(oauth, context, value),
            acquire_persisted_token(oauth, context, value),
            return_exceptions=True,
        )
        tokens = [v for v in results if isinstance(v, SecretStr)]
        errors = [v for v in results if isinstance(v, ApplicationError)]
        assert len(tokens) == len(errors) == 1
        assert errors[0].code == "MAILBOX_TOKEN_UNAVAILABLE" and errors[0].status_code == 503
        assert len([c for c in oauth.calls if c[0] == "POST"]) == posts + 2
        stored = await load_mailbox(
            context.settings,
            mailbox_id=value.mailbox_id,
            owner_user_id=context.owner,
            cipher=context.cipher,
        )
        cache = stored.serialized_cache.get_secret_value()
        winner = tokens[0].get_secret_value().removeprefix("synthetic-race-access-")
        assert stored.revision == 2
        assert tokens[0].get_secret_value() in cache and f"synthetic-race-refresh-{winner}" in cache
        assert f"synthetic-race-refresh-{1 - int(winner)}" not in cache
        assert "synthetic-refresh-token" not in cache

    asyncio.run(scenario())


def test_http_mailbox_flow_uses_real_msal_and_commits_ciphertext(oauth, persistence_context):
    """실제 HTTP·MSAL·RSA·암호화 DB 연결이 기존 로그인 세션을 보존하는지 확인한다.

    Args:
        oauth: 실제 SDK와 합성 Microsoft HTTP/JWKS 응답.
        persistence_context: 현재 합성 로그인과 전용 PostgreSQL.

    Returns:
        None: 별도 callback·일회 교환·암호화 커밋·캐시 토큰 호환을 확인한다.
    """
    origin = "https://app.example.invalid"
    prefix = "/api/v1/platform/auth/mailbox"
    context = persistence_context
    auth = MicrosoftAuthSettings(
        _env_file=None,
        enabled=True,
        client_id=CLIENT_ID,
        client_secret=SecretStr("synthetic-secret"),
        app_origin=origin,
        redirect_uri=origin + CALLBACK_PATH,
    )
    mailbox = MailboxAuthSettings(
        _env_file=None,
        enabled=True,
        encryption_key=SecretStr(b64encode(token_bytes(32)).decode()),
        key_version="test-real-http-v1",
        redirect_uri=origin + MAILBOX_CALLBACK,
    )
    app = create_app(context.settings, auth, mailbox_settings=mailbox)
    with TestClient(app, base_url=origin, follow_redirects=False) as client:
        client.cookies.set(SESSION_COOKIE, context.token)
        started = client.post(
            prefix + "/start", headers={"Origin": origin, CSRF_HEADER: csrf_token(context.token)}
        )
        assert started.status_code == 200
        query = parse_qs(urlsplit(started.json()["data"]["authorization_url"]).query)
        assert query["redirect_uri"] == [origin + MAILBOX_CALLBACK]
        assert query["response_mode"] == ["form_post"]
        now = datetime.now(UTC)
        oauth.claims = {
            "iss": ISSUER,
            "aud": str(CLIENT_ID),
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()) - 1,
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "nonce": query["nonce"][0],
            "tid": str(PERSONAL_TENANT),
            "oid": str(USER_ID),
            "sub": "synthetic-http-subject",
        }
        info = json.dumps({"uid": str(USER_ID), "utid": str(PERSONAL_TENANT)}).encode()
        oauth.response_overrides.update(
            client_info=urlsafe_b64encode(info).rstrip(b"=").decode(), scope="Mail.Read Mail.Send"
        )
        body = "state=" + query["state"][0] + "&code=synthetic-code"
        client.cookies.delete(SESSION_COOKIE)
        relay = client.post(
            prefix + "/callback",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert relay.status_code == 200
        assert not [c for c in oauth.calls if c[0] == "POST"]
        client.cookies.set(SESSION_COOKIE, context.token)
        completed = client.post(
            prefix + "/complete",
            content=body,
            headers={"Origin": origin, "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert completed.status_code == 303 and completed.headers["location"] == origin + "/"
        posts = [c for c in oauth.calls if c[0] == "POST"]
        assert len(posts) == 1 and posts[0][2]["data"]["redirect_uri"] == origin + MAILBOX_CALLBACK
        with psycopg.connect(context.dsn) as conn:
            row = conn.execute(
                "SELECT id,cache_ciphertext FROM mailbox_auth.connections WHERE owner_user_id=%s",
                (context.owner,),
            ).fetchone()
            assert b"synthetic-refresh-token" not in bytes(row[1])
        get_token = app.state.mailbox_tokens.get_mailbox_access_token
        token = asyncio.run(get_token(mailbox_id=row[0], owner_user_id=context.owner))
        assert token.get_secret_value() == "synthetic-access-token"
        assert len([c for c in oauth.calls if c[0] == "POST"]) == 1
        assert asyncio.run(load_session(context.settings, context.token)).id == context.owner
        assert client.cookies.get(SESSION_COOKIE) == context.token
        assert "synthetic-refresh-token" not in completed.text + relay.text
        disconnected = client.post(
            prefix + f"/{row[0]}/disconnect",
            headers={"Origin": origin, CSRF_HEADER: csrf_token(context.token)},
        )
        assert disconnected.status_code == 204
        with pytest.raises(ApplicationError, match="MAILBOX_NOT_CONNECTED") as error:
            asyncio.run(get_token(mailbox_id=row[0], owner_user_id=context.owner))
        assert error.value.status_code == 409
        assert len([c for c in oauth.calls if c[0] == "POST"]) == 1


@pytest.mark.parametrize("refresh_error", [None, "invalid_grant"])
def test_real_provider_feeds_kan71_mail_sync(
    oauth, mailbox_cache, persistence_context, mail_database, monkeypatch, tmp_path, refresh_error
):
    """KAN-71 수집이 실제 제공자 메서드로 토큰을 받고 실패 코드를 기록하는지 대조한다.

    Graph는 KAN-71의 가짜 응답이며 실제 Outlook·Graph 연동이나 소비자 합의의 증거가 아니다.

    Args:
        oauth: 실제 MSAL·합성 Microsoft HTTP 문맥.
        mailbox_cache: 실제 MSAL이 만든 합성 캐시.
        persistence_context: 실제 DB·세션·암호기.
        mail_database: KAN-71 수집 마이그레이션을 적용한 같은 전용 DB.
        monkeypatch: 가짜 Graph가 기대하는 합성 토큰 값을 제공자 값으로 맞추는 도구.
        tmp_path: 원문 임시 저장소.
        refresh_error: 갱신 시 공급자 오류. 없으면 유효 캐시를 그대로 사용한다.

    Returns:
        None: 토큰 전달·실패 기록·비밀 비노출·보관 행 불변을 확인한다.
    """
    context = persistence_context
    monkeypatch.setattr(mail_sync_db, "TOKEN", "synthetic-access-token")
    if refresh_error:
        oauth.token_response = {
            "error": refresh_error,
            "error_description": "synthetic-refresh-token",
        }
        oauth.response_status = 400
    value = asyncio.run(persist_token_cache(context, mailbox_cache, expired=bool(refresh_error)))
    before = persisted_token_row(context, value)
    graph = mail_sync_db.FakeGraph()
    provider = MailboxTokenProvider(
        settings=context.settings, adapter=oauth.adapter, cipher=context.cipher
    )
    mailbox = {"id": value.mailbox_id, "owner": context.owner}
    try:
        result = mail_sync_db.run(
            context.settings,
            mailbox,
            graph,
            LocalObjectStore(tmp_path),
            provider.get_mailbox_access_token,
        )
        runs = mail_sync_db.rows(
            context.dsn,
            "SELECT status,error_code,mail_sync_runs::text FROM integration.mail_sync_runs "
            "WHERE mailbox_id=%s",
            value.mailbox_id,
        )
        stored = (
            mail_sync_db.rows(
                context.dsn,
                "SELECT string_agg(m::text,'') FROM integration.mail_messages m WHERE mailbox_id=%s",
                value.mailbox_id,
            )[0][0]
            or ""
        )
        if refresh_error:
            assert (result.status, result.error_code) == ("FAILED", "MAILBOX_RECONNECT_REQUIRED")
            assert runs[0][:2] == ("FAILED", "MAILBOX_RECONNECT_REQUIRED")
            assert graph.requests == [] and stored == ""
        else:
            assert (result.status, result.error_code) == ("SUCCEEDED", None)
            assert runs[0][:2] == ("SUCCEEDED", None) and graph.requests
        assert len(runs) == 1
        for secret in ("synthetic-access-token", "synthetic-refresh-token"):
            assert secret not in runs[0][2] + stored
        assert persisted_token_row(context, value) == before
    finally:
        with psycopg.connect(context.dsn) as conn:
            conn.execute(
                "DELETE FROM integration.mail_attachments WHERE message_id IN "
                "(SELECT id FROM integration.mail_messages WHERE mailbox_id=%s)",
                (value.mailbox_id,),
            )
            for table in ("mail_messages", "mail_sync_cursors", "mail_sync_runs"):
                conn.execute(
                    psycopg.sql.SQL("DELETE FROM integration.{} WHERE mailbox_id=%s").format(
                        psycopg.sql.Identifier(table)
                    ),
                    (value.mailbox_id,),
                )
