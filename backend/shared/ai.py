"""Shared Gen AI client configuration; business prompts stay in their own modules."""

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import partial
from math import isfinite
from pathlib import Path
from typing import Literal

import google.auth
from google import genai
from google.auth.credentials import Credentials
from google.auth.impersonated_credentials import Credentials as ImpersonatedCredentials
from google.genai import types
from pydantic import BaseModel, Field, SecretStr, TypeAdapter
from pydantic_settings import BaseSettings, SettingsConfigDict


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    ai_provider: Literal["gemini", "vertex"] = "gemini"
    google_cloud_project: str = Field(default="proj-skkuls0106-183255377938", min_length=1)
    google_cloud_location: str = Field(default="global", pattern=r"^[a-z0-9-]+$")
    google_cloud_embedding_location: str | None = Field(default=None, pattern=r"^[a-z0-9-]+$")
    gemini_model: str = Field(default="gemini-3.6-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    gemini_max_output_tokens: int = Field(default=1024, ge=1, le=65536)
    gemini_timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    gemini_api_key: SecretStr | None = Field(default=None, repr=False)


PRICES = Path(__file__).with_name("ai_prices.json")
MILLION = Decimal(1_000_000)


@dataclass
class Usage:
    """공급자가 알려준 토큰 수. 미제공 값은 None이고 총합은 공급자 total_tokens만 믿는다.

    thought_tokens는 output_tokens에 더하지 않는다. raw는 공급자 usage_metadata를 응답 키 그대로
    옮긴 원문이다. billable_input_tokens는 input_tokens - cached_input_tokens다. Gemini는 캐시를
    쓰지 않은 호출에서 cachedContentTokenCount를 생략하므로(공급자 명세) 그때 캐시는 0으로 본다.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    thought_tokens: int | None = None
    cached_input_tokens: int | None = None
    total_tokens: int | None = None
    raw: dict[str, object] | None = None
    billable_input_tokens: int | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        """입력 토큰이 있을 때만 캐시를 뺀 청구 입력 토큰을 계산한다.

        Returns:
            None: billable_input_tokens를 채운다. input_tokens가 None이면 None이다.
        """
        if self.input_tokens is not None:
            self.billable_input_tokens = self.input_tokens - (self.cached_input_tokens or 0)


@dataclass
class Generation:
    text: str
    usage: Usage
    model_version: str | None
    finish_reasons: list[str]
    # 요청한 모델 ID, SDK 호출 시간, 시도 횟수(HttpRetryOptions attempts=1), 완료 시각(UTC).
    model: str | None = None
    elapsed_ms: int | None = None
    attempts: int = 1
    finished_at: datetime | None = None


@dataclass
class Embedding:
    values: list[float]
    input_tokens: float | None
    billable_characters: int | None
    model: str | None = None
    elapsed_ms: int | None = None
    attempts: int = 1
    finished_at: datetime | None = None


class ModelPrice(BaseModel):
    """모델 하나의 100만 토큰당 단가. 값은 공급자 공식 가격표에서 사람이 옮긴다."""

    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    cached_input_per_million: Decimal = Field(ge=0)
    currency: str = Field(min_length=1)
    as_of: date


@dataclass
class Cost:
    """호출 하나의 예상 금액. 계산할 수 없는 항목은 None이고 반올림하지 않는다."""

    input: Decimal | None = None
    output: Decimal | None = None
    cached_input: Decimal | None = None
    thought: Decimal | None = None
    total: Decimal | None = None
    currency: str | None = None
    price_as_of: date | None = None


def load_price_table(path: Path = PRICES) -> dict[str, ModelPrice]:
    """모델 ID별 단가표를 읽는다.

    Args:
        path: {모델 ID: ModelPrice} JSON 파일. 기본 파일은 빈 표다.

    Returns:
        dict[str, ModelPrice]: 검증된 단가표.
    """
    return TypeAdapter(dict[str, ModelPrice]).validate_json(path.read_bytes())


def estimate_cost(usage: Usage, model: str, table: dict[str, ModelPrice]) -> Cost:
    """사용량과 단가표로 예상 금액을 계산한다. 토큰을 두 번 세지 않는다.

    청구 입력(캐시 제외)은 입력 단가, 캐시 입력은 캐시 단가, 답변과 추론은 각각 출력 단가로 따로
    계산한다. 추론은 답변에 합치지 않는다.

    Args:
        usage: 호출 사용량.
        model: 단가표에서 찾을 모델 ID.
        table: load_price_table 결과.

    Returns:
        Cost: 단가가 없으면 모든 값이 None. 토큰이 None인 항목은 None이고 total은 있는 항목만
            더한다(하나도 없으면 None).
    """
    price = table.get(model)
    if price is None:
        return Cost()

    def part(tokens: int | None, per_million: Decimal) -> Decimal | None:
        """토큰 수 × 100만 토큰당 단가.

        Args:
            tokens: 토큰 수. None이면 계산하지 않는다.
            per_million: 100만 토큰당 단가.

        Returns:
            Decimal | None: 금액 또는 None.
        """
        return None if tokens is None else Decimal(tokens) * per_million / MILLION

    parts = {
        "input": part(usage.billable_input_tokens, price.input_per_million),
        "output": part(usage.output_tokens, price.output_per_million),
        "cached_input": part(usage.cached_input_tokens, price.cached_input_per_million),
        "thought": part(usage.thought_tokens, price.output_per_million),
    }
    known = [value for value in parts.values() if value is not None]
    return Cost(
        **parts,
        total=sum(known, Decimal(0)) if known else None,
        currency=price.currency,
        price_as_of=price.as_of,
    )


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
    """Use the selected provider explicitly; no credential or provider fallback."""
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
    if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value().strip():
        raise ValueError("Gemini API requires GEMINI_API_KEY when creating a real client")
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
    started = time.perf_counter()
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=settings.gemini_max_output_tokens,
            response_mime_type="application/json" if response_schema else None,
            response_schema=response_schema,
        ),
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    raw = response.usage_metadata
    usage = (
        Usage(
            raw.prompt_token_count,
            raw.candidates_token_count,
            raw.thoughts_token_count,
            raw.cached_content_token_count,
            raw.total_token_count,
            raw.model_dump(mode="json", by_alias=True, exclude_none=True),
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
        model=settings.gemini_model,
        elapsed_ms=elapsed_ms,
        finished_at=datetime.now(UTC),
    )


def embed(
    client: genai.Client,
    model: str,
    text: str,
    *,
    dimensions: int | None = None,
    task_type: str | None = None,
) -> Embedding:
    started = time.perf_counter()
    response = client.models.embed_content(
        model=model,
        contents=text,
        config=types.EmbedContentConfig(
            output_dimensionality=dimensions,
            task_type=task_type,
            # Developer API rejects auto_truncate even when it is False.
            auto_truncate=False if client.vertexai else None,
        ),
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
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
        model=model,
        elapsed_ms=elapsed_ms,
        finished_at=datetime.now(UTC),
    )
