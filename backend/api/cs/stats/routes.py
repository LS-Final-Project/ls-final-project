"""C/S aggregates (KAN-111; permissions.md 4.1, 5.9).

A manager sees company-wide aggregates, which never open records outside their detail scope.
A C/S agent sees the same overview limited to tickets they own. Customer history lists
tickets, so it needs the full ticket scope.
"""

import re
from datetime import datetime
from typing import Annotated, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.cs.improvements import repository as improvement_repository
from backend.api.cs.improvements.followup_routes import signal_for
from backend.api.cs.stats import repository, sla
from backend.api.cs.stats.schemas import (
    CustomerHistory,
    CustomerLoad,
    CustomerRequest,
    CustomerTicket,
    CustomerUnit,
    DataRange,
    DepartmentCount,
    ImprovementCounts,
    OpenAge,
    Overview,
    OwnerList,
    OwnerLoad,
    Period,
    RecurrenceCount,
    TicketCounts,
    Unconfirmed,
)
from backend.api.cs.tickets.access import sees_all_tickets, transfer_departments
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.api.cs.tickets.schemas import WorkflowState
from backend.api.cs.translation import repository as translations
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.departments import TransferDepartment
from backend.shared.errors import ApplicationError
from backend.shared.models import CUSTOMER_ID, ErrorResponse, SuccessResponse

router = APIRouter(
    prefix="/api/v1/cs/stats",
    tags=["stats"],
    responses={
        401: {"model": ErrorResponse, "description": "No valid user context"},
        403: {"model": ErrorResponse, "description": "The role may not see these aggregates"},
        422: {"model": ErrorResponse, "description": "Invalid period"},
        503: {"model": ErrorResponse},
    },
)

# Neither is stored yet: SLA due times and rule results are saved by the ticket job (KAN-107).
UNCONFIRMED: list[Unconfirmed] = ["SLA_DUE", "RULE_ASSIGNED_DEPT"]
# The overview computes SLA figures on read (KAN-233), so only the rule department is left.
OVERVIEW_UNCONFIRMED: list[Unconfirmed] = ["RULE_ASSIGNED_DEPT"]
STATES = get_args(WorkflowState)
Start = Annotated[datetime | None, Query(description="Inclusive, with a time zone.")]
End = Annotated[datetime | None, Query(description="Exclusive, with a time zone.")]
Customer = Annotated[str | None, Query(pattern=f"^{CUSTOMER_ID}$")]


def period(start: datetime | None, end: datetime | None) -> Period:
    for value in (start, end):
        if value is not None and value.utcoffset() is None:
            raise ApplicationError(422, "CS_INVALID_PERIOD", "Times need a time zone.")
    if start is not None and end is not None and start >= end:
        raise ApplicationError(422, "CS_INVALID_PERIOD", "The period end must follow its start.")
    return Period(start=start, end=end)


@router.get(
    "/overview",
    response_model=SuccessResponse[Overview],
    operation_id="cs_stats_overview",
    openapi_extra=PARTIAL,
)
async def overview(
    request: Request,
    start: Start = None,
    end: End = None,
    dept: TransferDepartment | None = None,
    customer: Customer = None,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Overview]:
    """Throughput, open age, transfers, customer load and improvement progress."""
    if principal.has("manager"):
        scope, owner = "ALL", None
    elif principal.has("cs_agent"):
        scope, owner = "OWN", principal.user_id
    else:
        raise HTTPException(403)
    window = period(start, end)
    p = repository.params(start, end, dept, customer, owner)
    async with connection(request.app.state.settings) as conn:
        counts = await repository.ticket_counts(conn, p)
        states, origins = await repository.by_state_and_origin(conn, p)
        ages = await repository.open_age(conn, p)
        departments = await repository.departments(conn, p)
        customers = await repository.top_customers(conn, p)
        now = await improvement_repository.database_now(conn)
        sla_rows = await repository.sla_tickets(conn, p)
        improvements = None
        if scope == "ALL":
            groups, totals = await repository.improvements(conn, p)
            faults, documents = await repository.completed_fault_requests(conn, p)
            recurrence = 0
            for row in faults:
                signal, _ = await signal_for(conn, row, now)
                recurrence += signal.decision == "RECURRENCE"
            by_status: dict[str, int] = {}
            by_type = {"FAULT": 0, "DOCUMENT": 0}
            for row in groups:
                by_status[row["status"]] = by_status.get(row["status"], 0) + row["n"]
                by_type[row["request_type"]] += row["n"]
            improvements = ImprovementCounts(
                by_status=by_status,
                by_type=by_type,  # type: ignore[arg-type]
                open=totals["open"],
                receiver_unconfirmed=totals["receiver_unconfirmed"],
                completed_in_period=totals["completed_in_period"],
                info_requested=totals["info_requested"],
                rejected=totals["rejected"],
                reply_overdue=totals["reply_overdue"],
                recurrence_after_completion=RecurrenceCount(
                    checked=len(faults), recurrence=recurrence, not_applicable=documents
                ),
            )
    data = Overview(
        scope=scope,  # type: ignore[arg-type]
        period=window,
        dept_code=dept,
        customer_external_id=customer,
        data_range=DataRange.model_validate(counts),
        tickets=TicketCounts(
            received=counts["received"],
            closed=counts["closed"],
            open=counts["open"],
            by_state={state: states.get(state, 0) for state in STATES},
            by_origin={o: origins.get(o, 0) for o in ("synthetic", "demo", "real")},  # type: ignore[misc]
            open_age=OpenAge.model_validate(ages),
        ),
        departments=[DepartmentCount.model_validate(d) for d in departments],
        top_customers=[CustomerLoad.model_validate(c) for c in customers],
        improvements=improvements,
        unconfirmed=OVERVIEW_UNCONFIRMED,
        sla=sla.summary(
            sla_rows,
            start=start,
            end=end,
            now=now,
            sees_all=sees_all_tickets(principal),
            departments=transfer_departments(principal),
        ),
    )
    return respond(request, data)


