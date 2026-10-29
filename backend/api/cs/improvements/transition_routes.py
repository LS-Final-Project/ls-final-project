"""Improvement request transitions (KAN-116): review, delivery, department replies and
actions, follow-up and completion (permissions.md 2.2, 2.2.1).

Every action locks the request row and checks scope (404), role (403), state (409) and
version (409) in that order. The request status is never chosen by a person: department
actions change one department row and the status is summarized from the current round.
Re-requests and recurrence rounds (new rounds after the first delivery) are KAN-117.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.cs.improvements import repository
from backend.api.cs.improvements.routes import (
    check_open,
    check_version,
    check_visible,
    commit_version,
    detail,
    not_found,
    require_writer,
)
from backend.api.cs.improvements.routes import (
    router as base_router,
)
from backend.api.cs.improvements.schemas import (
    CompleteRequest,
    DeptActionRequest,
    DeptReplyRequest,
    FollowupPlanRequest,
    RequestDetail,
    ResendRequest,
    ReviewRequest,
    SendRequest,
    Versioned,
)
from backend.api.cs.improvements.transitions import (
    FROZEN_STATES,
    RESEND_FROM,
    followup_allowed,
    rejection_closed,
    reply_target,
    summarize,
)
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    prefix="/api/v1/cs/improvement-requests",
    tags=["improvement-requests"],
    responses=base_router.responses,
)
Row = dict[str, Any]


def invalid_transition() -> ApplicationError:
    return ApplicationError(
        409, "CS_REQUEST_INVALID_TRANSITION", "The request is not in a state for this action."
    )


def require_state(request: Row, *states: str) -> None:
    if request["status"] not in states:
        raise invalid_transition()


def require_department(principal: Principal, dept_code: str) -> None:
    """Only staff of the receiving department act for it (403, after the 404 scope check)."""
    if not principal.has("dept_staff") or principal.dept != dept_code:
        raise HTTPException(403)


async def begin(conn: Any, principal: Principal, request_id: UUID, version: int) -> Row:
    """Locks the request after the scope check and verifies the version."""
    await check_visible(conn, principal, request_id)
    row = await repository.lock_request(conn, request_id)
    if row is None:
        raise not_found()
    return row


async def commit_transition(conn: Any, request: Row, actor: UUID, action: str, changes: Row) -> Row:
    """Commits a version with audit action `transition`; the operation name is in changes.action
    (cs.audit_events allows create/update/transition/review only, like the ticket workflow)."""
    return await commit_version(
        conn, request, actor, "transition", {**changes, "action": {"to": action}}
    )


async def resummarize(conn: Any, request: Row, actor: UUID, action: str, changes: Row) -> Row:
    """Recomputes the request status from the current round and commits a version."""
    departments = await repository.departments_in_round(
        conn, request["id"], request["current_round"]
    )
    status = summarize(departments)
    values: Row = {"status": status} if status != request["status"] else {}
    await repository.update_request(conn, request["id"], request["version"], values)
    changes = {**changes, "status": {"from": request["status"], "to": status}}
    return await commit_transition(conn, request, actor, action, changes)


async def department_row(conn: Any, request: Row, dept_code: str) -> Row:
    rows = await repository.departments_in_round(conn, request["id"], request["current_round"])
    for row in rows:
        if row["dept_code"] == dept_code:
            return row
    raise ApplicationError(
        404, "CS_DEPARTMENT_NOT_IN_ROUND", "The department is not in this round."
    )


@router.post(
    "/{request_id}/review",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_review_improvement_request",
    openapi_extra=PARTIAL,
)
async def review(
    request_id: UUID,
    payload: ReviewRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """DRAFT → REVIEWED by a C/S agent, who becomes the owner if there is none."""
    async with connection(request.app.state.settings) as conn:
        row = await begin(conn, principal, request_id, payload.version)
        require_writer(principal)
        require_state(row, "DRAFT")
        check_version(row, payload.version)
        now = await repository.database_now(conn)
        values: Row = {"status": "REVIEWED", "reviewed_by": principal.user_id, "reviewed_at": now}
        if row["cs_owner"] is None:
            values["cs_owner"] = principal.user_id
        if payload.proposed_depts is not None:
            values["proposed_depts"] = sorted(set(payload.proposed_depts))
        await repository.update_request(conn, request_id, row["version"], values)
        fresh = await commit_transition(
            conn, row, principal.user_id, "review", {"status": {"from": "DRAFT", "to": "REVIEWED"}}
        )
        data = await detail(conn, fresh)
    return respond(request, data)


@router.post(
    "/{request_id}/send",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_send_improvement_request",
    responses={422: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def send(
    request_id: UUID,
    payload: SendRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """REVIEWED → first round: every target department starts at SENT.

    Delivery here records the hand-over; mail to departments is not sent by this service.
    A document request without a confirmed receiver cannot be sent (CONFIRM_RECEIVER first).
    """
    async with connection(request.app.state.settings) as conn:
        row = await begin(conn, principal, request_id, payload.version)
        require_writer(principal)
        require_state(row, "REVIEWED")
        check_version(row, payload.version)
        targets = sorted(set(payload.dept_codes or row["proposed_depts"]))
        if not targets:
            raise ApplicationError(
                422, "CS_DEPARTMENT_REQUIRED", "Confirm a receiving department before sending."
            )
        now = await repository.database_now(conn)
        due = now + timedelta(days=payload.reply_due_days)
        for dept in targets:
            await repository.insert_department(
                conn,
                {
                    "request_id": request_id,
                    "round": 1,
                    "dept_code": dept,
                    "status": "SENT",
                    "sent_version": row["version"] + 1,
                    "reply_due_at": due,
                },
            )
        await repository.update_request(
            conn, request_id, row["version"], {"status": "SENT", "current_round": 1, "sent_at": now}
        )
        fresh = await commit_transition(
            conn,
            row,
            principal.user_id,
            "send",
            {
                "status": {"from": "REVIEWED", "to": "SENT"},
                "round": {"to": 1},
                "departments": {"to": targets},
            },
        )
        data = await detail(conn, fresh)
    return respond(request, data)


@router.post(
    "/{request_id}/departments/{dept_code}/acknowledge",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_acknowledge_improvement_request",
    openapi_extra=PARTIAL,
)
async def acknowledge(
    request_id: UUID,
    dept_code: str,
    payload: Versioned,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """Department SENT → ACKNOWLEDGED by that department's staff."""
    async with connection(request.app.state.settings) as conn:
        row = await begin(conn, principal, request_id, payload.version)
        require_department(principal, dept_code)
        if row["status"] in FROZEN_STATES:
            raise invalid_transition()
        dept = await department_row(conn, row, dept_code)
        if dept["status"] != "SENT":
            raise invalid_transition()
        check_version(row, payload.version)
        await repository.update_department(
            conn, request_id, row["current_round"], dept_code, {"status": "ACKNOWLEDGED"}
        )
        await repository.insert_reply(
            conn,
            {
                "id": uuid4(),
                "request_id": request_id,
                "round": row["current_round"],
                "dept_code": dept_code,
                "request_version": row["version"],
                "reply_type": "ACKNOWLEDGED",
                "replied_by": principal.user_id,
            },
        )
        fresh = await resummarize(
            conn, row, principal.user_id, "acknowledge", {"dept_code": {"to": dept_code}}
        )
        data = await detail(conn, fresh)
    return respond(request, data)


