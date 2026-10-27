"""Ticket workflow actions (contracts/platform/permissions.md 2.1, 4.1).

Each action locks the ticket row, then checks in the order of permissions.md 1.3: scope
(404), role (403), state (409), version (409). Sending an answer or an information request
moves the state through the mail sending path (KAN-74), not through this router.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.cs.tickets import repository as tickets
from backend.api.cs.tickets.routes import PARTIAL, check_visible, not_found, respond
from backend.api.cs.workflow import repository
from backend.api.cs.workflow.schemas import (
    Action,
    DeptResult,
    DeptResultRequest,
    OwnerRequest,
    ReasonRequest,
    ReviewRequest,
    Transfer,
    TransferRequest,
    Workflow,
)
from backend.shared.access import Principal, Role, current_principal
from backend.shared.database import connection
from backend.shared.departments import CS_DEPARTMENT
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind_path_ids
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    prefix="/api/v1/cs/tickets/{ticket_id}/workflow",
    tags=["workflow"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse, "description": "No valid user context"},
        403: {"model": ErrorResponse, "description": "The role may not take this action"},
        404: {"model": ErrorResponse, "description": "Not found or outside the user's scope"},
        409: {"model": ErrorResponse, "description": "Invalid transition or stale version"},
        503: {"model": ErrorResponse},
    },
)

Row = dict[str, Any]


def invalid_transition() -> ApplicationError:
    return ApplicationError(
        409, "CS_INVALID_TRANSITION", "The ticket state does not allow this action."
    )


def unreviewed_result() -> ApplicationError:
    return ApplicationError(
        409, "CS_DEPT_RESULT_UNREVIEWED", "A department result is waiting for review."
    )


def json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def require_role(principal: Principal, role: Role) -> None:
    if not principal.has(role):
        raise HTTPException(403)


async def begin(
    conn: Any, principal: Principal, ticket_id: UUID
) -> tuple[Row, list[Row], list[Row]]:
    """Locks the ticket and returns its workflow, transfers and department results."""
    await check_visible(conn, principal, ticket_id)
    if await tickets.lock_ticket(conn, ticket_id) is None:
        raise not_found()
    workflow = await repository.lock_workflow(conn, ticket_id) or not_started(ticket_id)
    transfers = await repository.list_transfers(conn, ticket_id)
    results = await repository.list_results(conn, ticket_id)
    return workflow, transfers, results


def not_started(ticket_id: UUID) -> Row:
    return {
        "ticket_id": ticket_id,
        "state": "RECEIVED",
        "version": 0,
        "owner_user_id": None,
        "customer_sent_at": None,
        "closed_at": None,
        "closed_by": None,
    }


def check(workflow: Row, payload: Action, *states: str) -> None:
    if workflow["state"] not in states:
        raise invalid_transition()
    if workflow["version"] != payload.version:
        raise ApplicationError(409, "CS_WORKFLOW_VERSION_CONFLICT", "The workflow has changed.")


def open_transfer(transfers: list[Row]) -> Row:
    # TRANSFERRED always has exactly one open transfer (partial unique index).
    return next(t for t in transfers if t["status"] == "OPEN")


async def save(
    conn: Any, workflow: Row, actor: UUID, action: str, values: Row, **details: Any
) -> Row:
    """Writes the workflow change and its audit event. Returns the new workflow row."""
    changes: Row = {
        name: {"from": json_value(workflow[name]), "to": json_value(value)}
        for name, value in values.items()
        if workflow[name] != value
    }
    changes["action"] = {"to": action}
    for name, value in details.items():
        changes[name] = {"to": json_value(value)}
    ticket_id = workflow["ticket_id"]
    if workflow["version"] == 0:
        await repository.insert_workflow(conn, ticket_id, {"state": workflow["state"], **values})
    else:
        await repository.update_workflow(conn, ticket_id, workflow["version"], values)
    version = workflow["version"] + 1
    await repository.insert_audit(
        conn, uuid4(), "ticket_workflow", ticket_id, "transition", version, actor, changes
    )
    return {**workflow, **values, "version": version}


async def view(conn: Any, workflow: Row) -> Workflow:
    ticket_id = workflow["ticket_id"]
    return Workflow(
        **workflow,
        transfers=[
            Transfer.model_validate(t) for t in await repository.list_transfers(conn, ticket_id)
        ],
        dept_results=[
            DeptResult.model_validate(r) for r in await repository.list_results(conn, ticket_id)
        ],
    )


@router.get(
    "",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_get_ticket_workflow",
    openapi_extra=PARTIAL,
)
async def get_workflow(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[Workflow]:
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, ticket_id)
        if await tickets.get_ticket(conn, ticket_id) is None:
            raise not_found()
        workflow = await repository.get_workflow(conn, ticket_id) or not_started(ticket_id)
        data = await view(conn, workflow)
    return respond(request, data)


@router.post(
    "/start-review",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_start_ticket_review",
    openapi_extra=PARTIAL,
)
async def start_review(
    ticket_id: UUID,
    payload: Action,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """RECEIVED → IN_REVIEW by a C/S agent, who becomes the owner if there is none."""
    async with connection(request.app.state.settings) as conn:
        workflow, _, _ = await begin(conn, principal, ticket_id)
        require_role(principal, "cs_agent")
        check(workflow, payload, "RECEIVED")
        values: Row = {"state": "IN_REVIEW"}
        if workflow["owner_user_id"] is None:
            values["owner_user_id"] = principal.user_id
        workflow = await save(conn, workflow, principal.user_id, "start_review", values)
        data = await view(conn, workflow)
    return respond(request, data)


@router.post(
    "/transfer",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_transfer_ticket",
    openapi_extra=PARTIAL,
)
async def transfer(
    ticket_id: UUID,
    payload: TransferRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """IN_REVIEW → TRANSFERRED. The C/S agent confirms the department and the reason."""
    async with connection(request.app.state.settings) as conn:
        workflow, _, results = await begin(conn, principal, ticket_id)
        require_role(principal, "cs_agent")
        check(workflow, payload, "IN_REVIEW")
        if any(r["review_status"] == "SUBMITTED" for r in results):
            raise unreviewed_result()
        await repository.insert_transfer(
            conn,
            {
                "id": uuid4(),
                "ticket_id": ticket_id,
                "dept_code": payload.dept_code,
                "reason": payload.reason,
                "transferred_by": principal.user_id,
            },
        )
        workflow = await save(
            conn,
            workflow,
            principal.user_id,
            "transfer",
            {"state": "TRANSFERRED"},
            dept_code=payload.dept_code,
            reason=payload.reason,
        )
        data = await view(conn, workflow)
    return respond(request, data)


@router.post(
    "/recall",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_recall_ticket_transfer",
    openapi_extra=PARTIAL,
)
async def recall(
    ticket_id: UUID,
    payload: ReasonRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """TRANSFERRED → IN_REVIEW: the C/S agent takes back a transfer, with a reason."""
    async with connection(request.app.state.settings) as conn:
        workflow, transfers, _ = await begin(conn, principal, ticket_id)
        require_role(principal, "cs_agent")
        check(workflow, payload, "TRANSFERRED")
        current = open_transfer(transfers)
        await repository.end_transfer(
            conn, current["id"], "RECALLED", principal.user_id, payload.reason
        )
        workflow = await save(
            conn,
            workflow,
            principal.user_id,
            "recall",
            {"state": "IN_REVIEW"},
            dept_code=current["dept_code"],
            reason=payload.reason,
        )
        data = await view(conn, workflow)
    return respond(request, data)


def require_transfer_department(principal: Principal, transfers: list[Row]) -> Row:
    current = open_transfer(transfers)
    if principal.dept != current["dept_code"]:
        # The user can read the ticket through an earlier transfer, but it is not theirs now.
        raise HTTPException(403)
    return current


@router.post(
    "/return",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_return_ticket_transfer",
    openapi_extra=PARTIAL,
)
async def return_transfer(
    ticket_id: UUID,
    payload: ReasonRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """TRANSFERRED → IN_REVIEW: the department sends back a ticket that is not its work."""
    async with connection(request.app.state.settings) as conn:
        workflow, transfers, _ = await begin(conn, principal, ticket_id)
        require_role(principal, "dept_staff")
        check(workflow, payload, "TRANSFERRED")
        current = require_transfer_department(principal, transfers)
        await repository.end_transfer(
            conn, current["id"], "RETURNED_BY_DEPT", principal.user_id, payload.reason
        )
        workflow = await save(
            conn,
            workflow,
            principal.user_id,
            "return",
            {"state": "IN_REVIEW"},
            dept_code=current["dept_code"],
            reason=payload.reason,
        )
        data = await view(conn, workflow)
    return respond(request, data)


@router.post(
    "/dept-result",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_submit_dept_result",
    openapi_extra=PARTIAL,
)
async def submit_dept_result(
    ticket_id: UUID,
    payload: DeptResultRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """TRANSFERRED → IN_REVIEW: the department reports its result. It waits for review."""
    async with connection(request.app.state.settings) as conn:
        workflow, transfers, _ = await begin(conn, principal, ticket_id)
        require_role(principal, "dept_staff")
        check(workflow, payload, "TRANSFERRED")
        current = require_transfer_department(principal, transfers)
        result_id = uuid4()
        await repository.insert_result(
            conn,
            {
                "id": result_id,
                "ticket_id": ticket_id,
                "transfer_id": current["id"],
                "dept_code": current["dept_code"],
                "result": payload.result,
                "reply_content": payload.reply_content,
                "submitted_by": principal.user_id,
            },
        )
        await repository.end_transfer(
            conn, current["id"], "RESULT_SUBMITTED", principal.user_id, None
        )
        # The result text stays in its row; the audit names the result and its version.
        await repository.insert_audit(
            conn,
            uuid4(),
            "dept_result",
            result_id,
            "create",
            1,
            principal.user_id,
            {"dept_code": {"to": current["dept_code"]}, "review_status": {"to": "SUBMITTED"}},
        )
        workflow = await save(
            conn,
            workflow,
            principal.user_id,
            "dept_result",
            {"state": "IN_REVIEW"},
            dept_result_id=result_id,
        )
        data = await view(conn, workflow)
    return respond(request, data)


@router.post(
    "/dept-results/{result_id}/review",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_review_dept_result",
    openapi_extra=PARTIAL,
)
async def review_dept_result(
    ticket_id: UUID,
    result_id: UUID,
    payload: ReviewRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """The C/S agent accepts a department result, or asks the same department to rework it.

    Rework opens a new transfer round to that department (IN_REVIEW → TRANSFERRED).
    """
    async with connection(request.app.state.settings) as conn:
        workflow, _, results = await begin(conn, principal, ticket_id)
        require_role(principal, "cs_agent")
        check(workflow, payload, "IN_REVIEW")
        result = next((r for r in results if r["id"] == result_id), None)
        if result is None:
            raise ApplicationError(
                404, "CS_DEPT_RESULT_NOT_FOUND", "The department result was not found."
            )
        if result["review_status"] != "SUBMITTED":
            raise ApplicationError(
                409, "CS_DEPT_RESULT_ALREADY_REVIEWED", "The result was already reviewed."
            )
        if result["version"] != payload.result_version:
            raise ApplicationError(
                409, "CS_DEPT_RESULT_VERSION_CONFLICT", "The result has changed."
            )
        status = "ACCEPTED" if payload.decision == "ACCEPT" else "REWORK_REQUESTED"
        await repository.review_result(
            conn, result_id, result["version"], status, principal.user_id, payload.note
        )
        await repository.insert_audit(
            conn,
            uuid4(),
            "dept_result",
            result_id,
            "review",
            result["version"] + 1,
            principal.user_id,
            {
                "review_status": {"from": "SUBMITTED", "to": status},
                "reviewed_version": {"to": result["version"]},
            },
        )
        values: Row = {}
        if status == "REWORK_REQUESTED":
            await repository.insert_transfer(
                conn,
                {
                    "id": uuid4(),
                    "ticket_id": ticket_id,
                    "dept_code": result["dept_code"],
                    "reason": payload.note,
                    "transferred_by": principal.user_id,
                },
            )
            values["state"] = "TRANSFERRED"
        workflow = await save(
            conn,
            workflow,
            principal.user_id,
            "review_dept_result",
            values,
            dept_result_id=result_id,
            decision=payload.decision,
        )
        data = await view(conn, workflow)
    return respond(request, data)


@router.post(
    "/close",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_close_ticket",
    openapi_extra=PARTIAL,
)
async def close(
    ticket_id: UUID,
    payload: Action,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """Closes from ANSWERED, or from IN_REVIEW with an accepted department result or a sent
    customer message. An unreviewed department result blocks closing."""
    async with connection(request.app.state.settings) as conn:
        workflow, _, results = await begin(conn, principal, ticket_id)
        require_role(principal, "cs_agent")
        check(workflow, payload, "IN_REVIEW", "ANSWERED")
        if any(r["review_status"] == "SUBMITTED" for r in results):
            raise unreviewed_result()
        accepted = any(r["review_status"] == "ACCEPTED" for r in results)
        if workflow["state"] == "IN_REVIEW" and not accepted and not workflow["customer_sent_at"]:
            raise ApplicationError(
                409,
                "CS_CLOSE_CONDITION_UNMET",
                "Closing needs an accepted department result or a sent customer message.",
            )
        workflow = await save(
            conn,
            workflow,
            principal.user_id,
            "close",
            {"state": "CLOSED", "closed_at": datetime.now(UTC), "closed_by": principal.user_id},
        )
        data = await view(conn, workflow)
    return respond(request, data)


@router.put(
    "/owner",
    response_model=SuccessResponse[Workflow],
    operation_id="cs_reassign_ticket_owner",
    openapi_extra=PARTIAL,
)
async def reassign_owner(
    ticket_id: UUID,
    payload: OwnerRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Workflow]:
    """Only a manager of the C/S department reassigns the owner. The state does not change.

    The C/S service cannot read the identity schema, so the new owner's role is not checked
    here; the screen offers registered C/S agents only.
    """
    async with connection(request.app.state.settings) as conn:
        workflow, _, _ = await begin(conn, principal, ticket_id)
        if not (principal.has("manager") and CS_DEPARTMENT in principal.managed_departments):
            raise HTTPException(403)
        if workflow["version"] != payload.version:
            raise ApplicationError(409, "CS_WORKFLOW_VERSION_CONFLICT", "The workflow has changed.")
        if workflow["owner_user_id"] != payload.owner_user_id:
            workflow = await save(
                conn,
                workflow,
                principal.user_id,
                "reassign_owner",
                {"owner_user_id": payload.owner_user_id},
            )
        data = await view(conn, workflow)
    return respond(request, data)
