"""실제 DB와 fake OAuth로 로그인 HTTP·쿠키·CSRF 경계를 검증한다."""

import logging
from datetime import timedelta
from hashlib import sha256
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.api.platform.auth import http
from backend.api.platform.auth.callback import OAuthPurpose, PendingAuthorization
from backend.api.platform.auth.http import (
    BINDING_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    AuthAccessLogFilter,
)
from backend.api.platform.auth.settings import CALLBACK_PATH, MicrosoftAuthSettings
from backend.api.platform.main import create_app
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

ORIGIN = "https://app.example.invalid"
LOGIN = "/api/v1/platform/auth/login"
ME = "/api/v1/platform/me"
LOGOUT = "/api/v1/platform/auth/logout"


def configuration(**changes):
    """실제 앱 자격증명을 사용하지 않는 HTTPS 로그인 설정을 생성한다.

    Args:
        **changes: 잘못된 설정을 검사하기 위해 덮어쓸 값.

    Returns:
        MicrosoftAuthSettings: 합성 앱의 명시적 활성 설정.
    """
    return MicrosoftAuthSettings(
        _env_file=None,
        **(
            {
                "enabled": True,
                "client_id": UUID(int=1),
                "client_secret": SecretStr("synthetic-secret"),
                "app_origin": ORIGIN,
                "redirect_uri": ORIGIN + CALLBACK_PATH,
            }
            | changes
        ),
    )


class FakeOAuth:
    """실제 공급자 통신 없이 기존 어댑터 경계의 시작·교환만 대체한다."""

    def __init__(self, subject):
        """교환할 식별자와 인가 시도·호출 기록을 준비한다.

        Args:
            subject: 검사 DB에 등록된 합성 Microsoft 식별자.

        Returns:
            None: 합성 공급자를 초기화한다.
        """
        self.subject = subject
        self.attempts = []
        self.calls = []
        self.error = None

    def begin(self, purpose, *, browser_binding, now, owner_user_id=None):
        """서버에 저장할 합성 flow를 생성한다.

        Args:
            purpose: HTTP 경계가 고정한 로그인 목적.
            browser_binding: HTTP 경계가 생성한 브라우저 결속값.
            now: 인가 시도 시작 시각.
            owner_user_id: 로그인은 None인 기존 사용자 소유자.

        Returns:
            tuple[str, PendingAuthorization]: 합성 Microsoft URL과 서버 전용 인가 시도.
        """
        assert purpose is OAuthPurpose.LOGIN and owner_user_id is None
        pending = PendingAuthorization(
            purpose=purpose,
            state=str(uuid4()),
            browser_binding_hash=sha256(browser_binding.encode()).hexdigest(),
            expires_at=now + timedelta(minutes=5),
            provider_flow={"nonce": "private-nonce", "code_verifier": "private-verifier"},
        )
        self.attempts.append(pending)
        return "https://login.microsoftonline.com/consumers/authorize", pending

    def redeem(self, pending, callback):
        """호출을 기록하고 검증된 식별자 또는 공급자 오류를 반환한다.

        Args:
            pending: 실제 DB에서 한 번 소비한 인가 시도.
            callback: 기존 검증 경계가 남긴 state·code만 있는 입력.

        Returns:
            MicrosoftSubject: 합성 공급자의 검증 결과.
        """
        self.calls.append(callback)
        if self.error:
            raise self.error
        return self.subject


@pytest.fixture
def browser(settings, registered_user, auth_database):
    """플랫폼 런타임 권한 DB와 HTTPS 클라이언트·fake OAuth를 준비한다.

    Args:
        settings: 전용 DB의 플랫폼 실행 권한 설정.
        registered_user: 합성 Microsoft 식별자와 사용자 ID.
        auth_database: 해당 검사에서 만든 인가 시도만 정리할 DB 관리자 연결.

    Returns:
        tuple[TestClient, FakeOAuth, UUID]: 클라이언트·공급자·등록 사용자 ID.
    """
    subject, user_id = registered_user
    app = create_app(settings, auth_settings=configuration())
    provider = FakeOAuth(subject)
    app.state.microsoft_oauth = provider
    try:
        with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
            yield client, provider, user_id
    finally:
        with psycopg.connect(auth_database) as conn:
            for pending in provider.attempts:
                conn.execute(
                    "DELETE FROM identity.oauth_authorizations WHERE state_hash=%s",
                    (sha256(pending.state.encode()).hexdigest(),),
                )


