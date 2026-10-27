"""Ticket workflow actions (contracts/platform/permissions.md 2.1, 4.1).

Each action locks the ticket row, then checks in the order of permissions.md 1.3: scope
(404), role (403), state (409), version (409). Sending an answer or an information request
moves the state through the mail sending path (KAN-74), not through this router.
"""

from datetime import UTC, datetime
from typing import Any, get_args
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.cs.tickets import repository as tickets
from backend.api.cs.tickets.access import sees_all_tickets, transfer_departments
from backend.api.cs.tickets.routes import PARTIAL, check_visible, not_found, respond
from backend.api.cs.tickets.schemas import ResolutionCase, WorkflowState
from backend.api.cs.workflow import repository
from backend.api.cs.workflow.schemas import (
    Action,
    DeptResult,
    DeptResultRequest,
    OwnerRequest,
    ReasonRequest,
    ResolutionCaseCreate,
    ReviewRequest,
    Transfer,
    TransferRequest,
    Workflow,
)
from backend.api.cs.workflow.service import (
    check,
    json_value,
    not_started,
    save,
    unreviewed_result,
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
ALL_STATES = get_args(WorkflowState)


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


def open_transfer(transfers: list[Row]) -> Row:
    # TRANSFERRED always has exactly one open transfer (partial unique index).
    return next(t for t in transfers if t["status"] == "OPEN")


async def view(conn: Any, workflow: Row, principal: Principal) -> Workflow:
    """The workflow as this user may see it. Department users see only the transfers and
    results of their own departments, not what another department reported."""
    ticket_id = workflow["ticket_id"]
    transfers = await repository.list_transfers(conn, ticket_id)
    results = await repository.list_results(conn, ticket_id)
    if not sees_all_tickets(principal):
        departments = transfer_departments(principal)
        transfers = [t for t in transfers if t["dept_code"] in departments]
        results = [r for r in results if r["dept_code"] in departments]
    return Workflow(
        **workflow,
        transfers=[Transfer.model_validate(t) for t in transfers],
        dept_results=[DeptResult.model_validate(r) for r in results],
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
        data = await view(conn, workflow, principal)
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
        check(workflow, payload.version, "RECEIVED")
        values: Row = {"state": "IN_REVIEW"}
        if workflow["owner_user_id"] is None:
            values["owner_user_id"] = principal.user_id
        workflow = await save(conn, workflow, principal.user_id, "start_review", values)
        data = await view(conn, workflow, principal)
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
        check(workflow, payload.version, "IN_REVIEW")
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
        data = await view(conn, workflow, principal)
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
        check(workflow, payload.version, "TRANSFERRED")
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
        data = await view(conn, workflow, principal)
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
        check(workflow, payload.version, "TRANSFERRED")
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
        data = await view(conn, workflow, principal)
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
        check(workflow, payload.version, "TRANSFERRED")
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
        data = await view(conn, workflow, principal)
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
        check(workflow, payload.version, "IN_REVIEW")
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
        data = await view(conn, workflow, principal)
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
        check(workflow, payload.version, "IN_REVIEW", "ANSWERED")
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
        data = await view(conn, workflow, principal)
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
        check(workflow, payload.version, *ALL_STATES)
        if workflow["owner_user_id"] != payload.owner_user_id:
            workflow = await save(
                conn,
                workflow,
                principal.user_id,
                "reassign_owner",
                {"owner_user_id": payload.owner_user_id},
            )
        data = await view(conn, workflow, principal)
    return respond(request, data)


case_router = APIRouter(
    prefix="/api/v1/cs/tickets/{ticket_id}",
    tags=["workflow"],
    dependencies=[Depends(bind_path_ids)],
    responses=router.responses,
)


@case_router.post(
    "/resolution-case",
    status_code=201,
    response_model=SuccessResponse[ResolutionCase],
    operation_id="cs_register_resolution_case",
    responses={422: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def register_resolution_case(
    ticket_id: UUID,
    payload: ResolutionCaseCreate,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[ResolutionCase]:
    """종결된 문의에 해결 사례를 한 건 등록한다(선택).

    연결할 부서 결과는 이 문의에서 채택된 것만 쓸 수 있다. 사례 등록은 업무 상태를 바꾸지
    않으며, 등록한 종결 판본을 감사에 남긴다.

    Args:
        ticket_id: 종결된 문의.
        payload: 사람이 확인한 사례 내용과 종결 화면의 업무 판본.
        request: DB 연결을 제공하는 요청.
        principal: 검증된 사용자. cs_agent만 등록한다.

    Returns:
        SuccessResponse[ResolutionCase]: 등록된 사례. 바로 문의 상세에 근거로 보인다.
    """
    async with connection(request.app.state.settings) as conn:
        workflow, _, results = await begin(conn, principal, ticket_id)
        require_role(principal, "cs_agent")
        check(workflow, payload.version, "CLOSED")
        if await repository.has_case(conn, ticket_id):
            raise ApplicationError(
                409, "CS_RESOLUTION_CASE_EXISTS", "The ticket already has a resolution case."
            )
        if payload.dept_result_id is not None and not any(
            r["id"] == payload.dept_result_id and r["review_status"] == "ACCEPTED" for r in results
        ):
            raise ApplicationError(
                422,
                "CS_DEPT_RESULT_NOT_ACCEPTED",
                "Only an accepted result of this ticket can back a case.",
            )
        ticket = await tickets.get_ticket(conn, ticket_id)
        if ticket is None:
            raise not_found()
        # The database clock, which the ticket detail compares known_at with.
        now = await repository.database_now(conn)
        case = {
            "id": uuid4(),
            "ticket_id": ticket_id,
            "source": "manual",
            "source_instance": "cs_api",
            "external_id": None,
            "data_origin": ticket["data_origin"],
            "derivation_kind": "SYSTEM",
            "resolved_at": workflow["closed_at"],
            "known_at": now,
            "product_code": ticket["product_code"],
            "registered_by": principal.user_id,
            **payload.model_dump(exclude={"version"}),
        }
        await repository.insert_case(conn, case)
        await repository.insert_audit(
            conn,
            uuid4(),
            "resolution_case",
            case["id"],
            "create",
            1,
            principal.user_id,
            {
                "ticket_id": {"to": str(ticket_id)},
                "workflow_version": {"to": workflow["version"]},
                "dept_result_id": {"to": json_value(payload.dept_result_id)},
                "fault_id": {"to": payload.fault_id},
            },
        )
    return respond(request, ResolutionCase.model_validate(case))
