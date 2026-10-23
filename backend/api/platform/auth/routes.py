"""운영·개발 인증의 현재 사용자 조회와 로컬 전용 계정 선택 경로."""

import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request, Response

from backend.api.platform.auth.http import operational_identity
from backend.api.platform.auth.identity import IDENTITY_QUERY, identity
from backend.api.platform.auth.schemas import DemoAccounts, Identity, SelectAccount
from backend.shared.database import connection
from backend.shared.models import ResponseMeta, SuccessResponse

router = APIRouter(prefix="/api/v1/platform", tags=["identity"])
dev_router = APIRouter(prefix="/api/v1/platform/dev", tags=["local-development"])
COOKIE = "team4_dev_session"


def guard_local(request: Request) -> None:
    settings = request.app.state.settings
    if settings.environment not in {"local", "test"} or not settings.dev_auth_enabled:
        raise HTTPException(404)


def guard_origin(request: Request) -> None:
    # CLI requests may omit Origin. Browser state changes must come from our local UI.
    origin = request.headers.get("origin")
    if origin and origin not in {"http://localhost:5173", "http://127.0.0.1:5173"}:
        raise HTTPException(403)


async def current_identity(request: Request) -> Identity:
    guard_local(request)
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401)
    digest = sha256(token.encode()).hexdigest()
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            IDENTITY_QUERY + "JOIN identity.dev_sessions s ON s.user_id=u.id "
            "WHERE s.token_hash=%s AND s.expires_at>now() AND u.is_active "
            "GROUP BY u.id",
            (digest,),
        )
        row = await cursor.fetchone()
    # Roles and activation are reread from DB on each request.
    return identity(row)


@router.get(
    "/me",
    response_model=SuccessResponse[Identity],
    operation_id="platform_get_me",
    responses={
        200: {
            "headers": {
                "X-CSRF-Token": {
                    "description": "Microsoft 서버 세션에 결속한 변경 요청용 값. Identity 계약은 유지한다.",
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def me(request: Request, response: Response) -> SuccessResponse[Identity]:
    """운영 로그인 또는 명시적 개발 모드의 현재 사용자를 반환한다.

    Args:
        request: 현재 플랫폼 요청.
        response: 운영 세션 CSRF 헤더를 설정할 응답.

    Returns:
        SuccessResponse[Identity]: DB에서 조회한 현재 사용자와 역할.
    """
    if request.app.state.microsoft_auth.enabled:
        user = await operational_identity(request, response)
    elif request.app.state.settings.dev_auth_enabled:
        user = await current_identity(request)
    else:
        # Microsoft 인증 비활성과 개발 인증 비활성에서는 인증을 허용하지 않는다.
        raise HTTPException(401)
    return SuccessResponse(data=user, meta=ResponseMeta(request_id=request.state.request_id))


@dev_router.get("/accounts", response_model=SuccessResponse[DemoAccounts])
async def accounts(request: Request) -> SuccessResponse[DemoAccounts]:
    guard_local(request)
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            IDENTITY_QUERY + "WHERE u.is_active GROUP BY u.id ORDER BY u.display_name"
        )
        rows = await cursor.fetchall()
    return SuccessResponse(
        data=DemoAccounts(items=[identity(row) for row in rows]),
        meta=ResponseMeta(request_id=request.state.request_id),
    )


@dev_router.post("/session", response_model=SuccessResponse[Identity])
async def select_account(
    payload: SelectAccount, request: Request, response: Response
) -> SuccessResponse[Identity]:
    guard_local(request)
    guard_origin(request)
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            IDENTITY_QUERY + "WHERE u.id=%s AND u.is_active GROUP BY u.id",
            (payload.user_id,),
        )
        user = identity(await cursor.fetchone())
        token = secrets.token_urlsafe(32)
        await conn.execute("DELETE FROM identity.dev_sessions WHERE expires_at <= now()")
        old_token = request.cookies.get(COOKIE)
        if old_token:
            await conn.execute(
                "DELETE FROM identity.dev_sessions WHERE token_hash=%s",
                (sha256(old_token.encode()).hexdigest(),),
            )
        await conn.execute(
            "INSERT INTO identity.dev_sessions(token_hash,user_id,expires_at) VALUES (%s,%s,%s)",
            (sha256(token.encode()).hexdigest(), user.id, datetime.now(UTC) + timedelta(hours=8)),
        )
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", max_age=28800, path="/api/v1/platform"
    )
    return SuccessResponse(data=user, meta=ResponseMeta(request_id=request.state.request_id))


@dev_router.delete("/session", status_code=204)
async def logout(request: Request, response: Response) -> None:
    guard_local(request)
    guard_origin(request)
    token = request.cookies.get(COOKIE)
    if token:
        async with connection(request.app.state.settings) as conn:
            await conn.execute(
                "DELETE FROM identity.dev_sessions WHERE token_hash=%s",
                (sha256(token.encode()).hexdigest(),),
            )
    response.delete_cookie(COOKIE, path="/api/v1/platform")