def post_callback(client, provider, parameters=None, **options):
    """마지막 인가 시도의 합성 form_post를 보낸다.

    Args:
        client: HTTPS 검사 클라이언트.
        provider: 시작한 인가 시도를 보관한 fake 공급자.
        parameters: 중복 키 검사 등을 위한 query 형식 목록. 없으면 정상 state·code.
        **options: 쿠키 또는 URL 검사를 위한 요청 인자.

    Returns:
        Response: callback 처리 결과.
    """
    parameters = (
        parameters
        if parameters is not None
        else [
            ("state", provider.attempts[-1].state),
            ("code", "synthetic-code"),
        ]
    )
    headers = {"Content-Type": "application/x-www-form-urlencoded"} | options.pop("headers", {})
    return client.post(CALLBACK_PATH, content=urlencode(parameters), headers=headers, **options)


def test_default_configuration_disables_operational_auth_before_db_access():
    """기본 비활성 상태에서 로그인 경로와 개발 쿠키의 운영 인증을 거부하는지 확인한다.

    Args:
        없음.

    Returns:
        None: 설정 미완료를 성공으로 응답하거나 개발 쿠키를 수용하지 않음을 검증한다.
    """
    app = create_app(
        Settings(_env_file=None, environment="production"),
        auth_settings=MicrosoftAuthSettings(_env_file=None),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        for method, path in (("GET", LOGIN), ("POST", CALLBACK_PATH), ("POST", LOGOUT)):
            response = client.request(method, path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"
            assert response.headers["Cache-Control"] == "no-store"
            assert "set-cookie" not in response.headers
        assert (
            client.get(
                ME, headers={"Cookie": "team4_dev_session=forged", "X-User-Role": "manager"}
            ).status_code
            == 401
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"client_id": None},
        {"client_secret": SecretStr("")},
        {
            "app_origin": "http://localhost:5173",
            "redirect_uri": "http://localhost:5173" + CALLBACK_PATH,
        },
        {"redirect_uri": "https://other.invalid" + CALLBACK_PATH},
        {"app_origin": ORIGIN + "/path"},
        {"app_origin": "https://app.example.invalid:bad"},
    ],
)
def test_enabled_login_requires_complete_same_origin_https_configuration(changes):
    """활성화된 로그인에 필요한 HTTPS·앱 자격증명·고정 callback 설정을 검증한다.

    Args:
        changes: 거부할 설정 값.

    Returns:
        None: 잘못된 설정을 시작 시 거부하고 비밀값을 오류에 넣지 않음을 확인한다.
    """
    with pytest.raises(ValueError) as rejected:
        create_app(
            Settings(_env_file=None, environment="test"), auth_settings=configuration(**changes)
        )
    assert "synthetic-secret" not in str(rejected.value)


def test_operational_and_development_auth_cannot_be_enabled_together():
    """실제 로그인과 개발 계정 선택의 모호한 동시 사용을 거부하는지 확인한다.

    Args:
        없음.

    Returns:
        None: 두 모드가 동시에 활성화될 수 없음을 검증한다.
    """
    with pytest.raises(ValueError):
        create_app(
            Settings(_env_file=None, environment="test", dev_auth_enabled=True),
            auth_settings=configuration(),
        )


def test_auth_access_log_removes_callback_query_secrets():
    """잘못된 GET callback의 code·state도 표준 접근 로그에서 제거하는지 확인한다.

    Args:
        없음.

    Returns:
        None: 기록할 경로에는 query가 남지 않음을 검증한다.
    """
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", CALLBACK_PATH + "?code=private-code&state=private-state", "1.1", 405),
        None,
    )
    assert AuthAccessLogFilter().filter(record)
    message = record.getMessage()
    assert (
        CALLBACK_PATH in message
        and "private-code" not in message
        and "private-state" not in message
    )


