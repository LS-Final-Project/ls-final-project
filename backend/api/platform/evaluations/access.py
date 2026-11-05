"""운영 세션과 서버 지정 UUID로 평가 접근을 제한한다."""

import hmac
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.platform.auth.context import load_session_principal
from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
from backend.shared.access import Principal


class EvaluationAccessSettings(BaseSettings):
    """기본 거부이며 업무 역할·이메일로 평가자를 자동 지정하지 않는 설정."""

    model_config = SettingsConfigDict(
        env_prefix="EVALUATION_", extra="ignore", hide_input_in_errors=True
    )
    evaluator_user_ids: frozenset[UUID] = frozenset()
    local_admission_enabled: bool = False
    smoke_lease_seconds: int = Field(default=30, ge=1, le=300)
    approved_config_hashes: frozenset[str] = frozenset()


async def require_session(request: Request) -> Principal:
    """현재 운영 세션의 등록 사용자를 돌려준다(업무별 권한은 호출자가 본다).

    Args:
        request: 서버 세션 쿠키를 가진 요청.

    Returns:
        Principal: 세션이 유효한 사용자. 로그인이 꺼졌거나 세션이 없으면 401이다.
    """
    if not request.app.state.microsoft_auth.enabled:
        raise HTTPException(401)
    return await load_session_principal(
        request.app.state.settings, request.cookies.get(SESSION_COOKIE)
    )


async def require_evaluator(request: Request) -> Principal:
    """현재 운영 세션의 등록 사용자가 지정 평가자인지 검사한다.

    Args:
        request: 서버 세션 쿠키와 앱의 서버 전용 허용 목록을 가진 요청.

    Returns:
        Principal: 세션 유효성과 평가자 지정이 확인된 사용자. 데이터 접근 권한은 별도다.
    """
    principal = await require_session(request)
    access: EvaluationAccessSettings = request.app.state.evaluation_access
    if principal.user_id not in access.evaluator_user_ids:
        raise HTTPException(403)
    return principal


def require_evaluation_write(request: Request) -> None:
    """평가 변경 요청이 허용 웹 출처와 현재 세션에 결속됐는지 검사한다.

    Args:
        request: 운영 세션 쿠키와 Origin·CSRF 헤더가 담긴 요청.

    Returns:
        None: 검사 통과 시 반환하며 위반은 403으로 거부한다.
    """
    origin = request.app.state.microsoft_auth.app_origin.rstrip("/")
    token = request.cookies.get(SESSION_COOKIE)
    supplied = request.headers.get(CSRF_HEADER, "")
    if request.headers.get("origin") != origin or not token:
        raise HTTPException(403)
    if not hmac.compare_digest(supplied.encode(), csrf_token(token).encode()):
        raise HTTPException(403)
