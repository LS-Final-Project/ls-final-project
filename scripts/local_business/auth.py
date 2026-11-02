"""Local provider fixtures only; product sessions, roles and CSRF remain authoritative.

These RSA identities are NOT evidence of Microsoft or Google authentication. Keys are
created by local setup, never copied from an earlier verification artifact.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import jwt
from fastapi import HTTPException
from pydantic import SecretStr

from backend.api.platform.auth.callback import (
    MicrosoftSubject,
    OAuthPurpose,
    PendingAuthorization,
    reject_callback,
)
from backend.api.platform.auth.settings import MicrosoftAuthSettings
from backend.shared.service_auth import PlatformCaller

CS_ORIGIN = "https://cs.local-business.invalid"
TFT_ORIGIN = "https://tft.local-business.invalid"
PLATFORM_ORIGIN = "https://platform.local-business.invalid"
PLATFORM_SA = "platform@local-business.iam.gserviceaccount.com"
CS_SA = "cs@local-business.iam.gserviceaccount.com"
TFT_SA = "tft@local-business.iam.gserviceaccount.com"


def local_origin(value: str) -> str:
    """Accept only a canonical HTTPS localhost origin with an explicit port."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = port is not None and 1 <= port <= 65535
    except ValueError:
        valid = False
    if not valid or value != f"https://localhost:{port}":
        raise ValueError("INVALID_LOCAL_WEB_ORIGIN")
    return value


class LocalLogin:
    """Supply four synthetic subjects; existing routes consume/bind state and sessions."""

    def __init__(self, web_origin: str):
        self.web_origin = local_origin(web_origin)

    def begin(self, purpose, *, browser_binding, now, owner_user_id=None):
        if purpose is not OAuthPurpose.LOGIN:
            raise HTTPException(403)
        state = token_urlsafe(32)
        return self.web_origin + "/local/authorize?" + urlencode({"state": state}), (
            PendingAuthorization(
                purpose=purpose,
                state=state,
                browser_binding_hash=sha256(browser_binding.encode()).hexdigest(),
                expires_at=now + timedelta(minutes=5),
                provider_flow={"local_synthetic_only": True},
                owner_user_id=owner_user_id,
            )
        )

    def redeem(self, pending, callback):
        code = callback.get("code")
        if (
            pending.purpose is not OAuthPurpose.LOGIN
            or pending.provider_flow != {"local_synthetic_only": True}
            or code not in {"local-1", "local-2", "local-3", "local-4"}
        ):
            raise reject_callback()
        return MicrosoftSubject(
            UUID("11111111-1111-4111-8111-111111111111"),
            UUID("00000000-0000-4000-8000-00000000000" + code[-1]),
        )


class LocalIdentity:
    """Use only the explicit local key directory; no environment/cloud credentials."""

    def __init__(self, state: Path, web_origin: str):
        self.auth = state / "auth"
        self.web_origin = local_origin(web_origin)

    def _key(self, filename: str) -> str:
        path = self.auth / filename
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError("INVALID_LOCAL_KEY_FILE")
        return path.read_text(encoding="utf-8")

    def auth_settings(self) -> MicrosoftAuthSettings:
        return MicrosoftAuthSettings(
            _env_file=None,
            enabled=True,
            client_id=UUID(int=1),
            client_secret=SecretStr("local-fixture-not-an-external-secret"),
            app_origin=self.web_origin,
            redirect_uri=self.web_origin + "/api/v1/platform/auth/callback",
        )

    def oauth(self) -> LocalLogin:
        return LocalLogin(self.web_origin)

    def caller(self, audience: str, email: str) -> PlatformCaller:
        self._destination(audience, email)
        public = self._key("caller-public.pem")

        class LocalSigningKey:
            def get_signing_key_from_jwt(self, raw):
                return SimpleNamespace(key=public)

        caller = PlatformCaller(audience, email)
        caller.keys = LocalSigningKey()
        return caller

    @staticmethod
    def _destination(audience: str, email: str) -> None:
        if audience not in {CS_ORIGIN, TFT_ORIGIN, PLATFORM_ORIGIN} or email not in {
            PLATFORM_SA,
            CS_SA,
            TFT_SA,
        }:
            raise ValueError("INVALID_LOCAL_SERVICE_IDENTITY")

    def token(self, audience: str, email: str) -> str:
        self._destination(audience, email)
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "iss": "https://accounts.google.com",
                "aud": audience,
                "sub": "local-fixture:" + email,
                "email": email,
                "email_verified": True,
                "iat": now,
                "exp": now + timedelta(seconds=60),
            },
            self._key("caller-key.pem"),
            algorithm="RS256",
        )
