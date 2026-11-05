"""Public Web Push registration: current session, existing Origin/CSRF policy."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.api.platform.auth.mailbox_http import mutation_token
from backend.api.platform.auth.routes import guard_local, guard_origin, me
from backend.api.platform.auth.schemas import Identity
from backend.api.platform.notifications import push_repository as repository
from backend.api.platform.notifications.push_settings import decoded, endpoint, public_key
from backend.api.platform.notifications.routes import query_keys
from backend.shared.database import connection
from backend.shared.models import ResponseMeta, SuccessResponse

router = APIRouter(prefix="/api/v1/platform/push", tags=["push"])
User = Annotated[SuccessResponse[Identity], Depends(me)]


class PublicKey(BaseModel):
    public_key: str


class Endpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    endpoint: str = Field(max_length=2048)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return endpoint(value)


class Keys(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    p256dh: str = Field(max_length=87)
    auth: str = Field(max_length=22)

    @field_validator("p256dh")
    @classmethod
    def validate_public(cls, value: str) -> str:
        return public_key(value)

    @field_validator("auth")
    @classmethod
    def validate_auth(cls, value: str) -> str:
        decoded(value, 16)
        return value


class Subscription(Endpoint):
    keys: Keys


def require_push(request: Request, *, mutation: bool = False) -> None:
    if not request.app.state.notification_push.enabled:
        raise HTTPException(404)
    query_keys(request, set())
    if mutation:
        if request.app.state.microsoft_auth.enabled:
            mutation_token(request)
        else:
            guard_local(request)
            guard_origin(request)


@router.get(
    "/public-key",
    response_model=SuccessResponse[PublicKey],
    operation_id="platform_push_public_key",
)
async def key(request: Request, response: Response, user: User) -> SuccessResponse[PublicKey]:
    require_push(request)
    response.headers["Cache-Control"] = "no-store"
    return SuccessResponse(
        data=PublicKey(public_key=request.app.state.notification_push.public_key),
        meta=ResponseMeta(request_id=request.state.request_id),
    )


@router.put("/subscription", status_code=204, operation_id="platform_put_my_push_subscription")
async def subscribe(payload: Subscription, request: Request, user: User) -> Response:
    require_push(request, mutation=True)
    async with connection(request.app.state.settings) as conn:
        await repository.subscribe(
            conn, user.data.id, payload.endpoint, payload.keys.p256dh, payload.keys.auth
        )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.delete(
    "/subscription", status_code=204, operation_id="platform_delete_my_push_subscription"
)
async def unsubscribe(payload: Endpoint, request: Request, user: User) -> Response:
    require_push(request, mutation=True)
    async with connection(request.app.state.settings) as conn:
        await repository.unsubscribe(conn, user.data.id, payload.endpoint)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
