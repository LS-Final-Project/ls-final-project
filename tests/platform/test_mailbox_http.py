"""전용 PostgreSQL·합성 사서함 어댑터로 기본 비활성 HTTP·쿠키·CSRF를 검사한다."""

import asyncio
import json
from base64 import b64encode
from datetime import timedelta
from hashlib import sha256
from html.parser import HTMLParser
from secrets import token_bytes
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.api.platform.auth import mailbox_http, mailbox_vault
from backend.api.platform.auth.callback import (
    MicrosoftSubject,
    OAuthPurpose,
    PendingAuthorization,
    validate_callback,
)
from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
from backend.api.platform.auth.mailbox_settings import MAILBOX_CALLBACK, MailboxAuthSettings
from backend.api.platform.auth.microsoft import PERSONAL_TENANT, MailboxAuthorization
from backend.api.platform.auth.sessions import issue_session
from backend.api.platform.auth.settings import CALLBACK_PATH, MicrosoftAuthSettings
from backend.api.platform.main import create_app
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

ORIGIN = "https://app.example.invalid"
PREFIX = mailbox_http.PREFIX


def configurations(**changes):
    """실제 자격증명 없는 운영 로그인·사서함 활성 설정을 만든다.

    Args:
        **changes: 사서함 설정을 교체할 검사 값.

    Returns:
        tuple: 같은 출처의 별도 callback·전용 키를 가진 두 설정.
    """
    return (
        MicrosoftAuthSettings(
            _env_file=None,
            enabled=True,
            client_id=UUID(int=1),
            client_secret=SecretStr("synthetic-secret"),
            app_origin=ORIGIN,
            redirect_uri=ORIGIN + CALLBACK_PATH,
        ),
        MailboxAuthSettings(
            _env_file=None,
            **{
                "enabled": True,
                "encryption_key": SecretStr(b64encode(token_bytes(32)).decode()),
                "key_version": "test-http-v1",
                "redirect_uri": ORIGIN + MAILBOX_CALLBACK,
                **changes,
            },
        ),
    )


class FakeMailbox:
    """실제 MSAL 검사는 별도 회귀가 담당하며 여기서는 검증된 인계만 합성한다."""

    def __init__(self):
        """실제 개인 정보 없는 별도 사서함·호출 기록을 준비한다.

        Args:
            없음.

        Returns:
            None: 합성 공급자 문맥을 초기화한다.
        """
        self.attempts = []
        self.exchanges = []
        self.subject = MicrosoftSubject(PERSONAL_TENANT, uuid4())
        self.error = None

    def begin(self, purpose, *, browser_binding, now, owner_user_id=None):
        """현재 서버 사용자와 결속된 사서함 인가 시도만 생성한다.

        Args:
            purpose: HTTP 경계에서 고정한 사서함 목적.
            browser_binding: 서버가 생성한 브라우저 결속값.
            now: 현재 인가 시각.
            owner_user_id: 현재 세션에서 복원한 소유자.

        Returns:
            tuple: 합성 Microsoft URL과 서버 저장용 시도.
        """
        assert purpose is OAuthPurpose.MAILBOX and owner_user_id is not None
        pending = PendingAuthorization(
            purpose,
            str(uuid4()),
            sha256(browser_binding.encode()).hexdigest(),
            now + timedelta(minutes=5),
            {"nonce": "synthetic-nonce", "code_verifier": "synthetic-verifier"},
            owner_user_id,
        )
        self.attempts.append(pending)
        return (
            "https://login.microsoftonline.com/consumers/authorize?state=" + pending.state,
            pending,
        )

    def redeem_mailbox(self, pending, parameters, *, browser_binding, session_user_id, now):
        """기존 callback 검증을 통과한 현재 소유자에게만 합성 캐시를 인계한다.

        Args:
            pending: 원자적으로 소비한 시도.
            parameters: 중복 키를 보존한 callback 입력.
            browser_binding: 현재 브라우저 결속.
            session_user_id: 현재 유효 세션 사용자.
            now: 현재 검증 시각.

        Returns:
            MailboxAuthorization: 실제 비밀 없는 합성 보관 인계.
        """
        validate_callback(
            pending,
            parameters,
            purpose=OAuthPurpose.MAILBOX,
            browser_binding=browser_binding,
            session_user_id=session_user_id,
            now=now,
        )
        self.exchanges.append(pending.state)
        if self.error:
            raise self.error
        cache = {
            "Account": {
                "account": {
                    "environment": "login.microsoftonline.com",
                    "realm": "consumers",
                    "local_account_id": str(self.subject.oid),
                    "home_account_id": str(self.subject.oid),
                }
            },
            "RefreshToken": {"token": {"secret": "synthetic-refresh-token"}},
        }
        return MailboxAuthorization(session_user_id, self.subject, SecretStr(json.dumps(cache)))