@router.post(
    "/{request_id}/departments/{dept_code}/reply",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_reply_improvement_request",
    responses={422: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def reply(
    request_id: UUID,
    dept_code: str,
    payload: DeptReplyRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """Department ACKNOWLEDGED → ADOPTED / INFO_REQUESTED / REJECTED. A rejection needs a reason.

    The reply names the request version it answered. A reply is a claim, not a result:
    nothing here completes the request.
    """
    async with connection(request.app.state.settings) as conn:
        row = await begin(conn, principal, request_id, payload.version)
        require_department(principal, dept_code)
        if row["status"] in FROZEN_STATES:
            raise invalid_transition()
        dept = await department_row(conn, row, dept_code)
        if dept["status"] != "ACKNOWLEDGED":
            raise invalid_transition()
        check_version(row, payload.version)
        target = reply_target(payload.reply_type)
        values: Row = {"status": target}
        if target != "REJECTED":
            values.update({"rejection_reviewed_by": None, "rejection_reviewed_at": None})
        await repository.update_department(
            conn, request_id, row["current_round"], dept_code, values
        )
        await repository.insert_reply(
            conn,
            {
                "id": uuid4(),
                "request_id": request_id,
                "round": row["current_round"],
                "dept_code": dept_code,
                "request_version": row["version"],
                "reply_type": payload.reply_type,
                "reason": payload.reason,
                "replied_by": principal.user_id,
            },
        )
        fresh = await resummarize(
            conn,
            row,
            principal.user_id,
            "reply",
            {"dept_code": {"to": dept_code}, "reply_type": {"to": payload.reply_type}},
        )
        data = await detail(conn, fresh)
    return respond(request, data)


@router.post(
    "/{request_id}/departments/{dept_code}/action",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_record_improvement_action",
    responses={422: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def record_action(
    request_id: UUID,
    dept_code: str,
    payload: DeptActionRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """Department ADOPTED → ACTION_TAKEN with the action, its scope, date and evidence.

    An adopted department does not wait for the others. The action is the department's
    improvement, not a single customer's repair.
    """
    async with connection(request.app.state.settings) as conn:
        row = await begin(conn, principal, request_id, payload.version)
        require_department(principal, dept_code)
        if row["status"] in FROZEN_STATES:
            raise invalid_transition()
        dept = await department_row(conn, row, dept_code)
        if dept["status"] != "ADOPTED":
            raise invalid_transition()
        check_version(row, payload.version)
        await repository.insert_action(
            conn,
            {
                "id": uuid4(),
                "request_id": request_id,
                "round": row["current_round"],
                "dept_code": dept_code,
                "action": payload.action,
                "action_scope": payload.action_scope,
                "action_date": payload.action_date,
                "action_evidence": payload.action_evidence,
                "recorded_by": principal.user_id,
            },
        )
        await repository.update_department(
            conn, request_id, row["current_round"], dept_code, {"status": "ACTION_TAKEN"}
        )
        fresh = await resummarize(
            conn,
            row,
            principal.user_id,
            "record_action",
            {"dept_code": {"to": dept_code}, "action_date": {"to": str(payload.action_date)}},
        )
        data = await detail(conn, fresh)
    return respond(request, data)


@router.post(
    "/{request_id}/departments/{dept_code}/resend",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_resend_improvement_request",
    responses={422: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def resend(
    request_id: UUID,
    dept_code: str,
    payload: ResendRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """Calls one department again inside the current round (C/S agent).

    INFO_REQUESTED → SENT after supplementing; REJECTED → SENT only with evidence added after
    the rejection (422 CS_EVIDENCE_REQUIRED otherwise); SENT / ACKNOWLEDGED → SENT as a
    reminder only after the reply deadline passed (409 otherwise). The round does not change.
    """
    async with connection(request.app.state.settings) as conn:
        row = await begin(conn, principal, request_id, payload.version)
        require_writer(principal)
        if row["status"] in FROZEN_STATES or row["current_round"] == 0:
            raise invalid_transition()
        departments = await repository.departments_in_round(conn, request_id, row["current_round"])
        if rejection_closed(departments):
            raise invalid_transition()
        dept = next((d for d in departments if d["dept_code"] == dept_code), None)
        if dept is None:
            raise ApplicationError(
                404, "CS_DEPARTMENT_NOT_IN_ROUND", "The department is not in this round."
            )
        if dept["status"] not in RESEND_FROM:
            raise invalid_transition()
        check_version(row, payload.version)
        now = await repository.database_now(conn)
        if dept["status"] == "REJECTED":
            rejected_at = await repository.latest_rejection_at(
                conn, request_id, row["current_round"], dept_code
            )
            if not await repository.evidence_added_after(conn, request_id, rejected_at):
                raise ApplicationError(
                    422,
                    "CS_EVIDENCE_REQUIRED",
                    "Re-requesting a rejecting department needs new evidence.",
                )
        elif dept["status"] in ("SENT", "ACKNOWLEDGED"):
            if dept["reply_due_at"] is None or dept["reply_due_at"] > now:
                raise invalid_transition()
        kind = {"INFO_REQUESTED": "supplement", "REJECTED": "re_request"}.get(
            dept["status"], "remind"
        )
        await repository.update_department(
            conn,
            request_id,
            row["current_round"],
            dept_code,
            {
                "status": "SENT",
                "sent_version": row["version"] + 1,
                "reply_due_at": now + timedelta(days=payload.reply_due_days),
                "rejection_reviewed_by": None,
                "rejection_reviewed_at": None,
            },
        )
        fresh = await resummarize(
            conn,
            row,
            principal.user_id,
            "resend",
            {
                "dept_code": {"to": dept_code},
                "kind": {"to": kind},
                "reason": {"to": payload.reason},
            },
        )
        data = await detail(conn, fresh)
    return respond(request, data)


@router.post(
    "/{request_id}/departments/{dept_code}/review-rejection",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_review_improvement_rejection",
    openapi_extra=PARTIAL,
)
async def review_rejection(
    request_id: UUID,
    dept_code: str,
    payload: Versioned,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """The C/S agent reviewed a rejection and will not re-request. No state change for the
    department; once every department is rejected and reviewed the request is closed by
    rejection and only a new round (KAN-117) reopens it."""
    async with connection(request.app.state.settings) as conn:
        row = await begin(conn, principal, request_id, payload.version)
        require_writer(principal)
        if row["status"] in FROZEN_STATES:
            raise invalid_transition()
        dept = await department_row(conn, row, dept_code)
        if dept["status"] != "REJECTED" or dept["rejection_reviewed_at"] is not None:
            raise invalid_transition()
        check_version(row, payload.version)
        now = await repository.database_now(conn)
        await repository.update_department(
            conn,
            request_id,
            row["current_round"],
            dept_code,
            {"rejection_reviewed_by": principal.user_id, "rejection_reviewed_at": now},
        )
        fresh = await resummarize(
            conn, row, principal.user_id, "review_rejection", {"dept_code": {"to": dept_code}}
        )
        data = await detail(conn, fresh)
    return respond(request, data)


@router.post(
    "/{request_id}/followup-plan",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_plan_improvement_followup",
    openapi_extra=PARTIAL,
)
async def plan_followup(
    request_id: UUID,
    payload: FollowupPlanRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """ACTION_TAKEN → FOLLOW_UP once every department acted or had its rejection reviewed and
    at least one action exists. The plan (target, method, period) is written before checking."""
    async with connection(request.app.state.settings) as conn:
        row = await begin(conn, principal, request_id, payload.version)
        require_writer(principal)
        require_state(row, "ACTION_TAKEN")
        departments = await repository.departments_in_round(conn, request_id, row["current_round"])
        if not followup_allowed(departments):
            raise invalid_transition()
        check_version(row, payload.version)
        await repository.update_request(
            conn,
            request_id,
            row["version"],
            {"status": "FOLLOW_UP", "followup_plan": payload.followup_plan},
        )
        fresh = await commit_transition(
            conn,
            row,
            principal.user_id,
            "plan_followup",
            {"status": {"from": "ACTION_TAKEN", "to": "FOLLOW_UP"}},
        )
        data = await detail(conn, fresh)
    return respond(request, data)


@router.post(
    "/{request_id}/complete",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_complete_improvement_request",
    openapi_extra=PARTIAL,
)
async def complete(
    request_id: UUID,
    payload: CompleteRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """FOLLOW_UP → COMPLETED: a person's decision after the follow-up result is recorded.

    A department reply or action never completes a request. Monitoring for recurrence
    continues after completion (KAN-117 reopens the request in a new round).
    """
    async with connection(request.app.state.settings) as conn:
        row = await begin(conn, principal, request_id, payload.version)
        require_writer(principal)
        check_open(row)
        require_state(row, "FOLLOW_UP")
        check_version(row, payload.version)
        now = await repository.database_now(conn)
        await repository.update_request(
            conn,
            request_id,
            row["version"],
            {
                "status": "COMPLETED",
                "followup_result": payload.followup_result,
                "completed_by": principal.user_id,
                "completed_at": now,
            },
        )
        fresh = await commit_transition(
            conn,
            row,
            principal.user_id,
            "complete",
            {"status": {"from": "FOLLOW_UP", "to": "COMPLETED"}},
        )
        data = await detail(conn, fresh)
    return respond(request, data)
