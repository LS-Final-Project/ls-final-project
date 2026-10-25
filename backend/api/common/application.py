"""Common liveness routes, request IDs and safe error responses."""

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.responses import Response

from backend import __version__
from backend.api.common.readiness import readiness
from backend.shared.errors import ApplicationError
from backend.shared.logs import log_context, trace_id
from backend.shared.models import (
    ErrorData,
    ErrorResponse,
    HealthData,
    ResponseMeta,
    SuccessResponse,
)
from backend.shared.service_auth import PlatformCaller
from backend.shared.settings import ServiceName, Settings

logger = logging.getLogger(__name__)
request_logger = logging.getLogger("backend.request")

HTTP_ERRORS = {
    400: ("BAD_REQUEST", "Invalid request."),
    401: ("AUTH_REQUIRED", "Authentication is required."),
    403: ("FORBIDDEN", "Access is forbidden."),
    404: ("NOT_FOUND", "Resource not found."),
    405: ("METHOD_NOT_ALLOWED", "Method not allowed."),
    422: ("VALIDATION_ERROR", "Request validation failed."),
    429: ("RATE_LIMITED", "Too many requests."),
    503: ("DEPENDENCY_UNAVAILABLE", "A required service is unavailable."),
    501: ("NOT_IMPLEMENTED", "This contract endpoint is not implemented yet."),
}


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[dict[str, str]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorData(
            code=code,
            message=message,
            retryable=status_code in {429, 502, 503, 504},
            details=details or [],
        ),
        meta=ResponseMeta(request_id=request.state.request_id),
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code, headers=headers)


def create_application(service: ServiceName, settings: Settings | None = None) -> FastAPI:
    settings = (settings or Settings()).model_copy(update={"service_name": service})
    production = settings.environment == "production"
    app = FastAPI(
        title={"cs": "C/S API", "tft": "TFT API", "platform": "Platform API"}[service],
        version=__version__,
        description="Process liveness only. Business routes are added by their owning service.",
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.service = service
    app.state.platform_caller = None
    if settings.business_relay_enabled and service in {"cs", "tft"}:
        audience = settings.business_cs_url if service == "cs" else settings.business_tft_url
        app.state.platform_caller = PlatformCaller(audience, settings.business_platform_email)

    @app.exception_handler(ApplicationError)
    async def handle_application_error(
        request: Request, exception: ApplicationError
    ) -> JSONResponse:
        return error_response(request, exception.status_code, exception.code, exception.message)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            request.state.request_id = UUID(request.headers.get("X-Request-ID", ""))
        except ValueError:
            request.state.request_id = uuid4()
        started = time.perf_counter()
        with log_context(
            service=service,
            request_id=request.state.request_id,
            trace_id=trace_id(request.headers.get("X-Cloud-Trace-Context")),
        ):
            try:
                response = await call_next(request)
            except Exception as error:
                # Exception strings can include provider responses or credentials:
                # log the type only (KAN-70).
                logger.error(
                    "Unhandled request error",
                    extra={"error_type": type(error).__name__},
                )
                response = error_response(
                    request, 500, "INTERNAL_ERROR", "An internal error occurred."
                )
            # One line per request. The path has no query string, which can carry codes.
            request_logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
        response.headers["X-Request-ID"] = str(request.state.request_id)
        return response

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exception: HTTPException) -> JSONResponse:
        code, message = HTTP_ERRORS.get(exception.status_code, ("HTTP_ERROR", "Request failed."))
        return error_response(
            request, exception.status_code, code, message, headers=exception.headers
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exception: RequestValidationError
    ) -> JSONResponse:
        details = [
            {"field": ".".join(map(str, item["loc"])), "type": item["type"]}
            for item in exception.errors()
        ]
        return error_response(
            request, 422, "VALIDATION_ERROR", "Request validation failed.", details=details
        )

    async def health(request: Request) -> SuccessResponse[HealthData]:
        return SuccessResponse(
            data=HealthData(service=service),
            meta=ResponseMeta(request_id=request.state.request_id),
        )

    responses: dict[int | str, dict[str, Any]] = {
        500: {"model": ErrorResponse, "description": "Internal error"}
    }
    app.add_api_route(
        "/health",
        health,
        methods=["GET"],
        response_model=SuccessResponse[HealthData],
        tags=["health"],
        responses=responses,
        name="process_health",
        description="Checks this process only; does not probe DB, OAuth, mail or model providers.",
    )
    router = APIRouter(prefix=f"/api/v1/{service}", tags=["health"])
    router.add_api_route(
        "/ready",
        readiness,
        methods=["GET"],
        name=f"{service}_readiness",
        responses={503: {"model": ErrorResponse}},
    )
    router.add_api_route(
        "/health",
        health,
        methods=["GET"],
        response_model=SuccessResponse[HealthData],
        responses=responses,
        name=f"{service}_health",
        description="Checks this service process only; does not authorize business operations.",
    )
    app.include_router(router)
    return app