class RelayForm(HTMLParser):
    """문서의 hidden 입력을 실행 없이 추출하는 검사 도구."""

    def __init__(self):
        """검사할 form·입력 목록을 초기화한다.

        Args:
            없음.

        Returns:
            None: 빈 파서를 준비한다.
        """
        super().__init__()
        self.inputs = []
        self.action = None

    def handle_starttag(self, tag, attrs):
        """HTMLParser가 복원한 속성에서 고정 경로와 두 입력만 읽는다.

        Args:
            tag: 현재 태그.
            attrs: 해석된 속성 목록.

        Returns:
            None: form과 hidden 입력을 수집한다.
        """
        values = dict(attrs)
        if tag == "form":
            self.action = values["action"]
        if tag == "input":
            self.inputs.append((values["name"], values["value"]))


@pytest.fixture
def context(mailbox_database, registered_user, settings):
    """전용 DB·현재 세션·실제 암호기와 합성 공급자를 갖는 HTTP 앱을 준비한다.

    Args:
        mailbox_database: 보관 스키마가 준비된 자체 검사 DB.
        registered_user: 합성 등록 사용자.
        settings: platform_app 권한의 DB 설정.

    Returns:
        SimpleNamespace: 이번 검사 자료만 정리하는 HTTP 문맥.
    """
    auth, mailbox = configurations()
    app = create_app(settings, auth, mailbox_settings=mailbox)
    fake = FakeMailbox()
    app.state.mailbox_oauth = fake
    session = asyncio.run(issue_session(settings, registered_user[0])).token.get_secret_value()
    try:
        with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
            client.cookies.set(SESSION_COOKIE, session)
            yield SimpleNamespace(
                client=client,
                app=app,
                provider=fake,
                owner=registered_user[1],
                session=session,
                dsn=mailbox_database,
                headers={"Origin": ORIGIN, CSRF_HEADER: csrf_token(session)},
            )
    finally:
        with psycopg.connect(mailbox_database) as conn:
            conn.execute(
                "DELETE FROM mailbox_auth.connections WHERE owner_user_id=%s", (registered_user[1],)
            )
            conn.execute(
                "DELETE FROM identity.oauth_authorizations WHERE owner_user_id=%s",
                (registered_user[1],),
            )


def begin(context):
    """실제 시작 API로 일회 시도를 만들고 합성 callback 본문을 준비한다.

    Args:
        context: 현재 세션의 HTTP 검사 문맥.

    Returns:
        str: 합성 code/state만 포함한 form 본문.
    """
    response = context.client.post(PREFIX + "/start", headers=context.headers)
    assert response.status_code == 200
    assert set(response.json()["data"]) == {"authorization_url"}
    return urlencode({"state": context.provider.attempts[-1].state, "code": "synthetic-code"})


