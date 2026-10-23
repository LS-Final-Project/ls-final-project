"""설정으로 활성화하는 로그인 HTTP 초안. 역할과 공급자 토큰을 브라우저에서 받지 않는다."""

import hmac
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from typing import Protocol
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from starlette.responses import RedirectResponse, Response

from backend.api.platform.auth.attempts import complete_stored_callback
from backend.api.platform.auth.callback import OAuthAdapter, OAuthPurpose, reject_callback
from backend.api.platform.auth.login import OAuthStartAdapter, start_login
from backend.api.platform.auth.microsoft import PersonalMicrosoftOAuth
from backend.api.platform.auth.schemas import Identity
from backend.api.platform.auth.sessions import issue_session, load_session, revoke_session
from backend.api.platform.auth.settings import MicrosoftAuthSettings
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse

SESSION_COOKIE = "__Host-team4_session"
BINDING_COOKIE = "__Host-team4_oauth"
CSRF_HEADER = "X-CSRF-Token"
AUTH_PREFIX = "/api/v1/platform/auth/"

router = APIRouter(
    prefix="/api/v1/platform/auth",
    tags=["identity"],
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
DRAFT = {"x-implementation-status": "draft", "x-activation": "MICROSOFT_AUTH_ENABLED"}


class LoginAdapter(OAuthStartAdapter, OAuthAdapter, Protocol):
    """기존 MSAL과 fake가 공유하는 로그인 시작·교환 경계."""


class AuthAccessLogFilter(logging.Filter):
    """잘못된 GET callback 등 인증 경로의 query가 Uvicorn 접근 로그에 남지 않게 한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Uvicorn 표준 접근 로그 인자의 인증 URL에서 query를 제거한다.

        Args:
            record: Uvicorn 접근 로그 레코드.

        Returns:
            bool: query를 제거한 로그를 계속 기록하도록 True를 반환한다.
        """
        if isinstance(record.args, tuple) and len(record.args) == 5:
            path = record.args[2]
            if isinstance(path, str) and path.startswith(AUTH_PREFIX):
                record.args = (*record.args[:2], path.split("?", 1)[0], *record.args[3:])
        return True


def configure_auth(app: FastAPI, configuration: MicrosoftAuthSettings) -> None:
    """설정을 검사하고 어댑터·캐시 방지·접근 로그 보호를 플랫폼 앱에 연결한다.

    Args:
        app: 소유 서비스인 플랫폼 FastAPI 앱.
        configuration: 명시적으로 활성화할 Microsoft 로그인 설정.

    Returns:
        None: 앱에 서버 전용 설정과 보호 경계를 등록한다.
    """
    configuration.validate_configuration(dev_auth_enabled=app.state.settings.dev_auth_enabled)
    app.state.microsoft_auth = configuration
    app.state.microsoft_oauth = None
    if configuration.enabled:
        assert configuration.client_id is not None and configuration.client_secret is not None
        app.state.microsoft_oauth = PersonalMicrosoftOAuth(
            configuration.client_id, configuration.client_secret, configuration.redirect_uri
        )
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, AuthAccessLogFilter) for item in logger.filters):
        logger.addFilter(AuthAccessLogFilter())

    @app.middleware("http")
    async def protect_responses(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """오류 응답을 포함한 인증 응답의 캐시와 referrer 전달을 차단한다.

        Args:
            request: 현재 HTTP 요청.
            call_next: 플랫폼 요청 처리기.

        Returns:
            Response: 인증 경로에 보호 헤더를 추가한 응답.
        """
        response = await call_next(request)
        if request.url.path.startswith(AUTH_PREFIX) or request.url.path == "/api/v1/platform/me":
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response


def configuration(request: Request) -> MicrosoftAuthSettings:
    """활성 설정만 반환하며 비활성 로그인은 DB·공급자 호출 전에 거부한다.

    Args:
        request: 현재 플랫폼 요청.

    Returns:
        MicrosoftAuthSettings: 활성화된 서버 로그인 설정.
    """
    value: MicrosoftAuthSettings = request.app.state.microsoft_auth
    if not value.enabled:
        raise ApplicationError(503, "AUTH_NOT_CONFIGURED", "Authentication is not configured.")
    return value


def adapter(request: Request) -> LoginAdapter:
    """앱에 준비된 공급자 어댑터를 반환한다.

    Args:
        request: 현재 플랫폼 요청.

    Returns:
        LoginAdapter: 실제 MSAL 또는 검사에서 교체한 fake 어댑터.
    """
    configuration(request)
    return request.app.state.microsoft_oauth


def csrf_token(token: str) -> str:
    """불투명 세션 ID를 노출하지 않고 해당 세션에 결속한 CSRF 값을 계산한다.

    Args:
        token: 서버만 읽는 HttpOnly 세션 쿠키의 원문.

    Returns:
        str: 원문 토큰과 구분되는 세션별 HMAC-SHA256 값.
    """
    return hmac.new(token.encode(), b"team4-platform-csrf-v1", "sha256").hexdigest()


async def operational_identity(request: Request, response: Response) -> Identity:
    """운영 세션으로 현재 사용자를 조회하고 변경 요청용 CSRF 헤더를 발급한다.

    Args:
        request: 운영 세션 쿠키를 포함한 현재 요청.
        response: 원문 세션 ID 대신 CSRF 헤더를 넣을 응답.

    Returns:
        Identity: DB에서 다시 확인한 활성 사용자와 현재 역할.
    """
    value = configuration(request)
    origin = request.headers.get("origin")
    if origin and origin != value.app_origin.rstrip("/"):
        raise HTTPException(403)
    token = request.cookies.get(SESSION_COOKIE)
    user = await load_session(request.app.state.settings, token)
    assert token is not None
    response.headers[CSRF_HEADER] = csrf_token(token)
    return user


@router.get(
    "/login",
    status_code=302,
    response_class=RedirectResponse,
    operation_id="platform_start_login",
    openapi_extra=DRAFT,
)
async def login(request: Request, provider: LoginAdapter = Depends(adapter)) -> RedirectResponse:
    """서버 인가 시도를 저장한 뒤 Microsoft로 이동하고 브라우저 결속 쿠키를 설정한다.

    Args:
        request: 로그인 시작 요청.
        provider: 서버의 Microsoft 로그인 어댑터.

    Returns:
        RedirectResponse: flow·토큰을 포함하지 않는 Microsoft 인가 URL의 302 응답.
    """
    binding = token_urlsafe(32)
    previous_token = request.cookies.get(SESSION_COOKIE)
    if previous_token:
        # Lax 세션은 Microsoft의 교차 사이트 POST에 실리지 않는다. 원문 대신 hash를
        # 결속값에 포함하고 전체 값을 서버 인가 시도에 결속해 callback 위조를 막는다.
        binding += "." + sha256(previous_token.encode()).hexdigest()
    url = await start_login(
        request.app.state.settings, browser_binding=binding, now=datetime.now(UTC), adapter=provider
    )
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        BINDING_COOKIE, binding, max_age=300, secure=True, httponly=True, samesite="none", path="/"
    )
    return response


