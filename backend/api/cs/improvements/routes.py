"""Improvement request API (KAN-112): write, version, evidence, owner and next owners.

The request is independent of its tickets. A ticket may be closed, reopened or still open
while it backs a request, and nothing here changes a ticket.
"""

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.cs.improvements import repository
from backend.api.cs.improvements.next_owner import next_owners
from backend.api.cs.improvements.schemas import (
    CONTENT_FIELDS,
    DeptReply,
    Evidence,
    EvidenceAdd,
    EvidenceInput,
    EvidenceRemove,
    ImprovementRequest,
    OwnerUpdate,
    RequestAction,
    RequestCreate,
    RequestDepartment,
    RequestDetail,
    RequestList,
    RequestSummary,
    RequestUpdate,
    RequestVersion,
    RequestVersionList,
)
from backend.api.cs.tickets.access import sees_all_tickets, transfer_departments
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.engine.cs.rules import RuleInputError, product_family
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.departments import CS_DEPARTMENT
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    prefix="/api/v1/cs/improvement-requests",
    tags=["improvement-requests"],
    responses={
        401: {"model": ErrorResponse, "description": "No valid user context"},
        403: {"model": ErrorResponse, "description": "The role may not take this action"},
        404: {"model": ErrorResponse, "description": "Not found or outside the user's scope"},
        409: {"model": ErrorResponse, "description": "Stale version or closed request"},
        503: {"model": ErrorResponse},
    },
)

Row = dict[str, Any]


def not_found() -> ApplicationError:
    return ApplicationError(404, "CS_REQUEST_NOT_FOUND", "The improvement request was not found.")


def invalid_evidence(message: str) -> ApplicationError:
    return ApplicationError(422, "CS_EVIDENCE_INVALID", message)


def scope(principal: Principal) -> tuple[list[str] | None, bool]:
    """(departments, include proposed). None means every request (permissions.md 4.1)."""
    if sees_all_tickets(principal):
        return None, False
    return sorted(transfer_departments(principal)), principal.has("manager")


def require_list(principal: Principal) -> None:
    if not principal.has("cs_agent", "manager", "dept_staff"):
        raise HTTPException(403)


async def check_visible(conn: Any, principal: Principal, request_id: UUID) -> None:
    departments, proposed = scope(principal)
    if departments is None:
        return
    if not departments or not await repository.visible_to(conn, request_id, departments, proposed):
        raise not_found()


def require_writer(principal: Principal) -> None:
    if not principal.has("cs_agent"):
        raise HTTPException(403)


def check_version(request: Row, version: int) -> None:
    if request["version"] != version:
        raise ApplicationError(409, "CS_REQUEST_VERSION_CONFLICT", "The request has changed.")


def check_open(request: Row) -> None:
    # A completed request reopens only through recurrence (KAN-116/117), never by editing.
    if request["status"] == "COMPLETED":
        raise ApplicationError(409, "CS_REQUEST_COMPLETED", "The request is completed.")


def plain(value: Any) -> Any:
    if isinstance(value, UUID | date):
        return str(value)
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def snapshot(request: Row, evidence: list[Row]) -> Row:
    """The versioned content: what a person wrote, the owner, and the active evidence."""
    content = {name: plain(request[name]) for name in CONTENT_FIELDS}
    content["cs_owner"] = plain(request["cs_owner"])
    content["evidence"] = sorted(
        (
            {"ticket_id": str(e["ticket_id"]), "case_id": plain(e["case_id"])}
            for e in evidence
            if e["removed_at"] is None
        ),
        key=lambda e: e["ticket_id"],
    )
    return content


async def commit_version(conn: Any, request: Row, actor: UUID, action: str, changes: Row) -> Row:
    """Stores the new version's content and its audit event, and returns the fresh row."""
    fresh = await repository.get_request(conn, request["id"])
    assert fresh is not None
    evidence = await repository.list_evidence(conn, request["id"])
    await repository.insert_version(
        conn, request["id"], fresh["version"], snapshot(fresh, evidence), actor
    )
    await repository.insert_audit(
        conn, uuid4(), request["id"], action, fresh["version"], actor, changes
    )
    return fresh


async def validate_evidence(conn: Any, request: Row, item: EvidenceInput) -> None:
    """The ticket must be about the request's product; a fault needs that fault's case."""
    ticket, case = await repository.ticket_and_case(conn, item.ticket_id, item.case_id)
    if ticket is None:
        raise invalid_evidence(f"Ticket {item.ticket_id} was not found.")
    if ticket["product_code"] != request["product_code"]:
        raise invalid_evidence("The ticket is about another product.")
    if request["request_type"] == "DOCUMENT":
        if item.case_id is not None:
            raise invalid_evidence("A document request links tickets, not resolution cases.")
        return
    if item.case_id is None:
        raise invalid_evidence("A fault request needs the ticket's resolution case.")
    if case is None or case["ticket_id"] != item.ticket_id:
        raise invalid_evidence("The case is unknown, not yet known or of another ticket.")
    if case["fault_id"] != request["fault_id"]:
        raise invalid_evidence("The case records another fault.")


