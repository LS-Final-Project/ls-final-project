"""Re-request and recurrence after delivery (KAN-117, contracts/cs/improvement-followups.md).

`GET /followup-signals` computes the candidate on read; nothing is stored. `POST /rounds` is
the person's decision that opens the next round (permissions.md 2.2, 2.2.1). The engine is
`backend/engine/cs/improvement_followups.py`.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request

from backend.api.cs.improvements import repository
from backend.api.cs.improvements.routes import (
    check_version,
    check_visible,
    detail,
    not_found,
    require_writer,
)
from backend.api.cs.improvements.routes import router as base_router
from backend.api.cs.improvements.schemas import RequestDetail, RoundRequest
from backend.api.cs.improvements.transition_routes import commit_transition, invalid_transition
from backend.api.cs.improvements.transitions import rejection_closed, summarize
from backend.api.cs.tickets.access import sees_all_tickets
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.engine.cs.improvement_followups import (
    Action,
    Record,
    RequestState,
    Round,
    Signal,
    evaluate,
)
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import SuccessResponse

router = APIRouter(
    prefix="/api/v1/cs/improvement-requests",
    tags=["improvement-requests"],
    responses=base_router.responses,
)
Row = dict[str, Any]
# Re-request opens only before action (permissions.md 2.2), or from rejection-closed.
REQUEST_AGAIN_FROM = ("SENT", "ACKNOWLEDGED", "ADOPTED", "INFO_REQUESTED")


async def signal_for(conn: Any, request: Row, as_of: datetime) -> tuple[Signal, list[Row]]:
    """The KAN-117 signal of one request at `as_of`, and its current-round departments."""
    if request["request_type"] != "FAULT":
        # Document requests have no resolution-record bundle to watch.
        raise ApplicationError(
            409, "CS_FOLLOWUP_FAULT_ONLY", "Re-request and recurrence apply to fault requests."
        )
    rounds = [
        Round(round=r["round"], opened_at=r["opened_at"])
        for r in await repository.list_rounds(conn, request["id"])
    ]
    if not rounds and request["current_round"] >= 1 and request["sent_at"] is not None:
        # Sent before KAN-117 recorded rounds: the first send is round 1.
        rounds = [Round(round=1, opened_at=request["sent_at"])]
    actions = [
        Action(
            round=a["round"],
            dept_code=a["dept_code"],
            action_date=a["action_date"],
            recorded_at=a["recorded_at"],
            scope_kind=a["scope_kind"],
            scope_value=a["scope_value"],
        )
        for a in await repository.list_actions(conn, request["id"])
    ]
    records = [
        Record(
            case_id=str(r["case_id"]),
            ticket_id=str(r["ticket_id"]),
            customer_id=r["customer_id"],
            known_at=r["known_at"],
            received_at=r["received_at"],
            fw_version_at_issue=r["fw_version_at_issue"],
            shipped_on=r["ship_date"],
            lot=r["lot_no"],
        )
        for r in await repository.bundle_records(conn, request["product_code"], request["fault_id"])
    ]
    departments = (
        await repository.current_departments(conn, request) if request["current_round"] else []
    )
    status = request["status"]
    signal = evaluate(RequestState(status=status, rounds=rounds, actions=actions), records, as_of)
    return signal, departments


@router.get(
    "/{request_id}/followup-signals",
    response_model=SuccessResponse[Signal],
    operation_id="cs_get_improvement_followup_signals",
    openapi_extra=PARTIAL,
)
async def get_signals(
    request_id: UUID,
    request: Request,
    as_of: datetime | None = Query(
        default=None, description="Judgement time (time zone required). Default now."
    ),
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Signal]:
    """Re-request / recurrence candidate at `as_of`. Same read scope as the request."""
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, request_id)
        row = await repository.get_request(conn, request_id)
        if row is None:
            raise not_found()
        now = await repository.database_now(conn)
        moment = as_of or now
        if moment.tzinfo is None or moment > now:
            raise ApplicationError(
                422, "CS_INVALID_AS_OF", "as_of needs a time zone and cannot be in the future."
            )
        signal, _ = await signal_for(conn, row, moment)
    if not sees_all_tickets(principal):
        # Department users and other departments' managers may read the request, but the
        # bundle holds other customers' records: their customer ids are not shown.
        signal = signal.model_copy(
            update={
                "records": [r.model_copy(update={"customer_id": None}) for r in signal.records],
                "update_notice_customers": [],
            }
        )
    return respond(request, signal)


@router.post(
    "/{request_id}/rounds",
    response_model=SuccessResponse[RequestDetail],
    operation_id="cs_open_improvement_round",
    openapi_extra=PARTIAL,
)
async def open_round(
    request_id: UUID,
    payload: RoundRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestDetail]:
    """A C/S agent opens the next round for a re-request or a recurrence (permissions.md 2.2).

    The candidate is checked again at the server's current time, which is also the round's
    opening time: records known between the person's `as_of` and now join this round's
    evidence and are not lost from the next count. Completing records of a reopened
    request are copied into the round row before the request starts again.
    """
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, request_id)
        row = await repository.lock_request(conn, request_id)
        if row is None:
            raise not_found()
        require_writer(principal)
        now = await repository.database_now(conn)
        if payload.as_of > now:
            raise ApplicationError(422, "CS_INVALID_AS_OF", "as_of cannot be in the future.")
        signal, departments = await signal_for(conn, row, now)
        if not await repository.list_rounds(conn, request_id) and row["sent_at"] is not None:
            # Sent before KAN-117 recorded rounds: keep the history complete with round 1 at
            # the original send, by the person the audit trail shows sent it.
            await repository.insert_round(
                conn,
                {
                    "request_id": request_id,
                    "round": 1,
                    "kind": "FIRST",
                    "opened_at": row["sent_at"],
                    "opened_by": await repository.sent_by(conn, request_id) or principal.user_id,
                },
            )
        closed = rejection_closed(departments)
        if payload.kind == "REQUEST_AGAIN":
            if not (
                row["status"] in REQUEST_AGAIN_FROM or (row["status"] == "REJECTED" and closed)
            ):
                raise invalid_transition()
        elif signal.phase == "NOT_SENT":
            raise invalid_transition()
        check_version(row, payload.version)
        evidence_tickets: list[tuple[UUID, UUID | None]] = []
        if payload.kind == "REQUEST_AGAIN":
            if signal.counts.candidate_index >= 1:
                evidence_tickets = [
                    (UUID(r.ticket_id), UUID(r.case_id))
                    for r in signal.records
                    if r.record_class not in ("IN_EARLIER_ROUND", "RECURRENCE")
                ]
            elif closed and payload.new_evidence is not None:
                for ticket_id in payload.new_evidence.ticket_ids:
                    ticket, _ = await repository.ticket_and_case(conn, ticket_id, None)
                    if ticket is None or ticket["product_code"] != row["product_code"]:
                        raise ApplicationError(
                            422,
                            "CS_EVIDENCE_INVALID",
                            "New evidence must be a ticket of this product.",
                        )
                    evidence_tickets.append((ticket_id, None))
            elif closed:
                raise ApplicationError(
                    422,
                    "CS_EVIDENCE_REQUIRED",
                    "A rejection-closed request needs a candidate or new evidence.",
                )
            else:
                raise ApplicationError(
                    409, "CS_FOLLOWUP_NOT_CANDIDATE", "There is no re-request candidate."
                )
            default = (
                [d["dept_code"] for d in departments]
                if closed
                else [
                    d["dept_code"]
                    for d in departments
                    if d["status"] != "ACTION_TAKEN"
                    and not (d["status"] == "REJECTED" and d["rejection_reviewed_at"] is not None)
                ]
            )
        else:
            if signal.decision != "RECURRENCE":
                raise ApplicationError(
                    409, "CS_FOLLOWUP_NOT_CANDIDATE", "There is no recurrence candidate."
                )
            recurrences = [r for r in signal.records if r.record_class == "RECURRENCE"]
            evidence_tickets = [(UUID(r.ticket_id), UUID(r.case_id)) for r in recurrences]
            # The department whose action the recurrence happened under is called again.
            default = sorted({r.action_dept for r in recurrences if r.action_dept})
        targets = sorted(set(payload.dept_codes or default))
        if not targets:
            raise ApplicationError(422, "CS_DEPARTMENT_REQUIRED", "Choose at least one department.")
        new_round = row["current_round"] + 1
        due = now + timedelta(days=payload.reply_due_days)
        new_rows: list[Row] = []
        for dept in departments:
            if dept["dept_code"] in targets:
                continue
            # Not called again: the department keeps its state in the new round (carried over).
            carried = {
                "request_id": request_id,
                "round": new_round,
                "dept_code": dept["dept_code"],
                "status": dept["status"],
                "carried_over": True,
                "sent_version": dept["sent_version"],
                "reply_due_at": dept["reply_due_at"],
                "rejection_reviewed_by": dept["rejection_reviewed_by"],
                "rejection_reviewed_at": dept["rejection_reviewed_at"],
            }
            await repository.insert_department(conn, carried)
            new_rows.append(carried)
        for dept_code in targets:
            sent = {
                "request_id": request_id,
                "round": new_round,
                "dept_code": dept_code,
                "status": "SENT",
                "sent_version": row["version"] + 1,
                "reply_due_at": due,
            }
            await repository.insert_department(conn, sent)
            new_rows.append({**sent, "rejection_reviewed_at": None})
        existing = await repository.active_evidence_tickets(conn, request_id)
        added = []
        for ticket_id, case_id in evidence_tickets:
            if ticket_id in existing:
                continue
            await repository.insert_evidence(
                conn,
                {
                    "id": uuid4(),
                    "request_id": request_id,
                    "ticket_id": ticket_id,
                    "case_id": case_id,
                    "added_by": principal.user_id,
                },
            )
            existing.add(ticket_id)
            added.append(str(ticket_id))
        previous = None
        values: Row = {"status": summarize(new_rows), "current_round": new_round}
        if row["status"] in ("FOLLOW_UP", "COMPLETED"):
            # The finished round's follow-up and completion stay as that round's record.
            previous = {
                "round": row["current_round"],
                "followup_plan": row["followup_plan"],
                "followup_result": row["followup_result"],
                "completed_by": str(row["completed_by"]) if row["completed_by"] else None,
                "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
            }
            values |= {
                "followup_plan": None,
                "followup_result": None,
                "completed_by": None,
                "completed_at": None,
            }
        await repository.insert_round(
            conn,
            {
                "request_id": request_id,
                "round": new_round,
                "kind": payload.kind,
                "opened_at": now,
                "opened_by": principal.user_id,
                "candidate_index": signal.counts.candidate_index
                if payload.kind == "REQUEST_AGAIN" and signal.counts.candidate_index
                else None,
                "as_of": payload.as_of,
                "new_evidence_note": payload.new_evidence.note if payload.new_evidence else None,
                "previous_completion": previous,
            },
        )
        await repository.update_request(conn, request_id, row["version"], values)
        fresh = await commit_transition(
            conn,
            row,
            principal.user_id,
            "open_round",
            {
                "status": {"from": row["status"], "to": values["status"]},
                "round": {"from": row["current_round"], "to": new_round},
                "kind": {"to": payload.kind},
                "departments": {"to": targets},
                "evidence_added": {"to": added},
                "as_of": {"to": payload.as_of.isoformat()},
            },
        )
        data = await detail(conn, fresh, principal)
    return respond(request, data)