@router.post(
    "/callback",
    status_code=303,
    response_class=RedirectResponse,
    operation_id="platform_complete_login",
    openapi_extra={
        **DRAFT,
        "requestBody": {
            "required": True,
            "content": {
                "application/x-www-form-urlencoded": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "state": {"type": "string"},
                            "code": {"type": "string"},
                            "error": {"type": "string"},
                        },
                        "additionalProperties": {"type": "string"},
                    }
                }
            },
        },
    },
)
async def callback(request: Request, provider: LoginAdapter = Depends(adapter)) -> RedirectResponse:
    """Microsoft form_post를 검증하고 새 서버 세션을 발급해 고정 앱 진입점으로 이동한다.

    Args:
        request: 브라우저 결속 쿠키와 form_post 본문을 포함한 요청.
        provider: code 교환과 ID 토큰 검증 어댑터.

    Returns:
        RedirectResponse: 세션 교체 후 code·state 없는 고정 URL의 303 응답.
    """
    value = configuration(request)
    if (
        request.url.query
        or request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/x-www-form-urlencoded"
    ):
        raise reject_callback()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise reject_callback()
    try:
        parameters = parse_qsl(
            body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=20
        )
    except (ValueError, UnicodeError):
        raise reject_callback() from None
    binding = request.cookies.get(BINDING_COOKIE, "")
    subject = await complete_stored_callback(
        request.app.state.settings,
        parameters,
        purpose=OAuthPurpose.LOGIN,
        browser_binding=binding,
        session_user_id=None,
        now=datetime.now(UTC),
        adapter=provider,
    )
    grant = await issue_session(
        request.app.state.settings, subject, previous_token_hash=binding.partition(".")[2] or None
    )
    response = RedirectResponse(value.app_origin.rstrip("/") + "/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        grant.token.get_secret_value(),
        max_age=28800,
        expires=grant.expires_at.astimezone(UTC),
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(BINDING_COOKIE, secure=True, httponly=True, samesite="none", path="/")
    return response


@router.post("/logout", status_code=204, operation_id="platform_logout", openapi_extra=DRAFT)
async def logout(request: Request) -> Response:
    """허용 Origin과 세션 결속 CSRF를 확인한 뒤 서버 세션 철회와 쿠키 삭제를 수행한다.

    Args:
        request: 운영 세션 쿠키·Origin·X-CSRF-Token을 포함한 요청.

    Returns:
        Response: 철회가 완료된 204 응답. 세션 쿠키가 없으면 같은 결과를 반환한다.
    """
    value = configuration(request)
    if request.headers.get("origin") != value.app_origin.rstrip("/"):
        raise HTTPException(403)
    token = request.cookies.get(SESSION_COOKIE)
    supplied = request.headers.get(CSRF_HEADER, "")
    if token and not hmac.compare_digest(supplied.encode(), csrf_token(token).encode()):
        raise HTTPException(403)
    await revoke_session(request.app.state.settings, token)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    response.delete_cookie(BINDING_COOKIE, secure=True, httponly=True, samesite="none", path="/")
    return response
