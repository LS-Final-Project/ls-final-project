"""KAN-201: push API/Job configuration; private VAPID material belongs only to the Job."""

import base64
import re
from typing import Self
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

HOST = "fcm.googleapis.com"


def decoded(value: str, length: int) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid push key")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(raw) != length or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
        raise ValueError("Invalid push key")
    return raw


def public_key(value: str) -> str:
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded(value, 65))
    return value


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        len(value) > 2048
        or not value.startswith(f"https://{HOST}/")
        or parsed.netloc != HOST
        or not re.fullmatch(r"/(?:fcm/send|wp)/[A-Za-z0-9_:-]+", parsed.path)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid push endpoint")
    return value


class PushSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOTIFICATION_PUSH_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    enabled: bool = False
    public_key: str = ""
    service_url: str = ""
    invoker_email: str = ""
    project_number: str = ""
    job: str = "notification-push"
    region: str = "asia-northeast3"
    batch_size: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def configured(self) -> Self:
        if self.enabled:
            public_key(self.public_key)
            if (
                not re.fullmatch(r"https://[a-z0-9-]+\.[a-z0-9.-]+\.run\.app", self.service_url)
                or not re.fullmatch(
                    r"[a-z][a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", self.invoker_email
                )
                or not re.fullmatch(r"[1-9][0-9]{5,19}", self.project_number)
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,61}[a-z0-9]", self.job)
                or self.region != "asia-northeast3"
            ):
                raise ValueError("Invalid push dispatch configuration")
        return self


class PushWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOTIFICATION_PUSH_", extra="ignore", hide_input_in_errors=True
    )
    enabled: bool = False
    private_key: SecretStr = SecretStr("")
    public_key: str = ""
    subject: str = ""
    batch_size: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def configured(self) -> Self:
        if self.enabled:
            private = ec.derive_private_key(
                int.from_bytes(decoded(self.private_key.get_secret_value(), 32)), ec.SECP256R1()
            )
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

            actual = private.public_key().public_bytes(
                Encoding.X962, PublicFormat.UncompressedPoint
            )
            if actual != decoded(self.public_key, 65) or not re.fullmatch(
                r"mailto:[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+", self.subject
            ):
                raise ValueError("Invalid VAPID configuration")
        return self