def complete(context, body, **changes):
    """동일 출처 완료 API를 호출한다.

    Args:
        context: 현재 세션과 사서함 결속을 가진 브라우저 대체.
        body: 제한된 form 본문.
        **changes: 부정 조건의 헤더 등 요청 교체.

    Returns:
        Response: 토큰 없는 성공 redirect 또는 안전한 오류.
    """
    return context.client.post(
        PREFIX + "/complete",
        **{
            "content": body,
            "headers": {"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
            **changes,
        },
    )


def test_default_disabled_routes_do_not_touch_database_or_provider():
    """기본 비활성은 키·DB 없이도 시작하며 모든 사서함 경로를 닫는다.

    Args:
        없음.

    Returns:
        None: 기존 로그인과 독립인404 및 미초기화 공급자·키를 확인한다.
    """
    app = create_app(
        Settings(_env_file=None, environment="test"),
        MicrosoftAuthSettings(_env_file=None),
        mailbox_settings=MailboxAuthSettings(_env_file=None),
    )
    with TestClient(app) as client:
        for suffix in ("/start", "/callback", "/complete", f"/{uuid4()}/disconnect"):
            assert client.post(PREFIX + suffix).status_code == 404
        assert client.get(PREFIX).status_code == 404
    assert app.state.mailbox_oauth is app.state.mailbox_cipher is app.state.mailbox_tokens is None


@pytest.mark.parametrize("bad", ["login", "key", "version", "callback"])
def test_enabled_configuration_fails_closed_without_secret_details(bad):
    """활성 설정의 키·버전·로그인·고정 callback 누락을 시작 시 안전하게 거부한다.

    Args:
        bad: 잘못된 활성 설정.

    Returns:
        None: 값 원문 없는 설정 오류를 확인한다.
    """
    auth, mailbox = configurations()
    if bad == "login":
        auth.enabled = False
    elif bad == "key":
        mailbox.encryption_key = SecretStr("synthetic-invalid-key")
    elif bad == "version":
        mailbox.key_version = ""
    else:
        mailbox.redirect_uri = ORIGIN + CALLBACK_PATH
    with pytest.raises(ValueError) as error:
        create_app(Settings(_env_file=None, environment="test"), auth, mailbox_settings=mailbox)
    assert "synthetic-invalid-key" not in str(error.value)


@pytest.mark.parametrize("bad", ["origin", "no_origin", "csrf", "no_session"])
def test_start_requires_current_session_same_origin_and_csrf(context, bad):
    """시작은 현재 운영 세션·허용 출처·세션 결속 CSRF 없이는 인가를 만들지 않는다.

    Args:
        context: 실제 DB의 HTTP 문맥.
        bad: 제거하거나 바꿀 요청 인증 조건.

    Returns:
        None: 고정 거부와 공급자·인가 저장 미호출을 확인한다.
    """
    headers = dict(context.headers)
    if bad == "origin":
        headers["Origin"] = "https://evil.example.invalid"
    elif bad == "no_origin":
        headers.pop("Origin")
    elif bad == "csrf":
        headers[CSRF_HEADER] = "wrong"
    else:
        context.client.cookies.clear()
    result = context.client.post(PREFIX + "/start", headers=headers)
    assert result.status_code == (401 if bad == "no_session" else 403)
    assert context.provider.attempts == []


def test_cookie_relay_commit_and_disconnect_preserve_login(context):
    """별도 Lax 결속·무상태 중간 문서·커밋·재사용 차단·해제가 기존 로그인을 보존한다.

    Args:
        context: 실제 DB와 합성 인가 공급자.

    Returns:
        None: 초기 연결 전체 HTTP 흐름과 별도 해제·기존 세션 보존을 확인한다.
    """
    body = begin(context)
    cookie = next(c for c in context.client.cookies.jar if c.name == mailbox_http.BINDING_COOKIE)
    assert cookie.secure and cookie._rest["HttpOnly"] is None and cookie._rest["SameSite"] == "lax"
    assert cookie.path == "/" and context.session not in cookie.value
    context.client.cookies.delete(SESSION_COOKIE)
    relay = context.client.post(
        PREFIX + "/callback",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://login.microsoftonline.com",
        },
    )
    assert relay.status_code == 200 and context.provider.exchanges == []
    assert (
        relay.headers["cache-control"] == "no-store"
        and relay.headers["referrer-policy"] == "no-referrer"
    )
    assert "frame-ancestors 'none'" in relay.headers["content-security-policy"]
    form = RelayForm()
    form.feed(relay.text)
    assert form.action == PREFIX + "/complete"
    context.client.cookies.set(SESSION_COOKIE, context.session)
    response = complete(context, urlencode(form.inputs))
    assert response.status_code == 303 and response.headers["location"] == ORIGIN + "/"
    assert len(context.provider.exchanges) == 1
    assert mailbox_http.BINDING_COOKIE not in context.client.cookies
    assert context.client.cookies.get(SESSION_COOKIE) == context.session
    with psycopg.connect(context.dsn) as conn:
        row = conn.execute(
            "SELECT id,revision,cache_ciphertext FROM mailbox_auth.connections WHERE owner_user_id=%s",
            (context.owner,),
        ).fetchone()
        assert row[1] == 1 and b"synthetic-refresh-token" not in bytes(row[2])
    assert complete(context, body).status_code == 400
    result = context.client.post(f"{PREFIX}/{row[0]}/disconnect", headers=context.headers)
    assert result.status_code == 204
    with psycopg.connect(context.dsn) as conn:
        assert conn.execute(
            "SELECT state,cache_ciphertext FROM mailbox_auth.connections WHERE id=%s", (row[0],)
        ).fetchone() == ("disconnected", None)
        assert (
            conn.execute(
                "SELECT count(*) FROM identity.sessions WHERE user_id=%s", (context.owner,)
            ).fetchone()[0]
            == 1
        )
    for secret in (
        "synthetic-refresh-token",
        "synthetic-nonce",
        "synthetic-verifier",
        context.session,
    ):
        assert secret not in response.text + relay.text