def test_http_login_me_logout_and_replay_with_real_database(browser):
    """로그인부터 세션 조회·CSRF 발급·철회·재사용 차단까지 실제 DB로 검증한다.

    Args:
        browser: HTTPS 클라이언트·fake 공급자·등록 사용자 ID.

    Returns:
        None: 등록 사용자와 DB 역할만 반환하고 철회된 토큰을 거부함을 확인한다.
    """
    client, provider, user_id = browser
    assert (
        client.get(
            ME, headers={"Cookie": "team4_dev_session=forged", "X-User-Role": "manager"}
        ).status_code
        == 401
    )
    start = client.get(LOGIN)
    assert start.status_code == 302
    binding = start.headers["set-cookie"]
    assert all(
        value in binding.lower()
        for value in ["secure", "httponly", "samesite=none", "path=/", "max-age=300"]
    )
    assert "domain=" not in binding.lower()
    parameters = [
        ("state", provider.attempts[-1].state),
        ("code", "synthetic-code"),
        ("roles", "manager"),
        ("user_id", str(uuid4())),
    ]
    callback = post_callback(client, provider, parameters)
    assert callback.status_code == 303 and callback.headers["Location"] == ORIGIN + "/"
    token = client.cookies.get(SESSION_COOKIE)
    assert token and client.cookies.get(BINDING_COOKIE) is None
    cookie = next(
        value
        for value in callback.headers.get_list("set-cookie")
        if value.startswith(SESSION_COOKIE + "=")
    )
    assert all(
        value in cookie.lower()
        for value in ["secure", "httponly", "samesite=lax", "path=/", "max-age=28800"]
    )
    assert "domain=" not in cookie.lower()
    me = client.get(ME)
    assert me.json()["data"]["id"] == str(user_id) and me.json()["data"]["roles"] == ["cs_agent"]
    csrf = me.headers[CSRF_HEADER]
    assert csrf != token and token not in me.text
    assert (
        me.headers["Cache-Control"] == "no-store" and me.headers["Referrer-Policy"] == "no-referrer"
    )
    assert provider.calls == [{"state": provider.attempts[-1].state, "code": "synthetic-code"}]
    assert client.post(LOGOUT, headers={"Origin": ORIGIN, CSRF_HEADER: csrf}).status_code == 204
    assert client.get(ME, headers={"Cookie": f"{SESSION_COOKIE}={token}"}).status_code == 401
    assert client.post(LOGOUT, headers={"Origin": ORIGIN}).status_code == 204
    assert client.get(LOGOUT).status_code == 405


def test_http_second_login_rotates_session_and_csrf(browser):
    """교차 사이트 POST에 Lax 쿠키가 빠져도 기존 세션·CSRF가 교체되는지 확인한다.

    Args:
        browser: HTTPS 클라이언트·fake 공급자·등록 사용자 ID.

    Returns:
        None: 이전 세션과 CSRF 거부 및 새 세션 보존을 확인한다.
    """
    client, provider, _ = browser
    client.get(LOGIN)
    assert post_callback(client, provider).status_code == 303
    old_token, old_csrf = client.cookies.get(SESSION_COOKIE), client.get(ME).headers[CSRF_HEADER]
    client.get(LOGIN)
    binding = client.cookies.get(BINDING_COOKIE)
    assert old_token not in binding
    assert binding.partition(".")[2] == sha256(old_token.encode()).hexdigest()
    assert (
        post_callback(
            client, provider, headers={"Cookie": f"{BINDING_COOKIE}={binding}"}
        ).status_code
        == 303
    )
    assert client.cookies.get(SESSION_COOKIE) != old_token
    assert client.get(ME).headers[CSRF_HEADER] != old_csrf
    assert client.get(ME, headers={"Cookie": f"{SESSION_COOKIE}={old_token}"}).status_code == 401
    assert client.post(LOGOUT, headers={"Origin": ORIGIN, CSRF_HEADER: old_csrf}).status_code == 403
    assert client.get(ME).status_code == 200


