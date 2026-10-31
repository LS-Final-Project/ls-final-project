"""등록된 활성 C/S 담당자만 조회하고 운영 중계의 재배정 대상을 검증한다."""

from collections.abc import Collection
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.api.platform.auth.routes import me
from backend.api.platform.auth.schemas import Identity
from backend.shared.access import CS_DEPARTMENT, Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse
from backend.shared.settings import Settings

router = APIRouter(prefix="/api/v1/platform", tags=["assignees"])
ACTIVE_CS = (
    "FROM identity.users u WHERE u.is_active AND EXISTS "
    "(SELECT 1 FROM identity.user_roles r WHERE r.user_id=u.id AND r.role='cs_agent')"
)


class Assignee(BaseModel):
    user_id: UUID
    display_name: str


class AssigneeList(BaseModel):
    items: list[Assignee]


class UserName(BaseModel):
    user_id: UUID
    display_name: str


class UserNameList(BaseModel):
    items: list[UserName]


class OwnerChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    owner_user_id: UUID


def require_cs_manager(roles: Collection[str], departments: Collection[str]) -> None:
    """C/S 부서 관리 권한이 없는 호출을 거부한다."""
    if "manager" not in roles or CS_DEPARTMENT not in departments:
        raise HTTPException(403)


async def validate_owner(settings: Settings, principal: Principal, body: bytes) -> None:
    """중계 전 역할·본문·활성 cs_agent 대상을 확인한다. identity는 플랫폼만 읽는다."""
    require_cs_manager(principal.roles, principal.managed_departments)
    try:
        change = OwnerChange.model_validate_json(body)
    except ValidationError:
        raise ApplicationError(422, "VALIDATION_ERROR", "Invalid owner assignment.") from None
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT u.id " + ACTIVE_CS + " AND u.id=%s", (change.owner_user_id,)
        )
        if await cursor.fetchone() is None:
            raise ApplicationError(422, "CS_INVALID_OWNER", "Select an active C/S agent.")


@router.get(
    "/assignees",
    response_model=SuccessResponse[AssigneeList],
    operation_id="platform_list_assignees",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_assignees(
    request: Request,
    response: Response,
    user_response: Annotated[SuccessResponse[Identity], Depends(me)],
    role: Annotated[Literal["cs_agent"], Query()] = "cs_agent",
) -> SuccessResponse[AssigneeList]:
    """재배정 가능한 활성 상담원의 ID·표시명만 반환한다. 계정·역할은 변경하지 않는다."""
    user = user_response.data
    require_cs_manager(user.roles, user.managed_departments)
    pairs = request.query_params.multi_items()
    if len(pairs) != len(dict(pairs)) or any(key != "role" for key, _ in pairs):
        raise HTTPException(400)
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT u.id AS user_id,u.display_name " + ACTIVE_CS + " ORDER BY u.display_name,u.id"
        )
        items = [Assignee(**row) for row in await cursor.fetchall()]
    response.headers["Cache-Control"] = "no-store"
    return SuccessResponse(
        data=AssigneeList(items=items), meta=ResponseMeta(request_id=request.state.request_id)
    )


@router.get(
    "/users",
    response_model=SuccessResponse[UserNameList],
    operation_id="platform_list_user_names",
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_user_names(
    request: Request,
    response: Response,
    _user: Annotated[SuccessResponse[Identity], Depends(me)],
    ids: Annotated[str, Query(max_length=3699)],
) -> SuccessResponse[UserNameList]:
    """화면이 업무 기록의 사용자 ID를 이름으로 묶어 읽는다. 이름만 낸다.

    tft는 identity를 읽지 못해 응답에 이름을 넣을 수 없다. 퇴직자도 기록의 이름이 보이도록
    활성 여부로 거르지 않고, 없는 ID는 응답에서 빠진다.

    Args:
        request: 현재 요청.
        response: 캐시 금지 헤더를 넣을 응답.
        _user: 로그인 확인.
        ids: 쉼표로 이은 사용자 UUID. 길이 제한이 100개까지만 받는다.

    Returns:
        SuccessResponse[UserNameList]: 찾은 사용자의 ID와 표시명.
    """
    try:
        wanted = [UUID(value) for value in ids.split(",")]
    except ValueError:
        raise ApplicationError(422, "VALIDATION_ERROR", "Invalid user IDs.") from None
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT id AS user_id,display_name FROM identity.users WHERE id=ANY(%s)", (wanted,)
        )
        items = [UserName(**row) for row in await cursor.fetchall()]
    response.headers["Cache-Control"] = "no-store"
    return SuccessResponse(
        data=UserNameList(items=items), meta=ResponseMeta(request_id=request.state.request_id)
    )
