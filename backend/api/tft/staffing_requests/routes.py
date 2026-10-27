"""Department staffing requests: review, edit and issue (KAN-122).

Check order follows contracts/platform/permissions.md 1.3: principal (401, by
`current_principal`), project visibility (404), action (403), state (409),
version (428/412), required input (422).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.encoders import jsonable_encoder

from backend.api.tft.authz import ensure_can_edit, ensure_can_issue
from backend.api.tft.projects.access import require_project_list, sees_project
from backend.api.tft.staffing_requests import repository
from backend.api.tft.staffing_requests.schemas import (
    StaffingRequest,
    StaffingRequestList,
    StaffingRequestUpdate,
)
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

router = APIRouter(
    prefix="/api/v1/tft",
    tags=["staffing-requests"],
    responses={
        401: {"model": ErrorResponse, "description": "No valid user context"},
        403: {"model": ErrorResponse, "description": "The role or user may not do this"},
        404: {
            "model": ErrorResponse,
            "description": "Not found or outside the user's scope. Closed outside local/test "
            "until the platform relays the user (KAN-78)",
        },
    },
)

PARTIAL = {"x-implementation-status": "partial"}
WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse},
    412: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    428: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
IF_MATCH = Header(
    default=None,
    alias="If-Match",
    description='Current request version, quoted like an ETag: "3". Required for writes.',
)
# Accepted per docs/api-contract.md so the frontend can send it on every state-making POST.
# Duplicate suppression by key is a later ticket; today If-Match makes a replay a 412.
IDEMPOTENCY_KEY = Header(
    default=None,
    alias="Idempotency-Key",
    max_length=128,
    description="Received and ignored for now; replays are refused by If-Match (412).",
)
Row = dict[str, Any]


def not_found() -> ApplicationError:
    return ApplicationError(
        404, "TFT_STAFFING_REQUEST_NOT_FOUND", "The staffing request was not found."
    )


def respond(request: Request, response: Response, row: Row) -> SuccessResponse[StaffingRequest]:
    data = StaffingRequest.model_validate(row)
    response.headers["ETag"] = f'"{data.version}"'
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))


def check_version(if_match: str | None, current: int) -> None:
    if if_match is None:
        raise ApplicationError(
            428, "VERSION_REQUIRED", "If-Match with the current version is required."
        )
    value = if_match.strip()
    if value.startswith("W/"):
        value = value[2:]
    value = value.strip('"')
    # isdigit() alone accepts non-ASCII digits that int() then rejects with a 500.
    if not (value.isascii() and value.isdigit()) or int(value) != current:
        raise ApplicationError(412, "VERSION_CONFLICT", "The staffing request has changed.")


def require_draft(row: Row) -> None:
    if row["status"] != "DRAFT":
        raise ApplicationError(
            409, "INVALID_TRANSITION", "Only a DRAFT staffing request can be changed."
        )


async def visible_project(conn: Any, principal: Principal, project_id: UUID) -> Row | None:
    """The project when it exists and the principal may see it (permissions.md 1.3 step 2);
    out-of-scope projects look the same as missing ones."""
    project = await repository.get_project_scope(conn, project_id)
    if project is None or not sees_project(
        principal, project["id"], project["responsible_user_id"], project["sales_user_id"]
    ):
        return None
    return project


async def visible_request(
    conn: Any, principal: Principal, request_id: UUID, *, lock: bool
) -> tuple[Row, Row]:
    """The request row and its project, or 404 when either is missing or out of scope."""
    load = repository.lock_request if lock else repository.get_request
    row = await load(conn, request_id)
    if row is None:
        raise not_found()
    project = await visible_project(conn, principal, row["project_id"])
    if project is None:
        raise not_found()
    return row, project


@router.get(
    "/projects/{project_id}/staffing-requests",
    response_model=SuccessResponse[StaffingRequestList],
    operation_id="tft_list_staffing_requests",
    responses={503: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def list_staffing_requests(
    project_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[StaffingRequestList]:
    require_project_list(principal)
    async with connection(request.app.state.settings) as conn:
        if await visible_project(conn, principal, project_id) is None:
            raise ApplicationError(404, "TFT_PROJECT_NOT_FOUND", "The project was not found.")
        rows = await repository.list_for_project(conn, project_id)
    items = [StaffingRequest.model_validate(row) for row in rows]
    return SuccessResponse(
        data=StaffingRequestList(items=items),
        meta=ResponseMeta(request_id=request.state.request_id),
    )


@router.get(
    "/staffing-requests/{request_id}",
    response_model=SuccessResponse[StaffingRequest],
    operation_id="tft_get_staffing_request",
    responses={
        200: {"headers": {"ETag": {"schema": {"type": "string"}, "description": "Version"}}},
        503: {"model": ErrorResponse},
    },
    openapi_extra=PARTIAL,
)
async def get_staffing_request(
    request_id: UUID,
    request: Request,
    response: Response,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[StaffingRequest]:
    require_project_list(principal)
    async with connection(request.app.state.settings) as conn:
        row, _ = await visible_request(conn, principal, request_id, lock=False)
    return respond(request, response, row)


@router.patch(
    "/staffing-requests/{request_id}",
    response_model=SuccessResponse[StaffingRequest],
    operation_id="tft_update_staffing_request",
    responses=WRITE_RESPONSES,
    openapi_extra=PARTIAL,
)
async def update_staffing_request(
    request_id: UUID,
    payload: StaffingRequestUpdate,
    request: Request,
    response: Response,
    if_match: str | None = IF_MATCH,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[StaffingRequest]:
    require_project_list(principal)
    async with connection(request.app.state.settings) as conn:
        row, project = await visible_request(conn, principal, request_id, lock=True)
        ensure_can_edit(principal, project)
        require_draft(row)
        check_version(if_match, row["version"])
        values = payload.changed_values()
        if "organization_id" in values and values["organization_id"] != row["organization_id"]:
            organization = await repository.get_organization(conn, values["organization_id"])
            # The requested department must be an organization of the project's company.
            if organization is None or organization["company_code"] != project["company_code"]:
                raise ApplicationError(
                    422, "VALIDATION_ERROR", "organization_id is unknown or outside the company."
                )
            row = {
                **row,
                "organization_name": organization["name"],
                "organization_dept_code": organization["dept_code"],
            }
        merged = {**row, **values}
        if merged["due_undecided"] and merged["due_at"] is not None:
            raise ApplicationError(
                422, "VALIDATION_ERROR", "due_at and due_undecided cannot both be set."
            )
        changes = {
            name: {"from": row[name], "to": value}
            for name, value in values.items()
            if row[name] != value
        }
        if changes:
            new_values = {name: change["to"] for name, change in changes.items()}
            updated_at = await repository.update_request(
                conn, request_id, row["version"], new_values
            )
            row = {**row, **new_values, "version": row["version"] + 1, "updated_at": updated_at}
            await repository.insert_revision(
                conn,
                uuid4(),
                request_id,
                row["version"],
                "EDIT",
                principal.user_id,
                jsonable_encoder(changes),
            )
    return respond(request, response, row)


@router.post(
    "/staffing-requests/{request_id}/issue",
    response_model=SuccessResponse[StaffingRequest],
    operation_id="tft_issue_staffing_request",
    responses=WRITE_RESPONSES,
    openapi_extra=PARTIAL,
)
async def issue_staffing_request(
    request_id: UUID,
    request: Request,
    response: Response,
    if_match: str | None = IF_MATCH,
    idempotency_key: str | None = IDEMPOTENCY_KEY,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[StaffingRequest]:
    require_project_list(principal)
    async with connection(request.app.state.settings) as conn:
        row, project = await visible_request(conn, principal, request_id, lock=True)
        ensure_can_issue(principal, project)
        require_draft(row)
        check_version(if_match, row["version"])
        if row["due_at"] is None and not row["due_undecided"]:
            raise ApplicationError(
                422, "EVIDENCE_REQUIRED", "A due date or the undecided mark is required to issue."
            )
        now = datetime.now(UTC)
        values = {"status": "ISSUED", "issued_by_user_id": principal.user_id, "issued_at": now}
        changes = {name: {"from": row[name], "to": value} for name, value in values.items()}
        updated_at = await repository.update_request(conn, request_id, row["version"], values)
        await repository.mark_role_issued(conn, row["required_role_id"], principal.user_id, now)
        row = {**row, **values, "version": row["version"] + 1, "updated_at": updated_at}
        await repository.insert_revision(
            conn,
            uuid4(),
            request_id,
            row["version"],
            "ISSUE",
            principal.user_id,
            jsonable_encoder(changes),
        )
    return respond(request, response, row)
