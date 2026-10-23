from fastapi import FastAPI

from backend.api.common.application import create_application
from backend.api.platform.auth.http import configure_auth
from backend.api.platform.auth.routes import dev_router
from backend.api.platform.auth.settings import MicrosoftAuthSettings
from backend.api.platform.routes import router
from backend.shared.settings import Settings


def create_app(
    settings: Settings | None = None, auth_settings: MicrosoftAuthSettings | None = None
) -> FastAPI:
    """플랫폼 앱과 기본 비활성 Microsoft 로그인 설정을 준비한다.

    Args:
        settings: 공통 런타임 설정. 없으면 환경에서 읽는다.
        auth_settings: Microsoft 로그인 설정. 없으면 전용 접두사 환경에서 읽는다.

    Returns:
        FastAPI: 로그인 계약과 기존 개발 경로를 포함한 플랫폼 앱.
    """
    app = create_application("platform", settings)
    configure_auth(app, auth_settings or MicrosoftAuthSettings())
    app.include_router(router)
    if app.state.settings.dev_auth_enabled and app.state.settings.environment in {"local", "test"}:
        app.include_router(dev_router)
    return app