@pytest.mark.parametrize(
    "body",
    [
        "state=a&state=b&code=c",
        "state=a&code=c&error=denied",
        "state=&code=c",
        "state=a&code=",
        "state=a&code=" + "x" * 8200,
    ],
)
def test_relay_rejects_invalid_input_without_provider_exchange(context, body):
    """무상태 중간 단계도 중복·오류·빈 값·초과 입력을 반사하지 않는다.

    Args:
        context: HTTP 검사 문맥.
        body: 잘못된 합성 form 입력.

    Returns:
        None: 고정 오류와 공급자 미호출을 확인한다.
    """
    result = context.client.post(
        PREFIX + "/callback",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert result.status_code == 400 and context.provider.exchanges == []


def test_relay_escapes_only_two_fields_and_does_not_reflect_tokens(context):
    """code/state의 HTML 주입을 escape하고 다른 민감 필드는 응답에서 제외한다.

    Args:
        context: 실제 상태 변경 없는 중간 단계의 HTTP 문맥.

    Returns:
        None: 태그 탈출 부재·동일 입력 복원·필드 화이트리스트를 확인한다.
    """
    malicious = '"><img src=x onerror=alert(1)><script>alert(1)</script>'
    body = urlencode(
        {"state": malicious, "code": malicious, "access_token": "synthetic-private-token"}
    )
    result = context.client.post(
        PREFIX + "/callback",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert result.status_code == 200 and "<img" not in result.text
    assert "synthetic-private-token" not in result.text
    parser = RelayForm()
    parser.feed(result.text)
    assert parser.inputs == [("state", malicious), ("code", malicious)]


@pytest.mark.parametrize("bad", ["origin", "binding", "session", "state", "inactive"])
def test_complete_denies_wrong_browser_or_current_session_without_consuming(context, bad):
    """다른 출처·브라우저·세션·시도나 비활성 사용자는 정상 인가를 소비하지 않는다.

    Args:
        context: 정상 시작한 실제 DB 시도.
        bad: 변경할 완료 요청 조건.

    Returns:
        None: 공급자 미호출과 정상 시도 보존을 확인한다.
    """
    body = begin(context)
    changes = {}
    expected = 400
    if bad == "origin":
        changes["headers"] = {"Origin": "null", "Content-Type": "application/x-www-form-urlencoded"}
        expected = 403
    elif bad == "binding":
        context.client.cookies.clear()
        context.client.cookies.set(SESSION_COOKIE, context.session)
    elif bad == "session":
        context.client.cookies.delete(SESSION_COOKIE)
        expected = 401
    elif bad == "state":
        body = urlencode({"state": str(uuid4()), "code": "synthetic-code"})
    else:
        with psycopg.connect(context.dsn) as conn:
            conn.execute("UPDATE identity.users SET is_active=false WHERE id=%s", (context.owner,))
        expected = 401
    assert complete(context, body, **changes).status_code == expected
    assert context.provider.exchanges == []
    with psycopg.connect(context.dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM identity.oauth_authorizations WHERE owner_user_id=%s",
                (context.owner,),
            ).fetchone()[0]
            == 1
        )


def test_consumed_provider_failure_is_not_redirect_or_reusable(context):
    """교환 실패를 연결 성공 redirect로 바꾸거나 소비한 인가를 복구하지 않는다.

    Args:
        context: 정상 시작 후 공급자 실패를 주입할 문맥.

    Returns:
        None: 고정 실패·캐시 부재·동일 callback 재사용 차단을 확인한다.
    """
    body = begin(context)
    context.provider.error = ApplicationError(
        503, "MAILBOX_TOKEN_UNAVAILABLE", "Mailbox authentication is unavailable."
    )
    assert complete(context, body).status_code == 503
    assert complete(context, body).status_code == 400
    assert len(context.provider.exchanges) == 1
    with psycopg.connect(context.dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM mailbox_auth.connections WHERE owner_user_id=%s",
                (context.owner,),
            ).fetchone()[0]
            == 0
        )


