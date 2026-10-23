"""공개 API에 연결하기 전의 서비스 로그인 흐름. 세션 발급은 수행하지 않는다."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from backend.api.platform.auth.attempts import complete_stored_callback, save_authorization
from backend.api.platform.auth.callback import OAuthAdapter, OAuthPurpose, PendingAuthorization
from backend.api.platform.auth.identity import registered_identity
from backend.api.platform.auth.schemas import Identity
from backend.shared.settings import Settings


class OAuthStartAdapter(Protocol):
    """기존 MSAL 구현과 합성 어댑터가 공유하는 인가 시작 경계."""

    def begin(
        self,
        purpose: OAuthPurpose,
        *,
        browser_binding: str,
        now: datetime,
        owner_user_id: UUID | None = None,
    ) -> tuple[str, PendingAuthorization]:
        """로그인 URL과 서버 전용 인가 시도를 생성한다.

        Args:
            purpose: 서비스 로그인 또는 사서함 연결 목적.
            browser_binding: 브라우저에 결속할 예측 불가능한 값.
            now: 시간대 정보가 있는 인가 시작 시각.
            owner_user_id: 사서함 연결의 기존 사용자. 서비스 로그인은 None.

        Returns:
            tuple[str, PendingAuthorization]: 인가 URL과 서버에만 보관할 flow.
        """
        ...


async def start_login(
    settings: Settings,
    *,
    browser_binding: str,
    now: datetime,
    adapter: OAuthStartAdapter,
) -> str:
    """로그인 목적의 MSAL flow를 생성하고 저장한 뒤 인가 URL만 반환한다.

    브라우저 결속값의 생성·쿠키 설정은 향후 HTTP 경계의 책임이다.
    동기 MSAL 호출은 스레드에서 실행하고 저장 실패 시 URL을 반환하지 않는다.

    Args:
        settings: 플랫폼 DB 연결 설정.
        browser_binding: 서버가 생성해 요청 브라우저에 전달할 결속값.
        now: 시간대 정보가 있는 인가 시작 시각.
        adapter: 인가 URL과 MSAL flow를 생성하는 공급자 어댑터.

    Returns:
        str: 서버 저장이 완료된 인가 시도의 Microsoft 로그인 URL.
    """
    url, pending = await run_in_threadpool(
        adapter.begin, OAuthPurpose.LOGIN, browser_binding=browser_binding, now=now
    )
    await save_authorization(settings, pending)
    return url


async def complete_login(
    settings: Settings,
    parameters: Sequence[tuple[str, str]],
    *,
    browser_binding: str,
    now: datetime,
    adapter: OAuthAdapter,
) -> Identity:
    """인가 시도 일회 소비·토큰 검증 후 DB에 등록된 로그인 사용자를 조회한다.

    로그인 목적과 소유자 없음으로 고정해 사서함 연결과 구분한다.
    결과는 서버 내부용이며 이 함수가 세션이나 역할을 생성하지 않는다.

    Args:
        settings: 플랫폼 DB 연결 설정.
        parameters: 중복 키를 보존한 callback query 목록.
        browser_binding: 로그인을 시작한 브라우저의 결속 쿠키 값.
        now: 시간대 정보가 있는 callback 처리 시각.
        adapter: code 교환과 ID 토큰 검증을 담당하는 공급자 어댑터.

    Returns:
        Identity: 검증된 tid와 oid에 일치하는 활성 등록 사용자와 DB 역할.
    """
    subject = await complete_stored_callback(
        settings,
        parameters,
        purpose=OAuthPurpose.LOGIN,
        browser_binding=browser_binding,
        session_user_id=None,
        now=now,
        adapter=adapter,
    )
    return await registered_identity(settings, subject)
