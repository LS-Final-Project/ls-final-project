"""개발 세션과 분리된 서버 세션 저장 경계. 공개 API와 쿠키는 연결하지 않는다."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from secrets import token_urlsafe

from fastapi import HTTPException
from pydantic import SecretStr

from backend.api.platform.auth.callback import MicrosoftSubject
from backend.api.platform.auth.identity import IDENTITY_QUERY, REGISTERED_IDENTITY_QUERY, identity
from backend.api.platform.auth.schemas import Identity
from backend.shared.database import connection
from backend.shared.settings import Settings


@dataclass(frozen=True)
class IssuedSession:
    """쿠키 연결을 위해 서버 내부에서만 사용하는 발급 결과. 토큰 repr은 마스킹한다."""

    token: SecretStr
    user: Identity
    expires_at: datetime


async def issue_session(
    settings: Settings,
    subject: MicrosoftSubject,
    *,
    previous_token: str | None = None,
) -> IssuedSession:
    """검증된 Microsoft 사용자에게 새 세션을 발급하고 기존 브라우저 세션을 교체한다.

    호출자는 공급자가 검증한 subject만 전달해야 한다. 등록·활성·역할 조건을 DB에서
    다시 검사하며 사용자나 역할을 생성하지 않는다. 기존 세션 삭제와 새 세션 삽입은
    함께 커밋한다. DB 시각으로 8시간의 절대 만료를 적용하고 원문 토큰은 저장하지 않는다.

    Args:
        settings: 플랫폼 DB 연결 설정.
        subject: 기존 OAuth 검증 경계를 통과한 Microsoft 테넌트·사용자 식별자.
        previous_token: 교체할 기존 브라우저의 운영 세션 원문. 없으면 새 세션만 발급한다.

    Returns:
        IssuedSession: 마스킹된 원문 토큰, 현재 DB 사용자와 절대 만료 시각.

    Raises:
        HTTPException: 등록 조건을 충족하지 못하거나 DB 처리가 실패한 경우.
    """
    async with connection(settings) as conn:
        cursor = await conn.execute(REGISTERED_IDENTITY_QUERY, (subject.tid, subject.oid))
        user = identity(await cursor.fetchone())
        token = token_urlsafe(32)
        await conn.execute("DELETE FROM identity.sessions WHERE expires_at<=clock_timestamp()")
        if previous_token:
            await conn.execute(
                "DELETE FROM identity.sessions WHERE token_hash=%s",
                (sha256(previous_token.encode()).hexdigest(),),
            )
        cursor = await conn.execute(
            "INSERT INTO identity.sessions(token_hash,user_id,expires_at) "
            "VALUES (%s,%s,clock_timestamp()+interval '8 hours') RETURNING expires_at",
            (sha256(token.encode()).hexdigest(), user.id),
        )
        row = await cursor.fetchone()
        assert row is not None  # RETURNING 없는 성공은 없으며 원문 토큰을 오류에 포함하지 않는다.
        expires_at = row["expires_at"]
    return IssuedSession(token=SecretStr(token), user=user, expires_at=expires_at)


async def load_session(settings: Settings, token: str | None) -> Identity:
    """유효한 운영 세션으로 현재 활성 사용자·역할·부서를 조회한다.

    조회로 만료를 연장하지 않으며 개발 세션 테이블을 대체 인증으로 사용하지 않는다.

    Args:
        settings: 플랫폼 DB 연결 설정.
        token: 향후 HTTP 경계가 운영 세션 쿠키에서 읽을 원문 토큰.

    Returns:
        Identity: 조회 시점의 DB 활성 사용자와 유효한 역할·부서 정보.

    Raises:
        HTTPException: 세션이 없거나 만료·철회됐거나 사용자 조건이 유효하지 않은 경우.
    """
    if not token:
        raise HTTPException(401)
    async with connection(settings) as conn:
        cursor = await conn.execute(
            IDENTITY_QUERY + "JOIN identity.sessions s ON s.user_id=u.id "
            "WHERE s.token_hash=%s AND s.expires_at>clock_timestamp() AND u.is_active GROUP BY u.id",
            (sha256(token.encode()).hexdigest(),),
        )
        row = await cursor.fetchone()
    return identity(row)


async def revoke_session(settings: Settings, token: str | None) -> None:
    """운영 세션을 삭제하며 중복 철회와 토큰 누락은 같은 결과로 처리한다.

    공개 로그아웃 API에서 호출하기 전의 CSRF 검증과 쿠키 삭제는 HTTP 경계의 책임이다.
    DB 장애는 성공으로 숨기지 않는다.

    Args:
        settings: 플랫폼 DB 연결 설정.
        token: 철회할 운영 세션의 원문 토큰. 없으면 DB에 접근하지 않는다.

    Returns:
        None: 해당 세션을 서버에서 무효화한다.
    """
    if not token:
        return
    async with connection(settings) as conn:
        await conn.execute(
            "DELETE FROM identity.sessions WHERE token_hash=%s",
            (sha256(token.encode()).hexdigest(),),
        )