def add_connection(dsn, owner, state, ciphertext, oid=None):
    """관리자 연결로 목록 검사용 사서함 연결 행 하나를 직접 만든다.

    Args:
        dsn: 전용 검사 DB의 관리자 연결 문자열.
        owner: 연결 소유 서비스 사용자 ID.
        state: connected 또는 disconnected.
        ciphertext: connected일 때 넣을 합성 암호문 바이트.
        oid: 지정할 합성 Microsoft oid. 없으면 새로 만든다.

    Returns:
        tuple[UUID, UUID]: 연결 ID와 응답에 나오면 안 되는 합성 Microsoft oid.
    """
    mailbox_id, oid = uuid4(), oid or uuid4()
    connected = state == "connected"
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO mailbox_auth.connections (id,owner_user_id,subject_tid,subject_oid,"
            "revision,state,key_version,cache_ciphertext) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                mailbox_id,
                owner,
                PERSONAL_TENANT,
                oid,
                1 if connected else 2,
                state,
                "list-test-v1",
                ciphertext if connected else None,
            ),
        )
    return mailbox_id, oid


def test_list_returns_only_current_owner_connections_without_secrets(context):
    """목록은 현재 세션 소유자의 상태·개정·시각만 주고 다른 사용자 행과 비밀을 내보내지 않는다.

    Args:
        context: 실제 DB·현재 세션의 HTTP 문맥.

    Returns:
        None: 실제 두 사용자 행으로 소유 범위·정렬·응답 필드·비밀 부재를 확인한다.
    """
    other = uuid4()
    ciphertext = b"synthetic-ciphertext-marker-" * 2
    with psycopg.connect(context.dsn) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (other, uuid4(), uuid4(), "other@example.invalid", "다른 합성 사용자", "TS_AUTO"),
        )
        conn.execute(
            "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'cs_agent')", (other,)
        )
    try:
        old = add_connection(context.dsn, context.owner, "disconnected", ciphertext)
        new = add_connection(context.dsn, context.owner, "connected", ciphertext)
        foreign = add_connection(context.dsn, other, "connected", ciphertext)
        response = context.client.get(PREFIX)
        assert response.status_code == 200
        items = response.json()["data"]["items"]
        assert [(i["mailbox_id"], i["state"], i["revision"]) for i in items] == [
            (str(new[0]), "connected", 1),
            (str(old[0]), "disconnected", 2),
        ]
        fields = {"mailbox_id", "state", "revision", "updated_at", "reconnect_required"}
        fields |= {"reconnect_reason", "reconnect_required_at"}
        assert all(set(i) == fields for i in items)
        assert [i["reconnect_required"] for i in items] == [False, False]
        with psycopg.connect(context.dsn) as conn:
            conn.execute(
                "UPDATE mailbox_auth.connections SET reconnect_required_at=clock_timestamp(),"
                "reconnect_reason='invalid_grant' WHERE id=%s",
                (new[0],),
            )
        flagged = context.client.get(PREFIX).json()["data"]["items"][0]
        assert (flagged["reconnect_required"], flagged["reconnect_reason"]) == (
            True,
            "invalid_grant",
        )
        assert flagged["reconnect_required_at"] is not None
        for secret in (
            str(foreign[0]),
            str(old[1]),
            str(new[1]),
            str(foreign[1]),
            str(PERSONAL_TENANT),
            "list-test-v1",
            ciphertext.decode(),
            b64encode(ciphertext).decode(),
            ciphertext.hex(),
            context.session,
        ):
            assert secret not in response.text
    finally:
        with psycopg.connect(context.dsn) as conn:
            conn.execute("DELETE FROM mailbox_auth.connections WHERE owner_user_id=%s", (other,))
            conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (other,))
            conn.execute("DELETE FROM identity.users WHERE id=%s", (other,))