def test_http_tampered_rotation_binding_preserves_old_session(browser):
    """기존 세션 hash를 변조한 결속 쿠키로 세션을 철회하거나 발급할 수 없는지 확인한다.

    Args:
        browser: HTTPS 클라이언트·fake 공급자·등록 사용자 ID.

    Returns:
        None: 결속 위조 거부와 정상 기존 세션·인가 시도 보존을 확인한다.
    """
    client, provider, _ = browser
    client.get(LOGIN)
    assert post_callback(client, provider).status_code == 303
    old_token = client.cookies.get(SESSION_COOKIE)
    client.get(LOGIN)
    binding = client.cookies.get(BINDING_COOKIE)
    tampered = binding.partition(".")[0] + "." + "0" * 64
    response = post_callback(client, provider, headers={"Cookie": f"{BINDING_COOKIE}={tampered}"})
    assert response.status_code == 400 and "set-cookie" not in response.headers
    assert client.get(ME).status_code == 200 and client.cookies.get(SESSION_COOKIE) == old_token
    assert (
        post_callback(
            client, provider, headers={"Cookie": f"{BINDING_COOKIE}={binding}"}
        ).status_code
        == 303
    )
    assert client.get(ME, headers={"Cookie": f"{SESSION_COOKIE}={old_token}"}).status_code == 401


@pytest.mark.parametrize(
    "invalid", ["state", "duplicate", "missing_code", "error", "missing_binding", "wrong_binding"]
)
def test_bad_http_callback_preserves_valid_attempt_and_never_issues_session(
    browser, auth_database, invalid
):
    """입력 위조를 교환 전에 거부하고 정상 인가 시도는 보존하는지 확인한다.

    Args:
        browser: HTTPS 클라이언트·fake 공급자·등록 사용자 ID.
        auth_database: 세션 생성 없음을 확인할 DB 관리자 연결.
        invalid: 거부할 프로토콜 입력 또는 브라우저 결속 불일치.

    Returns:
        None: 위조 요청 거부 후 같은 정상 시도로 로그인할 수 있음을 확인한다.
    """
    client, provider, user_id = browser
    client.get(LOGIN)
    state = provider.attempts[-1].state
    parameters = [("state", state), ("code", "synthetic-code")]
    headers = {}
    if invalid == "state":
        parameters[0] = ("state", "wrong-state")
    elif invalid == "duplicate":
        parameters.append(("state", state))
    elif invalid == "missing_code":
        parameters.pop()
    elif invalid == "error":
        parameters.append(("error", "access_denied"))
    else:
        headers["Cookie"] = (
            "" if invalid == "missing_binding" else f"{BINDING_COOKIE}=wrong-binding"
        )
    response = post_callback(client, provider, parameters, headers=headers)
    assert (
        response.status_code == 400 and not provider.calls and "set-cookie" not in response.headers
    )
    with psycopg.connect(auth_database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM identity.sessions WHERE user_id=%s", (user_id,)
        ).fetchone() == (0,)
    assert post_callback(client, provider).status_code == 303


@pytest.mark.parametrize("status", [401, 503])
def test_http_provider_failure_is_not_retried_or_converted_to_session(browser, status):
    """교환 실패의 원래 상태를 전달하고 소비한 인가 시도를 재사용하지 않는지 확인한다.

    Args:
        browser: HTTPS 클라이언트·fake 공급자·등록 사용자 ID.
        status: 공급자 검증 또는 연결 실패의 HTTP 상태.

    Returns:
        None: 실패 후 세션 미발급과 같은 시도의 재교환 차단을 확인한다.
    """
    client, provider, _ = browser
    client.get(LOGIN)
    provider.error = ApplicationError(status, "AUTH_PROVIDER_FAILURE", "Authentication failed.")
    response = post_callback(client, provider)
    assert response.status_code == status and client.cookies.get(SESSION_COOKIE) is None
    assert "synthetic-code" not in response.text and "private-nonce" not in response.text
    provider.error = None
    assert post_callback(client, provider).status_code == 400 and len(provider.calls) == 1


@pytest.mark.parametrize(
    "origin,token",
    [
        (None, None),
        ("https://evil.invalid", "valid"),
        (ORIGIN + ".evil.invalid", "valid"),
        (ORIGIN, None),
        (ORIGIN, "wrong"),
    ],
)
def test_http_logout_csrf_rejection_preserves_session(browser, origin, token):
    """Origin 또는 세션 결속 값이 잘못됐을 때 세션을 철회하지 않는지 확인한다.

    Args:
        browser: HTTPS 클라이언트·fake 공급자·등록 사용자 ID.
        origin: 로그아웃 요청의 출처. None이면 헤더를 생략한다.
        token: CSRF 헤더. valid는 현재 세션 값으로 치환한다.

    Returns:
        None: 위조 로그아웃을 403으로 거부하고 기존 세션을 보존함을 확인한다.
    """
    client, provider, _ = browser
    client.get(LOGIN)
    assert post_callback(client, provider).status_code == 303
    csrf = client.get(ME).headers[CSRF_HEADER]
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if token is not None:
        headers[CSRF_HEADER] = csrf if token == "valid" else token
    assert client.post(LOGOUT, headers=headers).status_code == 403
    assert client.get(ME).status_code == 200


