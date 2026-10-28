"""메일 소비자에게 주입할 서버 내부 토큰 제공자. Graph·재시도는 수행하지 않는다."""

from uuid import UUID

from pydantic import SecretStr

from backend.api.platform.auth.mailbox import acquire_stored_mailbox_token
from backend.api.platform.auth.mailbox_vault import MailboxCipher
from backend.api.platform.auth.microsoft import PersonalMicrosoftOAuth
from backend.shared.settings import Settings


class MailboxTokenProvider:
    """인증 의존성을 서버에서 결속하고 소비자에게 두 ID만 받는 메서드를 제공한다."""

    def __init__(
        self, *, settings: Settings, adapter: PersonalMicrosoftOAuth, cipher: MailboxCipher
    ) -> None:
        """기존 플랫폼 설정·사서함 어댑터·보관 암호기를 명시 주입받는다.

        Args:
            settings: 플랫폼 런타임 DB 설정.
            adapter: 기존 설정으로 준비한 Microsoft 사서함 어댑터.
            cipher: 동일 연결을 보관하는 서버 전용 암호기.

        Returns:
            None: DB·공급자 호출 없이 서버 내부 의존성만 결속한다.
        """
        self._settings = settings
        self._adapter = adapter
        self._cipher = cipher

    async def get_mailbox_access_token(
        self, *, mailbox_id: UUID, owner_user_id: UUID, force_refresh: bool = False
    ) -> SecretStr:
        """현재 소유자·권한·연결을 재검사하고 저장 완료된 유효 토큰만 반환한다.

        호출자는 요청이나 저장된 작업을 인증하고 소유자 ID를 복원해야 한다.
        HTTP 인증·업무 권한 검사·Graph 요청·401 이후 재시도는 이 메서드가 수행하지 않는다.
        `force_refresh=True`면 같은 재검사 뒤 캐시 토큰을 건너뛰고 MSAL로 갱신해 저장 커밋 후 반환한다.
        언제 강제 갱신을 부를지는 호출자(수집 계약)가 정한다.

        Args:
            mailbox_id: 서버에 저장된 사서함 연결 ID.
            owner_user_id: 인증한 요청 또는 신뢰할 서버 작업에서 복원한 소유자 ID.
            force_refresh: True면 유효한 캐시 토큰이 있어도 갱신한다. 기본값은 기존 동작이다.

        Returns:
            SecretStr: 기존 보관 경계의 현재 상태 확인·저장 커밋을 마친 access token 하나.

        Raises:
            ApplicationError: 접근 거부·미연결·재연결 필요 또는 일시적인 토큰 제공 실패.
        """
        return await acquire_stored_mailbox_token(
            self._settings,
            mailbox_id=mailbox_id,
            owner_user_id=owner_user_id,
            adapter=self._adapter,
            cipher=self._cipher,
            force_refresh=force_refresh,
        )
