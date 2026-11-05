"""C/S → platform calls for customer mail (KAN-110): approved sends and their status.

The platform owns the mailbox, the send queue and delivery (KAN-74). The C/S service hands an
approved message over, keeps the intent ID, and reads the status back. Nothing here reads the
platform's tables. Disabled by default: without `CS_MAIL_ENABLED` the send routes answer 404.
"""

from collections.abc import Callable
from typing import Any, Self
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.shared.service_auth import metadata_token

SEND_INTENTS = "/internal/v1/platform/mail/send-intents"
# Platform statuses after which nothing more will happen to an intent.
SETTLED = ("ACCEPTED", "FAILED")


class CustomerMailSettings(BaseSettings):
    """Where the platform's internal mail API is, once the operator enables it."""

    model_config = SettingsConfigDict(
        env_prefix="CS_MAIL_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    enabled: bool = False
    # The platform service URL: ID token audience and request origin (same as MAIL_SEND_SERVICE_URL).
    platform_url: str = ""

    def check(self) -> Self:
        if not self.enabled:
            return self
        parsed = urlsplit(self.platform_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or any(c.isspace() for c in self.platform_url)
        ):
            raise ValueError("CS_MAIL_PLATFORM_URL must be an HTTPS origin without a path")
        return self


class PlatformUnreachable(Exception):
    """No connection: the platform never received the request."""


class PlatformUnknown(Exception):
    """The request may have reached the platform, but no usable answer came back."""


class PlatformRejected(Exception):
    """The platform answered with a client error; no intent exists for this request."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class PlatformMail:
    """HTTP client for the platform's internal send API, authenticated as the C/S runtime."""

    # 5xx answers whose error code means "refused, nothing stored" rather than "unknown".
    # The mail API has none: any 5xx may have stored an intent.
    known_server_refusals: frozenset[str] = frozenset()

    def __init__(
        self,
        base_url: str,
        token_provider: Callable[[str], str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.token_provider = token_provider
        self.transport = transport

    async def _call(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        import asyncio

        try:
            token = await asyncio.to_thread(self.token_provider, self.base_url)
        except HTTPException:
            raise PlatformUnreachable() from None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20, connect=5),
                follow_redirects=False,
                transport=self.transport,
            ) as http:
                response = await http.request(
                    method, self.base_url + path, json=body, headers=headers
                )
        except httpx.ConnectError:
            raise PlatformUnreachable() from None
        except httpx.HTTPError:
            raise PlatformUnknown() from None
        try:
            payload = response.json()
        except ValueError:
            raise PlatformUnknown() from None
        if response.status_code >= 500:
            error = payload.get("error") if isinstance(payload, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            if code in self.known_server_refusals:
                raise PlatformRejected(response.status_code, code)
            raise PlatformUnknown()
        if response.status_code >= 400:
            code = "PLATFORM_ERROR"
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                code = str(payload["error"].get("code") or code)[:64]
            raise PlatformRejected(response.status_code, code)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise PlatformUnknown()
        return payload["data"]

    async def create_intent(self, body: dict[str, Any]) -> dict[str, Any]:
        """Hands an approved message to the platform. Same version and content returns the
        same intent, so a retry after an unknown outcome does not send twice."""
        return await self._call("POST", SEND_INTENTS, body)

    async def read_intent(self, intent_id: UUID) -> dict[str, Any]:
        return await self._call("GET", f"{SEND_INTENTS}/{intent_id}", None)


def configure_customer_mail(app: FastAPI, value: CustomerMailSettings, **overrides: Any) -> None:
    """Keeps the settings and the platform client on the app. Disabled means no client.

    Args:
        app: C/S app.
        value: Settings; checked when enabled.
        **overrides: Test injection: `token` (callable audience → ID token), `transport`.
    """
    app.state.customer_mail = value.check()
    app.state.platform_mail = None
    if not value.enabled:
        return
    app.state.platform_mail = PlatformMail(
        value.platform_url, overrides.get("token") or metadata_token, overrides.get("transport")
    )


def platform_mail(request: Request) -> PlatformMail:
    """The platform client, or 404 while customer mail is disabled."""
    client = getattr(request.app.state, "platform_mail", None)
    if client is None:
        raise HTTPException(404)
    return client


def unsettled(send: dict[str, Any]) -> bool:
    """A send whose outcome is not final: the platform may still send it or did not say."""
    if send["request_status"] in ("PENDING", "REQUEST_UNKNOWN"):
        return True
    return send["request_status"] == "REGISTERED" and send["send_status"] not in SETTLED
