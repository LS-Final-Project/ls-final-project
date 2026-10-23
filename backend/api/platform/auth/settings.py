"""기본 비활성 상태의 Microsoft 서버 로그인 설정."""

from urllib.parse import urlsplit
from uuid import UUID

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

CALLBACK_PATH = "/api/v1/platform/auth/callback"


class MicrosoftAuthSettings(BaseSettings):
    """화면 계약 확인과 HTTPS Web callback 등록 후 명시적으로 활성화할 설정."""

    model_config = SettingsConfigDict(
        env_prefix="MICROSOFT_AUTH_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )
    enabled: bool = False
    client_id: UUID | None = None
    client_secret: SecretStr | None = None
    app_origin: str = ""
    redirect_uri: str = ""

    def validate_configuration(self, *, dev_auth_enabled: bool) -> None:
        """활성화할 때 서버 자격증명·동일 출처 HTTPS callback과 모드 분리를 검사한다.

        Args:
            dev_auth_enabled: 기존 개발 계정 선택 기능의 활성 여부.

        Returns:
            None: 비활성이거나 필요한 설정을 충족하면 반환한다.

        Raises:
            ValueError: 활성화에 필요한 설정이 없거나 개발 인증과 함께 사용한 경우.
        """
        if not self.enabled:
            return
        try:
            origin = urlsplit(self.app_origin)
            port = origin.port
        except ValueError:
            raise ValueError("Microsoft 로그인 출처와 HTTPS callback 설정을 확인하세요.") from None
        if (
            dev_auth_enabled
            or self.client_id is None
            or self.client_secret is None
            or not self.client_secret.get_secret_value().strip()
            or origin.scheme != "https"
            or (port is not None and port == 0)
            or any(character.isspace() for character in self.app_origin)
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
            or self.redirect_uri != self.app_origin.rstrip("/") + CALLBACK_PATH
        ):
            raise ValueError(
                "Microsoft 로그인은 서버 자격증명과 동일 출처 HTTPS 설정이 필요합니다."
            )
