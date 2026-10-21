from fastapi import FastAPI

from backend.api.common.application import create_application
from backend.api.platform.auth.routes import dev_router
from backend.api.platform.routes import router
from backend.shared.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = create_application("platform", settings)
    app.include_router(router)
    if app.state.settings.dev_auth_enabled and app.state.settings.environment in {"local", "test"}:
        app.include_router(dev_router)
    return app
