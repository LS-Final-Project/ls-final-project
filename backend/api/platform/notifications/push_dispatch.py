"""OIDC scheduler entry point: starts a fixed Job, never sends browser push in a request."""

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response

from backend.api.platform.mail.storage import GoogleAccessToken
from backend.api.platform.notifications.push_settings import PushSettings
from backend.shared.errors import ApplicationError
from backend.shared.service_auth import PlatformCaller

router = APIRouter(prefix="/internal/v1/platform/notifications", include_in_schema=False)


def unavailable() -> ApplicationError:
    return ApplicationError(503, "PUSH_DISPATCH_UNAVAILABLE", "Push dispatch unavailable.")


def configure_push(app: FastAPI, settings: PushSettings, **overrides: Any) -> None:
    app.state.notification_push = settings
    app.state.notification_push_caller = (
        overrides.get("caller") or PlatformCaller(settings.service_url, settings.invoker_email)
        if settings.enabled
        else None
    )

    async def token() -> str:
        # ADC discovery may contact metadata. Never perform it during API startup,
        # public subscription calls, OpenAPI generation or disabled configuration.
        try:
            provider = GoogleAccessToken(
                scope="https://www.googleapis.com/auth/cloud-platform", error=unavailable
            )
            return await provider()
        except Exception:
            raise unavailable() from None

    app.state.notification_push_token = overrides.get("token") or token
    app.state.notification_push_transport = overrides.get("transport")


@router.post("/push", status_code=202)
async def dispatch(request: Request) -> Response:
    caller = request.app.state.notification_push_caller
    if caller is None:
        raise HTTPException(404)
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer "):
        raise HTTPException(401)
    await asyncio.to_thread(caller.verify, values[0][7:])
    if request.query_params or await request.body():
        raise HTTPException(400)
    settings: PushSettings = request.app.state.notification_push
    # No request-derived job/env/overrides. A lost response can start a duplicate Job;
    # per-delivery DB claims, rather than this HTTP request, enforce send uniqueness.
    url = (
        "https://run.googleapis.com/v2/projects/"
        f"{settings.project_number}/locations/{settings.region}/jobs/{settings.job}:run"
    )
    try:
        async with asyncio.timeout(10):
            token = await request.app.state.notification_push_token()
            async with httpx.AsyncClient(
                transport=request.app.state.notification_push_transport,
                follow_redirects=False,
                trust_env=False,
                timeout=8,
            ) as client:
                response = await client.post(
                    url, json={}, headers={"Authorization": f"Bearer {token}"}
                )
        if response.status_code != 200:
            raise unavailable()
    except (httpx.HTTPError, TimeoutError):
        raise unavailable() from None
    return Response(status_code=202, headers={"Cache-Control": "no-store"})
