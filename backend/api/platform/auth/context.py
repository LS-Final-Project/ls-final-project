"""검증된 운영 세션을 업무 서비스의 공통 사용자 모델로 변환하는 내부 경계."""

from backend.api.platform.auth.sessions import load_session
from backend.shared.access import Principal
from backend.shared.settings import Settings


async def load_session_principal(settings: Settings, token: str | None) -> Principal:
    """현재 운영 세션의 사용자·역할·관리 범위를 공통 Principal로 반환한다.

    KAN-78의 세션 조회와 공통 모델을 재사용한다. 별도 SQL이나 권한 캐시를 만들지
    않으며 이메일·표시 이름·Microsoft 식별자·원문 세션은 결과에 포함하지 않는다.
    HTTP 헤더를 읽거나 중계하지 않으며 내부 호출자 인증을 대신하지 않는다.

    Args:
        settings: 플랫폼 실행 계정으로 연결하는 DB 설정.
        token: HTTP 인증 경계가 운영 세션 쿠키에서 얻은 원문 토큰.

    Returns:
        Principal: 조회 시점의 사용자 ID·역할·부서·명시 부여된 관리 범위.

    Raises:
        HTTPException: 세션·등록 조건 위반은 401, DB 연결·조회 실패는 503.
    """
    user = await load_session(settings, token)
    return Principal(
        user_id=user.id,
        roles=frozenset(user.roles),
        dept=user.dept,
        managed_departments=frozenset(user.managed_departments),
        managed_projects=frozenset(user.managed_projects),
    )
