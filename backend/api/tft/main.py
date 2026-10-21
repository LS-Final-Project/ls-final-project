from fastapi import FastAPI

from backend.api.common.application import create_application
from backend.api.tft.routes import router
from backend.shared.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = create_application("tft", settings)
    app.include_router(router)
    return app
