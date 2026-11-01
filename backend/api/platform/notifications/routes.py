"""현재 세션의 알림만 조회하고 읽음 처리한다. 수신자 목록은 공개하지 않는다."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from backend.api.platform.auth.mailbox_http import mutation_token
from backend.api.platform.auth.routes import guard_local, guard_origin, me
from backend.api.platform.auth.schemas import Identity
from backend.api.platform.notifications import repository
from backend.api.platform.notifications.schemas import Notification, NotificationList, UnreadCount
from backend.shared.database import connection
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

router = APIRouter(
    prefix="/api/v1/platform/notifications",
    tags=["notifications"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
User = Annotated[SuccessResponse[Identity], Depends(me)]


def query_keys(request: Request, allowed: set[str]) -> None:
    pairs = request.query_params.multi_items()
    if len(pairs) != len(dict(pairs)) or any(key not in allowed for key, _ in pairs):
        raise HTTPException(400)


@router.get(
    "",
    response_model=SuccessResponse[NotificationList],
    operation_id="platform_list_my_notifications",
)
async def list_notifications(
    request: Request,
    response: Response,
    user: User,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> SuccessResponse[NotificationList]:
    query_keys(request, {"limit", "offset"})
    async with connection(request.app.state.settings) as conn:
        rows = await repository.list_mine(conn, user.data.id, limit + 1, offset)
    response.headers["Cache-Control"] = "no-store"
    return SuccessResponse(
        data=NotificationList(
            items=[Notification(**r) for r in rows[:limit]],
            next_offset=offset + limit if len(rows) > limit else None,
        ),
        meta=ResponseMeta(request_id=request.state.request_id),
    )


@router.get(
    "/unread-count",
    response_model=SuccessResponse[UnreadCount],
    operation_id="platform_count_my_unread_notifications",
)
async def count_notifications(
    request: Request, response: Response, user: User
) -> SuccessResponse[UnreadCount]:
    query_keys(request, set())
    async with connection(request.app.state.settings) as conn:
        count = await repository.unread_count(conn, user.data.id)
    response.headers["Cache-Control"] = "no-store"
    return SuccessResponse(
        data=UnreadCount(count=count), meta=ResponseMeta(request_id=request.state.request_id)
    )


@router.post("/{intent_id}/read", status_code=204, operation_id="platform_read_my_notification")
async def read_notification(intent_id: UUID, request: Request, user: User) -> Response:
    query_keys(request, set())
    if request.app.state.microsoft_auth.enabled:
        mutation_token(request)
    else:
        guard_local(request)
        guard_origin(request)
    async with connection(request.app.state.settings) as conn:
        if not await repository.mark_read(conn, user.data.id, intent_id):
            raise HTTPException(404)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
