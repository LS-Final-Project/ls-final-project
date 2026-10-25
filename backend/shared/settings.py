"""Process settings with a production guard for development authentication."""

import re
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ServiceName = Literal["cs", "tft", "platform"]


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
    database_url: SecretStr | None = None
    cloud_run_service: str | None = Field(default=None, validation_alias="K_SERVICE")

    @model_validator(mode="after")
    def validate_auth_environment(self) -> Self:
        """개발 인증의 운영 유입과 불완전한 업무 중계 설정을 시작 시 거부한다.

        Args:
            없음. 모델에 주입된 환경·인증·고정 서비스 설정을 확인한다.

        Returns:
            Self: 환경과 인증 경계가 일치하는 설정.
        """
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
