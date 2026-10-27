"""기본 비활성 사서함 HTTP·전용 보관 키 설정. 공용 리소스는 생성하지 않는다."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.platform.auth.settings import MicrosoftAuthSettings

MAILBOX_CALLBACK = "/api/v1/platform/auth/mailbox/callback"


class MailboxAuthSettings(BaseSettings):
    """운영자가 고정 Secret 버전을 주입한 뒤 별도로 켤 사서함 연결 설정."""

    model_config = SettingsConfigDict(
        env_prefix="MAILBOX_AUTH_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    enabled: bool = False
    encryption_key: SecretStr | None = Field(default=None, repr=False)
    key_version: str = ""
    redirect_uri: str = ""

    def validate_configuration(self, auth: MicrosoftAuthSettings) -> None:
        """활성 상태에서만 기존 로그인과 별도 callback·키 설정의 존재를 검사한다.

        Args:
            auth: 이미 검증한 Microsoft 로그인 설정.

        Returns:
            None: 기본 비활성 또는 필요한 설정을 충족한 경우.
        """
        if self.enabled and (
            not auth.enabled
            or self.encryption_key is None
            or self.redirect_uri != auth.app_origin.rstrip("/") + MAILBOX_CALLBACK
        ):
            raise ValueError(
                "사서함 연결에는 운영 로그인과 전용 키·동일 출처 callback이 필요합니다."
            )