@router.get(
    "/owners",
    response_model=SuccessResponse[OwnerList],
    operation_id="cs_stats_owners",
    openapi_extra=PARTIAL,
)
async def owners(
    request: Request,
    start: Start = None,
    end: End = None,
    dept: TransferDepartment | None = None,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[OwnerList]:
    """Load per C/S owner. Managers only; agents see their own numbers in the overview."""
    if not principal.has("manager"):
        raise HTTPException(403)
    window = period(start, end)
    p = repository.params(start, end, dept)
    async with connection(request.app.state.settings) as conn:
        rows = await repository.owners(conn, p)
        now = await improvement_repository.database_now(conn)
        deadlines = sla.by_owner(await repository.sla_tickets(conn, p), now)
        requests = await repository.owner_requests(conn, dept)
    empty = {"due_today": 0, "overdue": 0, "sla_unknown": 0}
    items = [
        OwnerLoad.model_validate(
            {
                **r,
                **deadlines.get(r["owner_user_id"], empty),
                "improvements_open": requests.get(r["owner_user_id"], 0),
                "by_state": {state: r[f"state_{state.lower()}"] for state in STATES},
            }
        )
        for r in rows
    ]
    return respond(request, OwnerList(period=window, items=items))


@router.get(
    "/customers/{customer_external_id}",
    response_model=SuccessResponse[CustomerHistory],
    operation_id="cs_stats_customer_history",
    openapi_extra=PARTIAL,
)
async def customer_history(
    customer_external_id: str,
    request: Request,
    start: Start = None,
    end: End = None,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[CustomerHistory]:
    """A customer's tickets, units and improvement requests. Lists records, so it needs the
    full ticket scope (cs_agent, C/S department manager)."""
    if not sees_all_tickets(principal):
        raise HTTPException(403)
    if not re.fullmatch(CUSTOMER_ID, customer_external_id):
        raise ApplicationError(422, "CS_INVALID_CUSTOMER", "The customer ID is invalid.")
    window = period(start, end)
    p = repository.params(start, end, customer=customer_external_id)
    async with connection(request.app.state.settings) as conn:
        counts = await repository.ticket_counts(conn, p)
        states, _ = await repository.by_state_and_origin(conn, p)
        products = await repository.by_product(conn, p)
        tickets = await repository.customer_tickets(conn, p)
        units = await repository.customer_units(conn, customer_external_id)
        requests = await repository.customer_requests(conn, customer_external_id)
        korean = await translations.korean_subjects(conn, [t["id"] for t in tickets])
    data = CustomerHistory(
        customer_external_id=customer_external_id,
        period=window,
        received=counts["received"],
        by_state={state: states.get(state, 0) for state in STATES},
        by_product=products,
        tickets=[
            CustomerTicket.model_validate({**t, "subject_ko": korean.get(t["id"])}) for t in tickets
        ],
        units=[CustomerUnit.model_validate(u) for u in units],
        improvement_requests=[CustomerRequest.model_validate(r) for r in requests],
        unconfirmed=UNCONFIRMED,
    )
    return respond(request, data)
