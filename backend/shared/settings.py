"""Process settings with a production guard for development authentication."""

from typing import Literal, Self

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
    database_url: SecretStr | None = None
    cloud_run_service: str | None = Field(default=None, validation_alias="K_SERVICE")

    @model_validator(mode="after")
    def validate_auth_environment(self) -> Self:
        if self.environment == "production" and self.dev_auth_enabled:
            raise ValueError("Development authentication is forbidden in production")
        if self.cloud_run_service and self.environment != "production":
            raise ValueError("Cloud Run services must use ENVIRONMENT=production")
        return self
