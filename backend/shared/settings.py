"""Process settings with a production guard for development authentication."""

import logging
import os
import re
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.shared.tracing import TRACING_BLOCKED, tracing_requested

ServiceName = Literal["cs", "tft", "platform"]
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    environment: Literal["local", "test", "production"] = "local"
    service_name: ServiceName = "cs"
    port: int = Field(default=8080, ge=1, le=65535)
    dev_auth_enabled: bool = False
    business_relay_enabled: bool = False
    business_cs_url: str = ""
    business_tft_url: str = ""
    business_platform_email: str = ""
    cs_draft_verification_enabled: bool = True
    cs_manual_data_origin: Literal["demo", "real"] | None = None
    database_url: SecretStr | None = None
    cloud_run_service: str | None = Field(default=None, validation_alias="K_SERVICE")
    # 추적 전송 정책(KAN-89)이 정해지기 전에는 local에서만 명시적으로 허용할 수 있다.
    ai_tracing_allowed: bool = False

    @model_validator(mode="after")
    def validate_auth_environment(self) -> Self:
        """추적 전송, 개발 인증의 운영 유입과 불완전한 업무 중계 설정을 시작 시 거부한다.

        Args:
            없음. 모델에 주입된 환경·인증·고정 서비스 설정을 확인한다.

        Returns:
            Self: 환경과 인증 경계가 일치하는 설정.
        """
        # LangSmith는 프로세스 환경을 읽으므로 .env가 아니라 os.environ을 본다.
        if tracing_requested(os.environ):
            if self.environment != "local" or not self.ai_tracing_allowed:
                raise ValueError(TRACING_BLOCKED)
            logger.warning("LangSmith tracing is allowed for local development only (KAN-89)")
        if self.environment == "production" and self.dev_auth_enabled:
            raise ValueError("Development authentication is forbidden in production")
        if self.cloud_run_service and self.environment != "production":
            raise ValueError("Cloud Run services must use ENVIRONMENT=production")
        if self.business_relay_enabled:
            if self.dev_auth_enabled:
                raise ValueError("Business relay requires operational authentication")
            for value in (self.business_cs_url, self.business_tft_url):
                parsed = urlsplit(value)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.path
                    or parsed.query
                    or parsed.fragment
                    or parsed.port not in {None, 443}
                    or any(c.isspace() for c in value)
                ):
                    raise ValueError("Business upstream must be an HTTPS origin without a path")
            if self.business_cs_url == self.business_tft_url or not re.fullmatch(
                r"[a-z][a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com",
                self.business_platform_email,
            ):
                raise ValueError("Distinct upstreams and a platform service account are required")
        return self
