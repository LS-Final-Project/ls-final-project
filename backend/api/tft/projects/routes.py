from fastapi import APIRouter, HTTPException

from backend.api.tft.projects.schemas import ProjectList
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(prefix="/api/v1/tft/projects", tags=["projects"])


@router.get(
    "",
    response_model=SuccessResponse[ProjectList],
    operation_id="tft_list_projects",
    responses={501: {"model": ErrorResponse}},
    openapi_extra={"x-implementation-status": "stub"},
)
async def list_projects() -> SuccessResponse[ProjectList]:
    """Contract stub. Browser mock examples are synthetic; real implementation is pending."""
    raise HTTPException(501)
