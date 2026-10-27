import base64
import binascii
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from psycopg import AsyncConnection

from backend.api.tft.projects import repository
from backend.api.tft.projects.access import require_project_list, sees_project
from backend.api.tft.projects.schemas import (
    AvailabilityCheck,
    CandidateRecommendation,
    CompetencyCheck,
    CompositionMember,
    CompositionVersion,
    ExperienceEvidence,
    FinalConfirmation,
    ProjectDetail,
    ProjectList,
    ProjectSummary,
    RequiredRole,
    ResponsibleSummary,
    RfpRequirement,
    RoleCompetency,
    RoleRequirementLink,
    StaffingRequest,
)
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
Row = dict[str, Any]


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
            principal.dept if principal.has("dept_staff") else None,
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
        if row is None:
            raise ApplicationError(404, "TFT_PROJECT_NOT_FOUND", "The project was not found.")
        full_access = sees_project(
            principal, row["id"], row["responsible_user_id"], row["sales_user_id"]
        )
        allowed_requests = []
        if not full_access and principal.has("dept_staff"):
            allowed_requests = [
                item
                for item in await repository.list_staffing_requests(conn, project_id)
                if item["organization_dept_code"] == principal.dept and item["status"] != "DRAFT"
            ]
            # An organization from another company does not grant project access.
            allowed = []
            for item in allowed_requests:
                organization = await repository.staffing_requests.get_organization(
                    conn, item["organization_id"]
                )
                if organization and organization["company_code"] == row["company_code"]:
                    allowed.append(item)
            allowed_requests = allowed
        if not full_access and not allowed_requests:
            raise ApplicationError(404, "TFT_PROJECT_NOT_FOUND", "The project was not found.")
        detail = await load_detail(conn, row)
        if not full_access:
            request_ids = {item["id"] for item in allowed_requests}
            role_ids = {item["required_role_id"] for item in allowed_requests}
            organization_ids = {item["organization_id"] for item in allowed_requests}
            detail.required_roles = [role for role in detail.required_roles if role.id in role_ids]
            requirement_ids = {
                link.requirement_id for role in detail.required_roles for link in role.requirements
            }
            detail.rfp_requirements = [
                item for item in detail.rfp_requirements if item.id in requirement_ids
            ]
            detail.staffing_requests = [
                item for item in detail.staffing_requests if item.id in request_ids
            ]
            detail.recommendations = [
                item
                for item in detail.recommendations
                if item.staffing_request_id in request_ids
                and item.organization_id in organization_ids
            ]
            detail.current_composition = None
            detail.final_confirmation = None
    return respond(request, detail)


async def load_detail(conn: AsyncConnection[Row], project: Row) -> ProjectDetail:
    """Assembles the six stage records of one project from their own tables."""
    project_id = project["id"]
    links = group(
        await repository.list_role_requirement_links(conn, project_id), "required_role_id"
    )
    competencies = group(
        await repository.list_role_competencies(conn, project_id), "required_role_id"
    )
    roles = [
        RequiredRole.model_validate(
            {
                **row,
                "requirements": [RoleRequirementLink.model_validate(r) for r in links[row["id"]]],
                "competencies": [RoleCompetency.model_validate(c) for c in competencies[row["id"]]],
            }
        )
        for row in await repository.list_required_roles(conn, project_id)
    ]
    evidence = group(
        await repository.list_experience_evidence(conn, project_id), "recommendation_id"
    )
    checks = group(await repository.list_competency_checks(conn, project_id), "recommendation_id")
    availability = group(
        await repository.list_availability_checks(conn, project_id), "recommendation_id"
    )
    recommendations = [
        CandidateRecommendation.model_validate(
            {
                **row,
                "experience_evidence": [
                    ExperienceEvidence.model_validate(e) for e in evidence[row["id"]]
                ],
                "competency_checks": [CompetencyCheck.model_validate(c) for c in checks[row["id"]]],
                "availability_checks": [
                    AvailabilityCheck.model_validate(a) for a in availability[row["id"]]
                ],
            }
        )
        for row in await repository.list_recommendations(conn, project_id)
    ]
    composition = None
    version = await repository.get_current_composition(conn, project_id)
    if version is not None:
        members = await repository.list_composition_members(conn, version["id"])
        composition = CompositionVersion.model_validate(
            {**version, "members": [CompositionMember.model_validate(m) for m in members]}
        )
    responsible = None
    if project["responsible_user_id"] is not None:
        change = await repository.get_latest_responsible_change(conn, project_id)
        # The column is only ever changed together with a history row; a mismatch is
        # shown as unassigned rather than inventing an assignment.
        if change is not None and change["new_user_id"] == project["responsible_user_id"]:
            responsible = ResponsibleSummary(
                user_id=change["new_user_id"],
                assigned_by_user_id=change["changed_by_user_id"],
                assigned_at=change["occurred_at"],
                reason=change["reason"],
            )
    confirmation = await repository.get_final_confirmation(conn, project_id)
    return ProjectDetail.model_validate(
        {
            **project,
            "responsible": responsible,
            "rfp_requirements": [
                RfpRequirement.model_validate(r)
                for r in await repository.list_rfp_requirements(conn, project_id)
            ],
            "required_roles": roles,
            "staffing_requests": [
                StaffingRequest.model_validate(r)
                for r in await repository.list_staffing_requests(conn, project_id)
            ],
            "recommendations": recommendations,
            "current_composition": composition,
            "final_confirmation": (
                FinalConfirmation.model_validate(confirmation) if confirmation else None
            ),
        }
    )


def group(rows: list[Row], key: str) -> defaultdict[UUID, list[Row]]:
    grouped: defaultdict[UUID, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return grouped


def respond(request: Request, data: Any) -> SuccessResponse[Any]:
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))
