"""서버 전용 인가 시도를 저장하고 Microsoft 교환 전에 한 번만 소비한다."""

from collections.abc import Sequence
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from backend.api.platform.auth.callback import (
    MicrosoftSubject,
    OAuthAdapter,
    OAuthPurpose,
    PendingAuthorization,
    complete_callback,
    reject_callback,
)
from backend.shared.database import connection
from backend.shared.settings import Settings


async def save_authorization(settings: Settings, pending: PendingAuthorization) -> None:
    """인가 시도를 브라우저에 노출하지 않고 플랫폼 DB에 저장한다.

    Args:
        settings: 플랫폼 DB 연결 설정.
        pending: MSAL이 생성한 서버 전용 flow와 결속·만료 정보.

    Returns:
        None: 저장 트랜잭션을 커밋한다.
    """
    if not pending.state or pending.expires_at.tzinfo is None:
        raise reject_callback()
    async with connection(settings) as conn:
        await conn.execute(
            "DELETE FROM identity.oauth_authorizations WHERE expires_at<=clock_timestamp()"
        )
        await conn.execute(
            "INSERT INTO identity.oauth_authorizations "
            "(state_hash,purpose,browser_binding_hash,owner_user_id,expires_at,provider_flow) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (
                sha256(pending.state.encode()).hexdigest(),
                pending.purpose.value,
                pending.browser_binding_hash,
                pending.owner_user_id,
                pending.expires_at,
                Jsonb(dict(pending.provider_flow)),
            ),
        )


async def complete_stored_callback(
    settings: Settings,
    parameters: Sequence[tuple[str, str]],
    *,
    purpose: OAuthPurpose,
    browser_binding: str,
    session_user_id: UUID | None,
    now: datetime,
    adapter: OAuthAdapter,
) -> MicrosoftSubject:
    """결속된 인가 시도를 원자적으로 삭제·커밋한 뒤 토큰을 교환한다.

    다른 브라우저·목적·사용자의 요청은 정상 인가 시도를 소비하지 않는다.
    공급자 검증이 실패해도 이미 소비한 시도는 복구하지 않으며 새 로그인이 필요하다.
    동기 어댑터는 스레드에서 실행해 이벤트 루프를 막지 않는다.

    Args:
        settings: 플랫폼 DB 연결 설정.
        parameters: 중복 키를 보존한 callback query 목록.
        purpose: 호출 경로에서 정한 인가 목적.
        browser_binding: 시작한 브라우저의 예측 불가능한 결속값.
        session_user_id: 현재 서비스 사용자. 사서함 연결 시 시작 사용자와 같아야 한다.
        now: 시간대 정보가 있는 현재 시각.
        adapter: MSAL 교환과 ID 토큰 검증 어댑터.

    Returns:
        MicrosoftSubject: 검증된 Microsoft 식별자. 서비스 세션은 생성하지 않는다.
    """
    callback = dict(parameters)
    if (
        len(callback) != len(parameters)
        or "error" in callback
        or not callback.get("state")
        or not callback.get("code", "").strip()
        or not browser_binding
        or now.tzinfo is None
        or (purpose is OAuthPurpose.MAILBOX and session_user_id is None)
    ):
        raise reject_callback()
    owner = session_user_id if purpose is OAuthPurpose.MAILBOX else None
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "DELETE FROM identity.oauth_authorizations "
            "WHERE state_hash=%s AND purpose=%s AND browser_binding_hash=%s "
            "AND owner_user_id IS NOT DISTINCT FROM %s::uuid "
            "AND expires_at>%s AND expires_at>clock_timestamp() "
            "RETURNING browser_binding_hash,expires_at,provider_flow,owner_user_id",
            (
                sha256(callback["state"].encode()).hexdigest(),
                purpose.value,
                sha256(browser_binding.encode()).hexdigest(),
                owner,
                now,
            ),
        )
        row = await cursor.fetchone()
    # 연결 종료 시 삭제를 커밋한 뒤 외부 토큰 교환을 수행한다.
    if row is None:
        raise reject_callback()
    pending = PendingAuthorization(
        purpose=purpose,
        state=callback["state"],
        browser_binding_hash=row["browser_binding_hash"],
        expires_at=row["expires_at"],
        provider_flow=row["provider_flow"],
        owner_user_id=row["owner_user_id"],
    )
    return await run_in_threadpool(
        complete_callback,
        pending,
        parameters,
        purpose=purpose,
        browser_binding=browser_binding,
        session_user_id=session_user_id,
        now=now,
        adapter=adapter,
    )
