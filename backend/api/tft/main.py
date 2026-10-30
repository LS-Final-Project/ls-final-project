from fastapi import FastAPI

from backend.api.common.application import create_application
from backend.api.tft.rfp.jobs import TftProcessingSettings, configure_processing_jobs
from backend.api.tft.routes import router
from backend.shared.settings import Settings


def create_app(
    settings: Settings | None = None, processing: TftProcessingSettings | None = None
) -> FastAPI:
    app = create_application("tft", settings)
    configure_processing_jobs(app, processing or TftProcessingSettings())
    app.include_router(router)
    return app
