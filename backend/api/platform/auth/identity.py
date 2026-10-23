"""개발 세션과 Microsoft 로그인에서 공유하는 등록 사용자 조회 및 검증."""

from fastapi import HTTPException

from backend.api.platform.auth.callback import MicrosoftSubject
from backend.api.platform.auth.schemas import Identity
from backend.shared.database import connection
from backend.shared.settings import Settings

IDENTITY_QUERY = (
    "SELECT u.id,u.tid,u.oid,u.email,u.display_name,u.dept, "
    "array_agg(r.role ORDER BY r.role) AS roles FROM identity.users u "
    "JOIN identity.user_roles r ON r.user_id=u.id "
)
REGISTERED_IDENTITY_QUERY = (
    IDENTITY_QUERY + "WHERE u.tid=%s AND u.oid=%s AND u.is_active GROUP BY u.id"
)


def identity(row: dict | None) -> Identity:
    """DB 사용자와 역할이 기존 Identity 계약을 충족하는지 검증한다.

    Args:
        row: DB에서 조회한 사용자와 역할. 조회 결과가 없으면 None.

    Returns:
        Identity: 역할 및 부서 조건을 충족한 사용자.

    Raises:
        HTTPException: 사용자가 없거나 역할·부서 정보가 유효하지 않은 경우.
    """
    if not row:
        raise HTTPException(401)
    try:
        return Identity.model_validate(row)
    except ValueError:
        raise HTTPException(401) from None


async def registered_identity(settings: Settings, subject: MicrosoftSubject) -> Identity:
    """검증된 tid와 oid에 일치하는 활성 사용자와 기존 DB 역할만 조회한다.

    이메일로 계정을 매칭하거나 미등록 사용자·역할을 자동 생성하지 않는다.

    Args:
        settings: 플랫폼 DB 연결 설정.
        subject: 공급자 어댑터가 검증한 Microsoft 식별자.

    Returns:
        Identity: DB에 등록된 활성 사용자와 현재 역할.

    Raises:
        HTTPException: 등록된 활성 사용자 또는 유효한 역할·부서 정보가 없는 경우.
    """
    async with connection(settings) as conn:
        cursor = await conn.execute(
            REGISTERED_IDENTITY_QUERY,
            (subject.tid, subject.oid),
        )
        row = await cursor.fetchone()
    return identity(row)