async def detail(conn: Any, request: Row) -> RequestDetail:
    departments = await repository.current_departments(conn, request)
    return RequestDetail(
        request=model(request),
        evidence=[
            Evidence.model_validate(e) for e in await repository.list_evidence(conn, request["id"])
        ],
        departments=[RequestDepartment.model_validate(d) for d in departments],
        replies=[
            DeptReply.model_validate(r) for r in await repository.list_replies(conn, request["id"])
        ],
        actions=[
            RequestAction.model_validate(a)
            for a in await repository.list_actions(conn, request["id"])
        ],
        next_owners=next_owners(request, departments),
    )


def model(request: Row) -> ImprovementRequest:
    return ImprovementRequest.model_validate(
        {**request, "receiver_unconfirmed": not request["proposed_depts"]}
    )


@router.get(
    "",
    response_model=SuccessResponse[RequestList],
    operation_id="cs_list_improvement_requests",
    openapi_extra=PARTIAL,
)
async def list_requests(
    request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[RequestList]:
    require_list(principal)
    departments, proposed = scope(principal)
    if departments is not None and not departments:
        return respond(request, RequestList(items=[]))
    async with connection(request.app.state.settings) as conn:
        rows = await repository.list_requests(conn, departments, proposed)
    return respond(request, RequestList(items=[RequestSummary.model_validate(r) for r in rows]))


@router.post(
    "",
    status_code=201,
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_create_improvement_request",
    responses={422: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def create_request(
    payload: RequestCreate, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[RequestDetail]:
    """C/S 담당자가 개선요청 초안을 작성한다(DRAFT, 판본 1). 작성자가 담당자가 된다.

    Args:
        payload: 유형·제품·고장 또는 문서 신호, 내용, 근거 문의.
        request: DB 연결을 제공하는 요청.
        principal: 검증된 사용자. cs_agent만 작성한다.

    Returns:
        SuccessResponse[RequestDetail]: 새 요청서와 근거, 다음 담당.
    """
    require_writer(principal)
    try:
        product_family(payload.product_code)
    except RuleInputError:
        raise ApplicationError(422, "CS_UNKNOWN_PRODUCT", "The product is unknown.") from None
    settings = request.app.state.settings
    request_id = uuid4()
    values = {
        "id": request_id,
        "request_type": payload.request_type,
        "origin": "MANUAL",
        # Same rule as manual tickets: the deployment decides, never the request.
        "data_origin": (
            settings.cs_manual_data_origin if settings.business_relay_enabled else "demo"
        ),
        "product_code": payload.product_code,
        "fault_id": payload.fault_id,
        "doc_signal": payload.doc_signal,
        "cs_owner": principal.user_id,
        "created_by": principal.user_id,
        **{name: getattr(payload, name) for name in CONTENT_FIELDS},
    }
    async with connection(request.app.state.settings) as conn:
        await repository.insert_request(conn, values)
        stored = await repository.get_request(conn, request_id)
        assert stored is not None
        for item in payload.evidence:
            await validate_evidence(conn, stored, item)
            await repository.insert_evidence(
                conn,
                {
                    "id": uuid4(),
                    "request_id": request_id,
                    "ticket_id": item.ticket_id,
                    "case_id": item.case_id,
                    "added_by": principal.user_id,
                },
            )
        stored = await commit_version(
            conn,
            stored,
            principal.user_id,
            "create",
            {
                "request_type": {"to": payload.request_type},
                "evidence": {"to": len(payload.evidence)},
            },
        )
        data = await detail(conn, stored)
    return respond(request, data)


@router.get(
    "/{request_id}",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_get_improvement_request",
    openapi_extra=PARTIAL,
)
async def get_request(
    request_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[RequestDetail]:
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, request_id)
        row = await repository.get_request(conn, request_id)
        if row is None:
            raise not_found()
        data = await detail(conn, row)
    return respond(request, data)


@router.patch(
    "/{request_id}",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_update_improvement_request",
    responses={422: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def update_request(
    request_id: UUID,
    payload: RequestUpdate,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """Changes the content and stores it as a new version. Unchanged values make none."""
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, request_id)
        require_writer(principal)
        row = await repository.lock_request(conn, request_id)
        if row is None:
            raise not_found()
        check_open(row)
        check_version(row, payload.version)
        changes = {
            name: {"from": plain(row[name]), "to": plain(value)}
            for name, value in payload.changed_values().items()
            if row[name] != value
        }
        if (
            "proposed_depts" in changes
            and row["request_type"] == "FAULT"
            and not payload.proposed_depts
        ):
            raise ApplicationError(
                422, "CS_DEPARTMENT_REQUIRED", "A fault request needs a proposed department."
            )
        if changes:
            values = {name: payload.changed_values()[name] for name in changes}
            await repository.update_request(conn, request_id, row["version"], values)
            row = await commit_version(conn, row, principal.user_id, "update", changes)
        data = await detail(conn, row)
    return respond(request, data)


@router.get(
    "/{request_id}/versions",
    response_model=SuccessResponse[RequestVersionList],
    operation_id="cs_list_improvement_request_versions",
    openapi_extra=PARTIAL,
)
async def list_versions(
    request_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[RequestVersionList]:
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, request_id)
        if await repository.get_request(conn, request_id) is None:
            raise not_found()
        rows = await repository.list_versions(conn, request_id)
    return respond(
        request, RequestVersionList(items=[RequestVersion.model_validate(r) for r in rows])
    )


@router.post(
    "/{request_id}/evidence",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_add_improvement_request_evidence",
    responses={422: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def add_evidence(
    request_id: UUID,
    payload: EvidenceAdd,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """Links a ticket (and, for faults, its case). The ticket's state does not matter."""
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, request_id)
        require_writer(principal)
        row = await repository.lock_request(conn, request_id)
        if row is None:
            raise not_found()
        check_open(row)
        check_version(row, payload.version)
        active = {
            e["ticket_id"]
            for e in await repository.list_evidence(conn, request_id)
            if e["removed_at"] is None
        }
        if payload.ticket_id in active:
            raise ApplicationError(409, "CS_EVIDENCE_EXISTS", "The ticket is already evidence.")
        await validate_evidence(conn, row, payload)
        await repository.insert_evidence(
            conn,
            {
                "id": uuid4(),
                "request_id": request_id,
                "ticket_id": payload.ticket_id,
                "case_id": payload.case_id,
                "added_by": principal.user_id,
            },
        )
        await repository.update_request(conn, request_id, row["version"], {})
        row = await commit_version(
            conn,
            row,
            principal.user_id,
            "update",
            {
                "evidence_added": {
                    "to": {"ticket_id": str(payload.ticket_id), "case_id": plain(payload.case_id)}
                }
            },
        )
        data = await detail(conn, row)
    return respond(request, data)


@router.post(
    "/{request_id}/evidence/{evidence_id}/remove",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_remove_improvement_request_evidence",
    openapi_extra=PARTIAL,
)
async def remove_evidence(
    request_id: UUID,
    evidence_id: UUID,
    payload: EvidenceRemove,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """Takes a ticket out of the evidence with a reason. The link stays in the history."""
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, request_id)
        require_writer(principal)
        row = await repository.lock_request(conn, request_id)
        if row is None:
            raise not_found()
        check_open(row)
        check_version(row, payload.version)
        if not await repository.remove_evidence(
            conn, request_id, evidence_id, principal.user_id, payload.reason
        ):
            raise ApplicationError(
                404, "CS_EVIDENCE_NOT_FOUND", "The evidence was not found or already removed."
            )
        await repository.update_request(conn, request_id, row["version"], {})
        row = await commit_version(
            conn,
            row,
            principal.user_id,
            "update",
            {"evidence_removed": {"to": str(evidence_id)}, "reason": {"to": payload.reason}},
        )
        data = await detail(conn, row)
    return respond(request, data)


@router.put(
    "/{request_id}/owner",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_reassign_improvement_request_owner",
    openapi_extra=PARTIAL,
)
async def reassign_owner(
    request_id: UUID,
    payload: OwnerUpdate,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """Only a manager of the C/S department reassigns the C/S owner (permissions.md 2.2)."""
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, request_id)
        if not (principal.has("manager") and CS_DEPARTMENT in principal.managed_departments):
            raise HTTPException(403)
        row = await repository.lock_request(conn, request_id)
        if row is None:
            raise not_found()
        check_open(row)
        check_version(row, payload.version)
        if row["cs_owner"] != payload.owner_user_id:
            await repository.update_request(
                conn, request_id, row["version"], {"cs_owner": payload.owner_user_id}
            )
            row = await commit_version(
                conn,
                row,
                principal.user_id,
                "update",
                {"cs_owner": {"from": plain(row["cs_owner"]), "to": str(payload.owner_user_id)}},
            )
        data = await detail(conn, row)
    return respond(request, data)
