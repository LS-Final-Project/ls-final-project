"""Manager batch assignment and the agent skill table (contracts/cs/assignment.md).

Preview writes nothing. Apply takes the previewed pairs, decides the same tickets again in the
same order with the current loads and writes only the pairs that still come out the same, so a
manager never applies something other than what they saw. Skills are read-only here: they are
set by the local seed or the operator script, never through the API.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.cs.assignments import repository
from backend.api.cs.assignments.schemas import (
    AgentList,
    AgentSkill,
    ApplyRequest,
    ApplyResponse,
    ApplyResult,
    Preview,
    PreviewRequest,
    Proposal,
    RequestApplyRequest,
    RequestApplyResponse,
    RequestApplyResult,
    RequestPreview,
    RequestProposal,
    UnmatchedRequest,
    UnmatchedRequests,
    UnmatchedTicket,
    UnmatchedTickets,
)
from backend.api.cs.assignments.service import (
    RequestAssigner,
    agents_from,
    family_of,
    priority_of,
    record,
    record_no_match,
    ticket_input,
)
from backend.api.cs.improvements import repository as improvements
from backend.api.cs.improvements.routes import commit_version
from backend.api.cs.tickets.access import sees_all_tickets
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.api.cs.translation import repository as translations
from backend.api.cs.workflow.service import lock
from backend.engine.cs.assignment import (
    ASSIGNMENT_RULES_VERSION,
    IMPROVEMENT_RULES_VERSION,
    Decision,
    RequestDecision,
    TicketInput,
    assign_batch,
)
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.departments import CS_DEPARTMENT
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    prefix="/api/v1/cs/assignments",
    tags=["assignments"],
    responses={
        401: {"model": ErrorResponse, "description": "No valid user context"},
        403: {"model": ErrorResponse, "description": "The role may not take this action"},
        503: {"model": ErrorResponse},
    },
)
Row = dict[str, Any]


def require_cs_manager(principal: Principal) -> None:
    """Same check as reassigning an owner (PUT /workflow/owner)."""
    if not (principal.has("manager") and CS_DEPARTMENT in principal.managed_departments):
        raise HTTPException(403)


async def proposals(conn: Any, rows: list[Row]) -> list[Proposal]:
    """Decides `rows` as one batch with the current agents and loads."""
    agents = agents_from(await repository.list_agents(conn))
    inputs = [ticket_input(r, await priority_of(conn, r["id"])) for r in rows]
    by_id = {r["id"]: r for r in rows}
    priority = {t.ticket_id: t.priority for t in inputs}
    family = {t.ticket_id: t.family for t in inputs}
    return [
        Proposal(
            ticket_id=d.ticket_id,
            external_id=by_id[d.ticket_id]["external_id"],
            subject=by_id[d.ticket_id]["subject"],
            product_code=by_id[d.ticket_id]["product_code"],
            family=family[d.ticket_id],
            body_language=by_id[d.ticket_id]["body_language"],
            received_at=by_id[d.ticket_id]["received_at"],
            priority=priority[d.ticket_id],
            user_id=d.user_id,
            reason_code=d.reason,
            matched_language=d.matched_language,
            matched_family=d.matched_family,
            candidate_count=d.candidate_count,
            chosen_open_count=d.chosen_open_count,
        )
        for d in assign_batch(inputs, agents)
    ]


@router.get(
    "/agents",
    response_model=SuccessResponse[AgentList],
    operation_id="cs_list_assignment_agents",
    openapi_extra=PARTIAL,
)
async def list_agents(
    request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[AgentList]:
    """The agent skill table (assumed data) with each agent's open tickets. Names are not
    here: the C/S service cannot read identity, so the screen reads them from the platform."""
    if not sees_all_tickets(principal):
        raise HTTPException(403)
    async with connection(request.app.state.settings) as conn:
        rows = await repository.list_agents(conn)
        loads = await repository.request_loads(conn)
    return respond(
        request,
        AgentList(
            rule_version=ASSIGNMENT_RULES_VERSION,
            request_rule_version=IMPROVEMENT_RULES_VERSION,
            items=[
                AgentSkill.model_validate({**r, "open_request_count": loads.get(r["user_id"], 0)})
                for r in rows
            ],
        ),
    )


@router.post(
    "/preview",
    response_model=SuccessResponse[Preview],
    operation_id="cs_preview_assignments",
    openapi_extra=PARTIAL,
)
async def preview(
    payload: PreviewRequest, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[Preview]:
    """Proposed owners for the oldest unassigned RECEIVED tickets. Writes nothing."""
    require_cs_manager(principal)
    async with connection(request.app.state.settings) as conn:
        rows = await repository.unassigned_tickets(conn, payload.limit)
        items = await proposals(conn, rows)
    return respond(request, Preview(rule_version=ASSIGNMENT_RULES_VERSION, items=items))


@router.post(
    "/apply",
    response_model=SuccessResponse[ApplyResponse],
    operation_id="cs_apply_assignments",
    responses={
        409: {"model": ErrorResponse, "description": "The rules changed since the preview"},
        422: {"model": ErrorResponse},
    },
    openapi_extra=PARTIAL,
)
async def apply(
    payload: ApplyRequest, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[ApplyResponse]:
    """Writes the previewed owners that the rules still give. Running it again changes nothing.

    Each ticket is locked and must still be RECEIVED without an owner. A ticket whose decision
    now differs from the preview (another agent's load or skills changed) is not written and
    comes back as PREVIEW_CHANGED with the new proposal; preview again to see it.
    """
    require_cs_manager(principal)
    if payload.rule_version != ASSIGNMENT_RULES_VERSION:
        raise ApplicationError(
            409, "CS_ASSIGNMENT_RULES_CHANGED", "The assignment rules changed. Preview again."
        )
    wanted = {item.ticket_id: item.user_id for item in payload.items}
    if len(wanted) != len(payload.items):
        raise ApplicationError(422, "VALIDATION_ERROR", "A ticket appears twice.")
    results: dict[UUID, ApplyResult] = {}
    async with connection(request.app.state.settings) as conn:
        # Lock in a fixed order so two managers applying at once cannot deadlock.
        for ticket_id in sorted(wanted, key=str):
            try:
                await lock(conn, ticket_id)
            except ApplicationError:
                results[ticket_id] = ApplyResult(
                    ticket_id=ticket_id, outcome="NOT_FOUND", user_id=None, reason_code=None
                )
        rows = await repository.tickets_by_ids(conn, list(wanted))
        open_rows = []
        for row in rows:
            if row["owner_user_id"] is not None:
                outcome = "ALREADY_ASSIGNED"
            elif row["state"] != "RECEIVED":
                outcome = "NOT_RECEIVED"
            else:
                open_rows.append(row)
                continue
            results[row["id"]] = ApplyResult(
                ticket_id=row["id"], outcome=outcome, user_id=row["owner_user_id"], reason_code=None
            )
        items = await proposals(conn, open_rows)
        for item in items:
            if item.user_id != wanted[item.ticket_id]:
                outcome = "PREVIEW_CHANGED"
            elif item.user_id is None:
                # 관리자가 확인한 NO_MATCH도 판단 기록으로 남긴다(KAN-234). 상태·담당은 그대로.
                await record_no_match(
                    conn, decision_of(item), input_of(item), "BATCH", principal.user_id
                )
                outcome = "NO_MATCH"
            else:
                workflow = await lock(conn, item.ticket_id)
                await record(conn, workflow, decision_of(item), "BATCH", principal.user_id)
                outcome = "ASSIGNED"
            results[item.ticket_id] = ApplyResult(
                ticket_id=item.ticket_id,
                outcome=outcome,
                user_id=item.user_id,
                reason_code=item.reason_code,
            )
    ordered = [results[item.ticket_id] for item in payload.items]
    assigned = sum(r.outcome == "ASSIGNED" for r in ordered)
    return respond(
        request,
        ApplyResponse(
            rule_version=ASSIGNMENT_RULES_VERSION,
            assigned=assigned,
            skipped=len(ordered) - assigned,
            items=ordered,
        ),
    )


def decision_of(item: Proposal) -> Decision:
    return Decision(
        ticket_id=item.ticket_id,
        user_id=item.user_id,
        reason=item.reason_code,
        matched_language=item.matched_language,
        matched_family=item.matched_family,
        candidate_count=item.candidate_count,
        chosen_open_count=item.chosen_open_count,
    )


def input_of(item: Proposal) -> TicketInput:
    """미리보기 제안이 판단에 쓴 입력(언어·제품군)."""
    return TicketInput(
        ticket_id=item.ticket_id,
        language=item.body_language,
        family=item.family,
        received_at=item.received_at,
        priority=item.priority,
    )


@router.get(
    "/unmatched",
    response_model=SuccessResponse[UnmatchedTickets],
    operation_id="cs_list_unmatched_tickets",
    openapi_extra=PARTIAL,
)
async def unmatched_tickets(
    request: Request,
    principal: Principal = Depends(current_principal),
    limit: int = Query(default=100, ge=1, le=200),
) -> SuccessResponse[UnmatchedTickets]:
    """맞는 담당자 없는 문의(KAN-234): 지금 미배정·RECEIVED이고 최신 배정 판단이 NO_MATCH.

    최근 접수 순 `limit`건. 배정을 거치지 않은 과거 적재 문의는 판단 기록이 없어 빠진다.
    """
    require_cs_manager(principal)
    async with connection(request.app.state.settings) as conn:
        rows = await repository.unmatched_tickets(conn, limit + 1)
        korean = await translations.korean_subjects(conn, [r["id"] for r in rows[:limit]])
    return respond(
        request,
        UnmatchedTickets(
            items=[
                UnmatchedTicket.model_validate(
                    {**r, "ticket_id": r["id"], "subject_ko": korean.get(r["id"])}
                )
                for r in rows[:limit]
            ],
            has_more=len(rows) > limit,
        ),
    )


# --- Improvement requests (cs_owner) ------------------------------------------------------


@router.get(
    "/improvements/unmatched",
    response_model=SuccessResponse[UnmatchedRequests],
    operation_id="cs_list_unmatched_improvements",
    openapi_extra=PARTIAL,
)
async def unmatched_requests(
    request: Request,
    principal: Principal = Depends(current_principal),
    limit: int = Query(default=100, ge=1, le=200),
) -> SuccessResponse[UnmatchedRequests]:
    """맞는 담당자 없는 개선요청(KAN-234): C/S 담당 없는 진행 요청 중 최신 판단이 NO_MATCH.

    제품군은 지금 제품 코드에서 다시 구한다. 개선요청의 NO_MATCH 기록은 제품군을 적지 않기
    때문이다(null = product_master에 없는 제품).
    """
    require_cs_manager(principal)
    async with connection(request.app.state.settings) as conn:
        rows = await repository.unmatched_requests(conn, limit + 1)
    return respond(
        request,
        UnmatchedRequests(
            items=[
                UnmatchedRequest.model_validate(
                    {**r, "request_id": r["id"], "family": family_of(r["product_code"])}
                )
                for r in rows[:limit]
            ],
            has_more=len(rows) > limit,
        ),
    )


def request_proposals(assigner: RequestAssigner, rows: list[Row]) -> list[RequestProposal]:
    """Decides `rows` oldest first; each proposal counts toward the next (load spreading)."""
    proposals = []
    for row in sorted(rows, key=lambda r: (r["created_at"], str(r["id"]))):
        decision = assigner.decide(row["id"], row["product_code"], row["created_at"])
        assert decision is not None
        assigner.take(decision)
        proposals.append(
            RequestProposal(
                request_id=row["id"],
                request_type=row["request_type"],
                product_code=row["product_code"],
                fault_id=row["fault_id"],
                family=family_of(row["product_code"]),
                status=row["status"],
                created_at=row["created_at"],
                user_id=decision.user_id,
                reason_code=decision.reason,
                matched_family=decision.matched_family,
                candidate_count=decision.candidate_count,
                chosen_open_count=decision.chosen_open_count,
            )
        )
    return proposals


async def request_assigner(conn: Any) -> RequestAssigner:
    """The skill table and request loads; here a read failure is an error, not a silent skip."""
    return RequestAssigner(
        agents_from(await repository.list_agents(conn)), await repository.request_loads(conn), True
    )


@router.post(
    "/improvements/preview",
    response_model=SuccessResponse[RequestPreview],
    operation_id="cs_preview_improvement_assignments",
    openapi_extra=PARTIAL,
)
async def preview_requests(
    payload: PreviewRequest, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[RequestPreview]:
    """Proposed C/S owners for open improvement requests without one. Writes nothing."""
    require_cs_manager(principal)
    async with connection(request.app.state.settings) as conn:
        rows = await repository.unassigned_requests(conn, payload.limit)
        items = request_proposals(await request_assigner(conn), rows)
    return respond(request, RequestPreview(rule_version=IMPROVEMENT_RULES_VERSION, items=items))


@router.post(
    "/improvements/apply",
    response_model=SuccessResponse[RequestApplyResponse],
    operation_id="cs_apply_improvement_assignments",
    responses={
        409: {"model": ErrorResponse, "description": "The rules changed since the preview"},
        422: {"model": ErrorResponse},
    },
    openapi_extra=PARTIAL,
)
async def apply_requests(
    payload: RequestApplyRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[RequestApplyResponse]:
    """Writes the previewed owners the rules still give, like the ticket apply. Idempotent.

    Each request is locked and must still be open without a C/S owner. The owner change gets a
    new request version and audit event like a manager reassignment.
    """
    require_cs_manager(principal)
    if payload.rule_version != IMPROVEMENT_RULES_VERSION:
        raise ApplicationError(
            409, "CS_ASSIGNMENT_RULES_CHANGED", "The assignment rules changed. Preview again."
        )
    wanted = {item.request_id: item.user_id for item in payload.items}
    if len(wanted) != len(payload.items):
        raise ApplicationError(422, "VALIDATION_ERROR", "A request appears twice.")
    results: dict[UUID, RequestApplyResult] = {}
    async with connection(request.app.state.settings) as conn:
        locked: dict[UUID, Row] = {}
        for request_id in sorted(wanted, key=str):
            row = await improvements.lock_request(conn, request_id)
            if row is None:
                results[request_id] = RequestApplyResult(
                    request_id=request_id, outcome="NOT_FOUND", user_id=None, reason_code=None
                )
            else:
                locked[request_id] = row
        open_rows = []
        for row in await repository.requests_by_ids(conn, list(locked)):
            if row["cs_owner"] is not None:
                outcome = "ALREADY_ASSIGNED"
            elif not row["is_open"]:
                outcome = "NOT_OPEN"
            else:
                open_rows.append(row)
                continue
            results[row["id"]] = RequestApplyResult(
                request_id=row["id"], outcome=outcome, user_id=row["cs_owner"], reason_code=None
            )
        assigner = await request_assigner(conn)
        for item in request_proposals(assigner, open_rows):
            if item.user_id != wanted[item.request_id]:
                outcome = "PREVIEW_CHANGED"
            elif item.user_id is None:
                outcome = "NO_MATCH"
            else:
                row = locked[item.request_id]
                await improvements.update_request(
                    conn, item.request_id, row["version"], {"cs_owner": item.user_id}
                )
                decision = RequestDecision(
                    request_id=item.request_id,
                    user_id=item.user_id,
                    reason=item.reason_code,
                    matched_family=item.matched_family,
                    candidate_count=item.candidate_count,
                    chosen_open_count=item.chosen_open_count,
                )
                await assigner.record(conn, decision, "BATCH", principal.user_id)
                await commit_version(
                    conn,
                    row,
                    principal.user_id,
                    "update",
                    {
                        "cs_owner": {"from": None, "to": str(item.user_id)},
                        "assignment_reason": {"to": item.reason_code},
                        "rule_version": {"to": IMPROVEMENT_RULES_VERSION},
                    },
                )
                outcome = "ASSIGNED"
            results[item.request_id] = RequestApplyResult(
                request_id=item.request_id,
                outcome=outcome,
                user_id=item.user_id,
                reason_code=item.reason_code,
            )
    ordered = [results[item.request_id] for item in payload.items]
    assigned = sum(r.outcome == "ASSIGNED" for r in ordered)
    return respond(
        request,
        RequestApplyResponse(
            rule_version=IMPROVEMENT_RULES_VERSION,
            assigned=assigned,
            skipped=len(ordered) - assigned,
            items=ordered,
        ),
    )