def test_http_registration_and_role_changes_are_read_from_database(browser, auth_database):
    """로그인 후의 DB 역할·활성 변경을 HTTP 조회에도 반영하는지 확인한다.

    Args:
        browser: HTTPS 클라이언트·fake 공급자·등록 사용자 ID.
        auth_database: 등록 상태 변경용 DB 관리자 연결.

    Returns:
        None: 역할 헤더로 변경을 우회하지 못하고 비활성 사용자를 차단함을 검증한다.
    """
    client, provider, user_id = browser
    client.get(LOGIN)
    assert post_callback(client, provider).status_code == 303
    with psycopg.connect(auth_database) as conn:
        conn.execute("UPDATE identity.user_roles SET role='sales' WHERE user_id=%s", (user_id,))
    assert client.get(ME, headers={"X-User-Role": "manager"}).json()["data"]["roles"] == ["sales"]
    with psycopg.connect(auth_database) as conn:
        conn.execute("UPDATE identity.users SET is_active=false WHERE id=%s", (user_id,))
    assert client.get(ME).status_code == 401
    client.get(LOGIN)
    assert post_callback(client, provider).status_code == 401


@pytest.mark.parametrize("invalid", ["mime", "utf8", "size", "fields", "form", "query"])
def test_http_callback_body_limits_preserve_the_pending_flow(browser, invalid):
    """본문 형식·크기·필드 수와 query 입력을 제한하고 정상 시도를 보존하는지 확인한다.

    Args:
        browser: HTTPS 클라이언트·fake 공급자·등록 사용자 ID.
        invalid: 거부할 본문 형식 또는 query 사용.

    Returns:
        None: 잘못된 본문을 교환 전에 거부하고 정상 callback을 계속 허용함을 검증한다.
    """
    client, provider, _ = browser
    client.get(LOGIN)
    path = CALLBACK_PATH
    body = urlencode({"state": provider.attempts[-1].state, "code": "synthetic-code"})
    content_type = "application/x-www-form-urlencoded"
    if invalid == "mime":
        content_type = "application/json"
    elif invalid == "utf8":
        body = b"\xff"
    elif invalid == "size":
        body = "code=" + "x" * 9000
    elif invalid == "fields":
        body = urlencode([(str(index), "value") for index in range(21)])
    elif invalid == "form":
        body = "state"
    else:
        path += "?code=private-code"
    response = client.post(path, content=body, headers={"Content-Type": content_type})
    assert response.status_code == 400 and not provider.calls
    assert response.headers["Cache-Control"] == "no-store" and "set-cookie" not in response.headers
    assert post_callback(client, provider).status_code == 303


def test_http_logout_database_failure_does_not_clear_cookie_or_session(browser, monkeypatch):
    """DB 철회 실패를 성공·쿠키 삭제로 숨기지 않는지 확인한다.

    Args:
        browser: HTTPS 클라이언트·fake 공급자·등록 사용자 ID.
        monkeypatch: DB 경계의 일시 장애를 재현하는 pytest 도구.

    Returns:
        None: 503 응답 후 브라우저 쿠키와 서버 세션이 함께 유지됨을 확인한다.
    """
    client, provider, _ = browser
    client.get(LOGIN)
    assert post_callback(client, provider).status_code == 303
    token = client.cookies.get(SESSION_COOKIE)
    csrf = client.get(ME).headers[CSRF_HEADER]
    from fastapi import HTTPException

    monkeypatch.setattr(http, "revoke_session", AsyncMock(side_effect=HTTPException(503)))
    response = client.post(LOGOUT, headers={"Origin": ORIGIN, CSRF_HEADER: csrf})
    assert response.status_code == 503 and "set-cookie" not in response.headers
    assert client.cookies.get(SESSION_COOKIE) == token and client.get(ME).status_code == 200
