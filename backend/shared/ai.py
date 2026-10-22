"""Shared Gen AI client configuration; business prompts stay in their own modules."""

import json
import time
from dataclasses import dataclass
from functools import partial
from math import isfinite
from typing import Literal

import google.auth
from google import genai
from google.auth.credentials import Credentials
from google.auth.impersonated_credentials import Credentials as ImpersonatedCredentials
from google.genai import types
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    ai_provider: Literal["vertex", "gemini"] = "vertex"
    google_cloud_project: str = Field(default="proj-skkuls0106-183255377938", min_length=1)
    google_cloud_location: str = Field(default="global", pattern=r"^[a-z0-9-]+$")
    google_cloud_embedding_location: str | None = Field(default=None, pattern=r"^[a-z0-9-]+$")
    gemini_model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    gemini_max_output_tokens: int = Field(default=1024, ge=1, le=65536)
    gemini_timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    gemini_api_key: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def require_explicit_developer_key(self) -> "AISettings":
        if self.ai_provider == "gemini" and not self.gemini_api_key:
            raise ValueError("Developer API mode requires GEMINI_API_KEY")
        return self


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    thought_tokens: int | None = None
    cached_input_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class Generation:
    text: str
    usage: Usage
    model_version: str | None
    finish_reasons: list[str]


@dataclass
class Embedding:
    values: list[float]
    input_tokens: float | None
    billable_characters: int | None


def runtime_credentials(
    project: str, service: Literal["cs", "tft"], *, source_credentials: Credentials | None = None
) -> Credentials:
    """Opt-in identity verification; requires existing caller impersonation permission."""
    if source_credentials is None:
        source_credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    return ImpersonatedCredentials(
        source_credentials=source_credentials,
        target_principal=f"{service}-runtime@{project}.iam.gserviceaccount.com",
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        lifetime=300,
    )


class RuntimeIdentityError(ValueError):
    """Safe diagnostic for runtime identity verification; never includes provider bodies."""


def refresh_runtime_credentials(credentials: Credentials, wait_seconds: int) -> None:
    """Wait only for a newly granted IAM token permission, without model retries."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request as AuthRequest

    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            credentials.refresh(partial(AuthRequest(), timeout=15))
            return
        except RefreshError as error:
            denied = False
            for argument in error.args:
                try:
                    body = json.loads(argument) if isinstance(argument, str) else argument
                    details = body.get("error", {}) if isinstance(body, dict) else {}
                    denied = denied or (
                        details.get("code") == 403
                        and details.get("status") == "PERMISSION_DENIED"
                        and any(
                            item.get("reason") == "IAM_PERMISSION_DENIED"
                            for item in details.get("details", [])
                            if isinstance(item, dict)
                        )
                    )
                except (ValueError, TypeError):
                    pass
            if not denied or time.monotonic() >= deadline:
                raise RuntimeIdentityError(
                    "Runtime token issuance failed; check temporary IAM binding and propagation"
                ) from None
            print(
                "Waiting for runtime IAM permission propagation; no model request was sent.",
                flush=True,
            )
            time.sleep(min(10, max(0, deadline - time.monotonic())))


def create_client(
    settings: AISettings, *, credentials: Credentials | None = None, embedding: bool = False
) -> genai.Client:
    """Use ADC on Vertex; never switch to a Developer API key after failure."""
    options = types.HttpOptions(
        api_version="v1" if settings.ai_provider == "vertex" else "v1beta",
        timeout=settings.gemini_timeout_ms,
        retry_options=types.HttpRetryOptions(attempts=1),
    )
    if settings.ai_provider == "vertex":
        # Explicit project/location select the OAuth path even if an API key is in the environment.
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=(
                settings.google_cloud_embedding_location or settings.google_cloud_location
                if embedding
                else settings.google_cloud_location
            ),
            credentials=credentials,
            http_options=options,
        )
    if credentials is not None:
        raise ValueError("OAuth credentials are only used by Vertex mode")
    assert settings.gemini_api_key is not None
    return genai.Client(
        vertexai=False,
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=options,
    )


def generate(
    client: genai.Client,
    settings: AISettings,
    prompt: str,
    *,
    response_schema: type[BaseModel] | None = None,
) -> Generation:
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=settings.gemini_max_output_tokens,
            response_mime_type="application/json" if response_schema else None,
            response_schema=response_schema,
        ),
    )
    raw = response.usage_metadata
    usage = (
        Usage(
            raw.prompt_token_count,
            raw.candidates_token_count,
            raw.thoughts_token_count,
            raw.cached_content_token_count,
            raw.total_token_count,
        )
        if raw
        else Usage()
    )
    return Generation(
        response.text or "",
        usage,
        response.model_version,
        [
            candidate.finish_reason.value
            for candidate in response.candidates or []
            if candidate.finish_reason is not None
        ],
    )


def embed(
    client: genai.Client,
    model: str,
    text: str,
    *,
    dimensions: int | None = None,
    task_type: str | None = None,
) -> Embedding:
    response = client.models.embed_content(
        model=model,
        contents=text,
        config=types.EmbedContentConfig(
            output_dimensionality=dimensions, task_type=task_type, auto_truncate=False
        ),
    )
    entries = response.embeddings or []
    if len(entries) != 1 or not entries[0].values:
        raise ValueError("Expected one nonempty embedding")
    entry = entries[0]
    values = entry.values or []
    if not all(isfinite(value) for value in values):
        raise ValueError("Embedding contains non-finite values")
    if dimensions is not None and len(values) != dimensions:
        raise ValueError("Embedding dimension does not match the requested dimension")
    if entry.statistics and entry.statistics.truncated:
        raise ValueError("Embedding input was truncated")
    return Embedding(
        values,
        entry.statistics.token_count if entry.statistics else None,
        response.metadata.billable_character_count if response.metadata else None,
    )
