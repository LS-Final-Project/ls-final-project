"""현재 서비스 세션과 사서함 인가 결과를 연결하는 내부 경계. 저장·HTTP는 미연결이다."""

from collections.abc import Sequence
from datetime import datetime

from starlette.concurrency import run_in_threadpool

from backend.api.platform.auth.attempts import consume_authorization, save_authorization
from backend.api.platform.auth.callback import OAuthPurpose, reject_callback
from backend.api.platform.auth.microsoft import MailboxAuthorization, PersonalMicrosoftOAuth
from backend.api.platform.auth.sessions import load_session
from backend.shared.settings import Settings


async def start_mailbox_link(
    settings: Settings,
    *,
    session_token: str | None,
    browser_binding: str,
    now: datetime,
    adapter: PersonalMicrosoftOAuth,
) -> str:
    """현재 활성 등록 사용자의 사서함 인가 시도를 저장하고 URL만 반환한다.

    요청 경계는 세션 쿠키·Origin·CSRF를 검사해야 한다. 브라우저가 보낸 사용자
    ID를 받지 않고 기존 세션 조회로 소유자를 결정한다. 토큰 저장은 수행하지 않는다.

    Args:
        settings: 플랫폼 런타임 DB 설정.
        session_token: 신뢰할 HTTP 경계가 세션 쿠키에서 읽은 원문 값.
        browser_binding: 서버가 생성한 브라우저 결속값.
        now: 시간대 정보가 있는 인가 시작 시각.
        adapter: 개인 Microsoft 사서함 어댑터.

    Returns:
        str: 인가 시도 저장이 완료된 Microsoft 동의 URL.
    """
    owner = await load_session(settings, session_token)
    url, pending = await run_in_threadpool(
        adapter.begin,
        OAuthPurpose.MAILBOX,
        browser_binding=browser_binding,
        now=now,
        owner_user_id=owner.id,
    )
    await save_authorization(settings, pending)
    return url


async def complete_mailbox_link(
    settings: Settings,
    parameters: Sequence[tuple[str, str]],
    *,
    session_token: str | None,
    browser_binding: str,
    now: datetime,
    adapter: PersonalMicrosoftOAuth,
) -> MailboxAuthorization:
    """일회 인가 소비와 교환 전후 현재 세션 검사를 거쳐 캐시를 인계한다.

    서비스 로그인 세션을 새로 만들거나 사서함 식별자로 기존 사용자를 바꾸지 않는다.
    결과는 보관 계층 전용 메모리 값이며 암호화·DB 저장이 완료된 연결 상태가 아니다.
    이후 보관 트랜잭션에서도 현재 소유자·활성·연결 상태와 경합을 검사해야 한다.

    Args:
        settings: 플랫폼 런타임 DB 설정.
        parameters: 중복 키를 보존한 callback 입력.
        session_token: 신뢰할 HTTP 경계에서 얻은 현재 세션 쿠키 값.
        browser_binding: 연결을 시작한 브라우저의 결속값.
        now: 시간대 정보가 있는 callback 처리 시각.
        adapter: 초기 토큰 교환과 서명·캐시 검증을 수행할 어댑터.

    Returns:
        MailboxAuthorization: 현재 소유자와 별도 사서함 식별자에 결속한 캐시.
    """
    owner = await load_session(settings, session_token)
    pending = await consume_authorization(
        settings,
        parameters,
        purpose=OAuthPurpose.MAILBOX,
        browser_binding=browser_binding,
        session_user_id=owner.id,
        now=now,
    )
    result = await run_in_threadpool(
        adapter.redeem_mailbox,
        pending,
        parameters,
        browser_binding=browser_binding,
        session_user_id=owner.id,
        now=now,
    )
    current_owner = await load_session(settings, session_token)
    if current_owner.id != owner.id or result.owner_user_id != owner.id:
        raise reject_callback()
    return result
