"""기본 비활성 사서함 HTTP 연결. 로그인·업무 중계·토큰 소비자와 별도 활성화한다."""

import hmac
from datetime import UTC, datetime
from html import escape
from secrets import token_urlsafe
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import HTMLResponse, RedirectResponse, Response

from backend.api.platform.auth.callback import reject_callback
from backend.api.platform.auth.http import (
    CSRF_HEADER,
    SESSION_COOKIE,
    configuration,
    csrf_token,
    operational_identity,
    read_callback_form,
)
from backend.api.platform.auth.mailbox import complete_and_store_mailbox_link, start_mailbox_link
from backend.api.platform.auth.mailbox_settings import MailboxAuthSettings
from backend.api.platform.auth.mailbox_tokens import MailboxTokenProvider
from backend.api.platform.auth.mailbox_vault import (
    MailboxCipher,
    disconnect_mailbox,
    list_mailboxes,
)
from backend.api.platform.auth.microsoft import PersonalMicrosoftOAuth
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

PREFIX = "/api/v1/platform/auth/mailbox"
BINDING_COOKIE = "__Host-team4_mailbox_oauth"
ACTIVATION = {"x-implementation-status": "partial", "x-activation": "MAILBOX_AUTH_ENABLED"}
FORM_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/x-www-form-urlencoded": {
                "schema": {
                    "type": "object",
                    "properties": {"state": {"type": "string"}, "code": {"type": "string"}},
                    "required": ["state", "code"],
                }
            }
        },
    }
}
ERROR_CODES = (400, 401, 403, 404, 409, 503)
# HTML 성공 경로도 오류는 공통 처리기의 JSON이다. model을 두면 FastAPI가 text/html로 문서화한다.
JSON_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}
    }
    for code in ERROR_CODES
}
router = APIRouter(
    prefix=PREFIX,
    tags=["mailbox-authentication"],
    responses={code: {"model": ErrorResponse} for code in ERROR_CODES},
)


class AuthorizationStart(BaseModel):
    """서버 flow·토큰을 포함하지 않는 Microsoft 동의 URL."""

    authorization_url: str


class MailboxConnection(BaseModel):
    """토큰·암호문·Microsoft 식별자·키 버전 없이 연결 상태만 담는다."""

    mailbox_id: UUID
    state: Literal["connected", "disconnected"]
    revision: int
    updated_at: datetime
    # 공급자가 갱신을 거부해 제품에서 다시 연결해야 하는 연결. 외부에서 동의를 복구해도
    # 여기서 다시 연결하기 전까지 토큰을 주지 않는다.
    reconnect_required: bool
    reconnect_reason: (
        Literal[
            "invalid_grant",
            "interaction_required",
            "login_required",
            "consent_required",
            "token_missing",
        ]
        | None
    )
    reconnect_required_at: datetime | None


class MailboxConnections(BaseModel):
    """현재 세션 소유자의 연결 목록."""

    items: list[MailboxConnection]


def configure_mailbox_auth(app: FastAPI, value: MailboxAuthSettings) -> None:
    """기본 비활성을 유지하며 명시 활성화 때만 전용 암호기·callback 어댑터를 준비한다.

    Args:
        app: 기존 운영 로그인 설정을 검증한 플랫폼 앱.
        value: 서버 환경 또는 검사에서 명시 주입한 사서함 설정.

    Returns:
        None: DB·Microsoft 통신이나 Secret/IAM 변경 없이 앱 상태를 준비한다.
    """
    auth = app.state.microsoft_auth
    value.validate_configuration(auth)
    app.state.mailbox_auth = value
    app.state.mailbox_cipher = None
    app.state.mailbox_oauth = None
    app.state.mailbox_tokens = None
    if value.enabled:
        assert value.encryption_key is not None
        try:
            app.state.mailbox_cipher = MailboxCipher(
                key=value.encryption_key, key_version=value.key_version
            )
        except ApplicationError:
            raise ValueError("사서함 전용 암호화 키와 고정 버전 설정을 확인하세요.") from None
        app.state.mailbox_oauth = PersonalMicrosoftOAuth(
            auth.client_id, auth.client_secret, value.redirect_uri
        )
        app.state.mailbox_tokens = MailboxTokenProvider(
            settings=app.state.settings,
            adapter=app.state.mailbox_oauth,
            cipher=app.state.mailbox_cipher,
        )


