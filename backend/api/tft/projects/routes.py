import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from backend.api.tft.projects import repository
from backend.api.tft.projects.access import (
    in_review_department,
    require_project_list,
    sees_project,
)
from backend.api.tft.projects.schemas import (
    ProjectDetail,
    ProjectList,
    ProjectSummary,
    RfpRequirement,
)
from backend.api.tft.rfp.integration import StoredRequirement, project_integration
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind_path_ids
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

router = APIRouter(
    prefix="/api/v1/tft/projects",
    tags=["projects"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse, "description": "No valid user context"},
        403: {"model": ErrorResponse, "description": "The role has no TFT scope"},
        404: {
            "model": ErrorResponse,
            "description": "Not found or outside the user's scope. Production requires an enabled "
            "business relay and a verified platform caller (KAN-178)",
        },
    },
)

PARTIAL = {"x-implementation-status": "partial"}


def cursor_error() -> ApplicationError:
    return ApplicationError(400, "TFT_INVALID_CURSOR", "The page cursor is invalid.")


def encode_cursor(created_at: datetime, project_id: UUID) -> str:
    raw = json.dumps({"c": created_at.isoformat(), "i": str(project_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        data = json.loads(base64.urlsafe_b64decode(value.encode()))
    except (binascii.Error, ValueError):
        raise cursor_error() from None
    # A cursor is client input: check its shape before parsing any value.
    if not isinstance(data, dict) or set(data) != {"c", "i"}:
        raise cursor_error()
    created_at, project_id = data["c"], data["i"]
    if not isinstance(created_at, str) or not isinstance(project_id, str):
        raise cursor_error()
    try:
        parsed_at = datetime.fromisoformat(created_at)
        parsed_id = UUID(project_id)
    except ValueError:
        raise cursor_error() from None
    if parsed_at.tzinfo is None:
        raise cursor_error()
    try:
        # Offsets near the datetime limits overflow when normalised.
        parsed_at = parsed_at.astimezone(UTC)
    except (OverflowError, ValueError):
        raise cursor_error() from None
    return parsed_at, parsed_id


@router.get(
    "",
    response_model=SuccessResponse[ProjectList],
    operation_id="tft_list_projects",
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def list_projects(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=200),
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[ProjectList]:
    require_project_list(principal)
    after = decode_cursor(cursor) if cursor else None
    async with connection(request.app.state.settings) as conn:
        rows = await repository.list_projects(
            conn,
            limit,
            after,
            sorted(principal.managed_projects),
            principal.user_id,
            principal.dept if "dept_staff" in principal.roles else None,
        )
    items = [ProjectSummary.model_validate(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(last["created_at"], last["id"])
    return respond(request, ProjectList(items=items, next_cursor=next_cursor))


@router.get(
    "/{project_id}",
    response_model=SuccessResponse[ProjectDetail],
    operation_id="tft_get_project",
    responses={503: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def get_project(
    project_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[ProjectDetail]:
    require_project_list(principal)
    async with connection(request.app.state.settings) as conn:
        row = await repository.get_project(conn, project_id)
        # Out-of-scope projects look the same as missing ones (permissions.md 1.3 step 2).
        if row is None or not (
            sees_project(principal, row["id"], row["responsible_user_id"], row["sales_user_id"])
            or (
                "dept_staff" in principal.roles
                and in_review_department(
                    principal, await repository.review_departments(conn, project_id)
                )
            )
        ):
            raise ApplicationError(404, "TFT_PROJECT_NOT_FOUND", "The project was not found.")
        requirements = await repository.list_rfp_requirements(conn, project_id)
        integration = await repository.integration_state(conn, project_id)
    detail = ProjectDetail.model_validate(
        {
            **row,
            "rfp_requirements": [RfpRequirement.model_validate(r) for r in requirements],
            "integration_responsibility": integration_body(integration),
        }
    )
    return respond(request, detail)


def integration_body(state: tuple[dict[str, Any], list[dict[str, Any]]] | None) -> Any:
    """통합 책임 입력 → 응답 칸. 스키마가 없으면 None이다(repository.integration_state).

    Args:
        state: (사람 입력 칸, 최신 요구 판본 행) 또는 None.

    Returns:
        Any: IntegrationResponsibility 입력 dict 또는 None.
    """
    if state is None:
        return None
    holder, rows = state
    return {
        "holder": holder["integration_holder"],
        "holder_candidate_codes": holder["integration_holder_candidates"],
        "note": holder["integration_note"],
        "set_by": holder["integration_set_by"],
        "set_at": holder["integration_set_at"],
        **project_integration(
            [StoredRequirement.model_validate(r) for r in rows], holder["integration_holder"]
        ),
    }


def respond(request: Request, data: Any) -> SuccessResponse[Any]:
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))