@pytest.mark.parametrize("bad", ["origin", "no_session", "inactive"])
def test_list_requires_current_session_and_rejects_foreign_origin(context, bad):
    """목록은 CSRF 없이 읽지만 다른 출처·세션 없음·비활성 사용자는 거부한다.

    Args:
        context: 실제 DB·현재 세션의 HTTP 문맥.
        bad: 거부할 요청 조건.

    Returns:
        None: 403 또는 401과 DB 행 비노출을 확인한다.
    """
    headers = {}
    if bad == "origin":
        headers["Origin"] = "https://evil.example.invalid"
    elif bad == "no_session":
        context.client.cookies.delete(SESSION_COOKIE)
    else:
        with psycopg.connect(context.dsn) as conn:
            conn.execute("UPDATE identity.users SET is_active=false WHERE id=%s", (context.owner,))
    response = context.client.get(PREFIX, headers=headers)
    assert response.status_code == (403 if bad == "origin" else 401)
    assert "items" not in response.text


def test_list_database_failure_is_safe_unavailable():
    """목록 조회의 DB 장애를 빈 목록으로 숨기지 않고 원문 없이 503으로 낸다.

    Args:
        없음.

    Returns:
        None: 로컬 연결 거부의 고정 503 변환과 연결 문자열 비노출을 확인한다.
    """
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(
            "postgresql://postgres:synthetic-dsn-secret@127.0.0.1:1/kan77_auth_test"
        ),
    )
    with pytest.raises(ApplicationError, match="MAILBOX_TOKEN_UNAVAILABLE") as error:
        asyncio.run(mailbox_vault.list_mailboxes(settings, owner_user_id=uuid4()))
    assert error.value.status_code == 503
    assert "synthetic-dsn-secret" not in repr(error.value) + error.value.message


def connections(context, owner=None):
    """관리자 연결로 소유자의 연결 행을 오래된 변경 순으로 읽는다.

    Args:
        context: 실제 DB의 HTTP 문맥.
        owner: 조회할 소유자. 없으면 현재 세션 소유자.

    Returns:
        list[tuple]: 연결 ID·Microsoft oid·개정·상태·암호문·변경 시각.
    """
    with psycopg.connect(context.dsn) as conn:
        return conn.execute(
            "SELECT id,subject_oid,revision,state,cache_ciphertext,updated_at "
            "FROM mailbox_auth.connections WHERE owner_user_id=%s ORDER BY updated_at, id",
            (owner or context.owner,),
        ).fetchall()