def mailbox_provider(request: Request) -> PersonalMicrosoftOAuth:
    """비활성 경로는 DB·공급자 호출 전에 닫고 기존 서버 어댑터만 반환한다.

    Args:
        request: 플랫폼 HTTP 요청.

    Returns:
        PersonalMicrosoftOAuth: 사서함 전용 callback을 가진 서버 어댑터.
    """
    if not request.app.state.mailbox_auth.enabled:
        raise HTTPException(404)
    configuration(request)
    return request.app.state.mailbox_oauth


def mutation_token(request: Request) -> str:
    """사서함 시작·해제의 동일 출처와 기존 세션 결속 CSRF 헤더를 검사한다.

    Args:
        request: Origin·세션 쿠키·X-CSRF-Token을 포함한 변경 요청.

    Returns:
        str: 이후 DB 현재 세션 검사에 사용할 원문 쿠키 값.
    """
    auth = configuration(request)
    if request.url.query or request.headers.get("origin") != auth.app_origin.rstrip("/"):
        raise HTTPException(403)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401)
    supplied = request.headers.get(CSRF_HEADER, "")
    if not hmac.compare_digest(supplied.encode(), csrf_token(token).encode()):
        raise HTTPException(403)
    return token


@router.post(
    "/start",
    response_model=SuccessResponse[AuthorizationStart],
    operation_id="platform_start_mailbox_link",
    openapi_extra=ACTIVATION,
)
async def start(
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[AuthorizationStart]:
    """현재 운영 세션의 사서함 인가를 시작하고 별도 Lax 결속 쿠키와 URL만 반환한다.

    Args:
        request: 동일 출처·세션 CSRF를 확인할 요청.
        response: 별도 사서함 결속 쿠키를 설정할 응답.
        provider: 사서함 callback 전용 서버 어댑터.

    Returns:
        SuccessResponse[AuthorizationStart]: 서버 인가가 저장된 Microsoft URL.
    """
    token = mutation_token(request)
    binding = token_urlsafe(32)
    url = await start_mailbox_link(
        request.app.state.settings,
        session_token=token,
        browser_binding=binding,
        now=datetime.now(UTC),
        adapter=provider,
    )
    result: SuccessResponse[AuthorizationStart] = SuccessResponse(
        data=AuthorizationStart(authorization_url=url),
        meta=ResponseMeta(request_id=request.state.request_id),
    )
    response.set_cookie(
        BINDING_COOKIE, binding, max_age=300, secure=True, httponly=True, samesite="lax", path="/"
    )
    return result


@router.post(
    "/callback",
    response_class=HTMLResponse,
    responses=JSON_ERRORS,
    operation_id="platform_relay_mailbox_callback",
    openapi_extra={**ACTIVATION, **FORM_BODY},
)
async def callback_relay(
    request: Request, provider: PersonalMicrosoftOAuth = Depends(mailbox_provider)
) -> HTMLResponse:
    """교차 사이트 form_post를 같은 출처의 완료 POST로 전달하는 무상태 문서만 반환한다.

    현재 요청에 Lax 쿠키가 없어도 이 단계에서 인가를 소비·교환·저장하지 않는다.
    code/state는 URL·스크립트에 넣지 않고 escape한 hidden 입력 두 개로만 전달한다.

    Args:
        request: Microsoft form_post 또는 동일한 제한 형식의 입력.
        provider: 기본 비활성 경로를 먼저 닫는 의존성. 이 단계에서는 호출하지 않는다.

    Returns:
        HTMLResponse: 고정된 같은 출처의 완료 경로로 제출하는 캐시 금지 문서.
    """
    parameters = await read_callback_form(request)
    values = dict(parameters)
    if (
        len(values) != len(parameters)
        or "error" in values
        or not values.get("state")
        or not values.get("code", "").strip()
    ):
        raise reject_callback()
    nonce = token_urlsafe(32)
    document = (
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>사서함 연결</title>'
        "<p>사서함 연결을 완료하고 있습니다.</p>"
        f'<form method="post" action="{PREFIX}/complete">'
        f'<input type="hidden" name="state" value="{escape(values["state"], quote=True)}">'
        f'<input type="hidden" name="code" value="{escape(values["code"], quote=True)}">'
        "</form><noscript>브라우저에서 JavaScript를 허용한 뒤 다시 연결해 주세요.</noscript>"
        f'<script nonce="{nonce}">'
        "const f=document.forms[0];"
        'const fail=()=>{document.querySelector("p").textContent="사서함 연결을 완료하지 못했습니다. 다시 시도해 주세요."};'
        'fetch(f.action,{method:"POST",credentials:"same-origin",'
        'body:new URLSearchParams(new FormData(f)),redirect:"manual"})'
        '.then(r=>{if(r.type==="opaqueredirect"){location.replace("/")}else{fail()}})'
        ".catch(fail);</script></html>"
    )
    return HTMLResponse(
        document,
        headers={
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/complete",
    status_code=303,
    response_class=RedirectResponse,
    operation_id="platform_complete_mailbox_link",
    openapi_extra={**ACTIVATION, **FORM_BODY},
)
async def complete(
    request: Request, provider: PersonalMicrosoftOAuth = Depends(mailbox_provider)
) -> RedirectResponse:
    """동일 출처 POST의 실제 세션·사서함 결속·인가를 검증해 커밋 후 고정 루트로 이동한다.

    callback은 일회 state와 별도 브라우저 결속으로 CSRF를 검사한다. 앞의 무상태
    문서가 성공을 보장하지 않으며 현재 세션·권한 검사는 이 요청과 저장 시 다시 수행한다.

    Args:
        request: Lax 세션·사서함 결속 쿠키와 form 입력을 포함하는 요청.
        provider: 기존 MSAL 사서함 검증 어댑터.

    Returns:
        RedirectResponse: 암호화 저장 커밋 후 토큰·연결 ID 없는 앱 루트의303 응답.
    """
    auth = configuration(request)
    if request.headers.get("origin") != auth.app_origin.rstrip("/"):
        raise HTTPException(403)
    parameters = await read_callback_form(request)
    await complete_and_store_mailbox_link(
        request.app.state.settings,
        parameters,
        session_token=request.cookies.get(SESSION_COOKIE),
        browser_binding=request.cookies.get(BINDING_COOKIE, ""),
        now=datetime.now(UTC),
        adapter=provider,
        cipher=request.app.state.mailbox_cipher,
    )
    response = RedirectResponse(auth.app_origin.rstrip("/") + "/", status_code=303)
    response.delete_cookie(BINDING_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    return response


@router.post(
    "/{mailbox_id}/disconnect",
    status_code=204,
    operation_id="platform_disconnect_mailbox",
    openapi_extra=ACTIVATION,
)
async def disconnect(
    mailbox_id: UUID, request: Request, provider: PersonalMicrosoftOAuth = Depends(mailbox_provider)
) -> Response:
    """현재 세션 소유자의 저장 자격증명만 제거한다.

    Args:
        mailbox_id: 해제할 서버 연결 ID.
        request: 동일 출처·세션 CSRF를 확인할 요청.
        provider: 기본 비활성을 검사하는 의존성. 공급자를 호출하지 않는다.

    Returns:
        Response: 현재 소유자의 암호문 제거가 커밋된204 응답.
    """
    token = mutation_token(request)
    await disconnect_mailbox(request.app.state.settings, mailbox_id=mailbox_id, session_token=token)
    return Response(status_code=204)


@router.get(
    "",
    response_model=SuccessResponse[MailboxConnections],
    operation_id="platform_list_mailboxes",
    openapi_extra=ACTIVATION,
)
async def list_connections(
    request: Request,
    response: Response,
    provider: PersonalMicrosoftOAuth = Depends(mailbox_provider),
) -> SuccessResponse[MailboxConnections]:
    """현재 운영 세션 소유자의 사서함 연결 상태만 읽는다.

    읽기라 CSRF 헤더는 요구하지 않고 `/me`와 같은 Origin·세션 검사를 재사용한다.

    Args:
        request: 운영 세션 쿠키를 포함한 요청.
        response: 기존 세션 결속 CSRF 헤더를 받을 응답.
        provider: 기본 비활성을 검사하는 의존성. 공급자를 호출하지 않는다.

    Returns:
        SuccessResponse[MailboxConnections]: 연결 ID·상태·개정·변경 시각과 재연결 필요
        여부·사유·시각 목록.
    """
    user = await operational_identity(request, response)
    rows = await list_mailboxes(request.app.state.settings, owner_user_id=user.id)
    return SuccessResponse(
        data=MailboxConnections(
            items=[
                MailboxConnection(
                    **row, reconnect_required=row["reconnect_required_at"] is not None
                )
                for row in rows
            ]
        ),
        meta=ResponseMeta(request_id=request.state.request_id),
    )
