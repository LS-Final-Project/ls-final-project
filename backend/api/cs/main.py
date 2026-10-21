from fastapi import FastAPI

from backend.api.common.application import create_application
from backend.shared.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    return create_application("cs", settings)