def connect(context):
    """실제 시작·완료 API로 현재 합성 계정의 사서함 연결을 한 번 완료한다.

    Args:
        context: 현재 세션과 합성 공급자를 가진 HTTP 문맥.

    Returns:
        None: 303 완료를 확인한다.
    """
    assert complete(context, begin(context)).status_code == 303


def test_same_account_reconnects_existing_row_and_new_account_adds_row(context):
    """같은 Microsoft 계정의 재연결·재동의는 같은 mailbox_id를 쓰고 다른 계정은 새 행이다.

    Args:
        context: 실제 DB·합성 공급자의 HTTP 문맥.

    Returns:
        None: 해제 후 재연결·연결 중 재동의·다른 계정 연결의 행 수·ID·개정·상태를 확인한다.
    """
    connect(context)
    [(mailbox_id, oid, revision, state, _, _)] = connections(context)
    assert (revision, state) == (1, "connected")
    response = context.client.post(f"{PREFIX}/{mailbox_id}/disconnect", headers=context.headers)
    assert response.status_code == 204
    connect(context)
    assert [r[:4] for r in connections(context)] == [(mailbox_id, oid, 3, "connected")]
    connect(context)
    assert [r[:4] for r in connections(context)] == [(mailbox_id, oid, 4, "connected")]
    context.provider.subject = MicrosoftSubject(PERSONAL_TENANT, uuid4())
    connect(context)
    rows = connections(context)
    assert rows[0][:4] == (mailbox_id, oid, 4, "connected")
    assert len(rows) == 2 and rows[1][0] != mailbox_id
    assert rows[1][1:4] == (context.provider.subject.oid, 1, "connected")


def test_duplicate_same_account_rows_update_only_the_latest(context):
    """유일 제약이 없어 같은 계정 행이 둘이면 최근 변경 행 하나만 재연결한다.

    Args:
        context: 실제 DB·합성 공급자의 HTTP 문맥.

    Returns:
        None: 최근 행만 개정·상태가 바뀌고 오래된 행은 그대로인지 확인한다.
    """
    oid = context.provider.subject.oid
    older = add_connection(context.dsn, context.owner, "disconnected", None, oid)[0]
    newer = add_connection(context.dsn, context.owner, "disconnected", None, oid)[0]
    before = connections(context)
    connect(context)
    after = {r[0]: r for r in connections(context)}
    assert len(after) == 2
    assert after[older] == next(r for r in before if r[0] == older)
    assert after[newer][2:4] == (3, "connected")


def test_same_account_of_another_owner_is_never_touched(context):
    """다른 소유자의 같은 Microsoft 계정 행은 재사용·수정하지 않고 현재 소유자 행을 새로 만든다.

    Args:
        context: 실제 DB·합성 공급자의 HTTP 문맥.

    Returns:
        None: 다른 소유자 행의 모든 열 불변과 현재 소유자 새 행을 확인한다.
    """
    other = uuid4()
    with psycopg.connect(context.dsn) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (other, uuid4(), uuid4(), "other@example.invalid", "다른 합성 사용자", "TS_AUTO"),
        )
        conn.execute(
            "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'cs_agent')", (other,)
        )
    try:
        oid = context.provider.subject.oid
        add_connection(context.dsn, other, "connected", b"synthetic-other-owner-cipher-" * 2, oid)
        before = connections(context, other)
        connect(context)
        assert connections(context, other) == before
        [(_, own_oid, revision, state, _, _)] = connections(context)
        assert (own_oid, revision, state) == (oid, 1, "connected")
    finally:
        with psycopg.connect(context.dsn) as conn:
            conn.execute("DELETE FROM mailbox_auth.connections WHERE owner_user_id=%s", (other,))
            conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (other,))
            conn.execute("DELETE FROM identity.users WHERE id=%s", (other,))
