"""Project-scoped, read-only rule report; never performs approval or team selection."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from backend.api.tft.projects import repository
from backend.api.tft.projects.access import require_project_list, sees_project
from backend.api.tft.projects.routes import load_detail, respond
from backend.api.tft.verification.rules import TeamAssessment, assess
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(prefix="/api/v1/tft/projects", tags=["team-verification"])


@router.get(
    "/{project_id}/verification",
    response_model=SuccessResponse[TeamAssessment],
    operation_id="tft_verify_team",
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 503)},
    openapi_extra={"x-implementation-status": "partial"},
)
async def verification(
    project_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[TeamAssessment]:
    require_project_list(principal)
    async with connection(request.app.state.settings) as conn:
        row = await repository.get_project(conn, project_id)
        if row is None or not sees_project(
            principal, project_id, row["responsible_user_id"], row["sales_user_id"]
        ):
            raise ApplicationError(404, "TFT_PROJECT_NOT_FOUND", "프로젝트를 찾을 수 없습니다.")
        detail = await load_detail(conn, row)
    return respond(request, assess(detail))
