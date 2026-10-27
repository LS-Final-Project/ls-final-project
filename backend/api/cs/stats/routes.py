"""C/S aggregates (KAN-111; permissions.md 4.1, 5.9).

A manager sees company-wide aggregates, which never open records outside their detail scope.
A C/S agent sees the same overview limited to tickets they own. Customer history lists
tickets, so it needs the full ticket scope.
"""

from datetime import datetime
from typing import Annotated, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.cs.stats import repository
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
    TicketCounts,
    Unconfirmed,
)
from backend.api.cs.tickets.access import sees_all_tickets
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.api.cs.tickets.schemas import WorkflowState
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.departments import TransferDepartment
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, SuccessResponse

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
STATES = get_args(WorkflowState)
Start = Annotated[datetime | None, Query(description="Inclusive, with a time zone.")]
End = Annotated[datetime | None, Query(description="Exclusive, with a time zone.")]
Customer = Annotated[str | None, Query(min_length=1, max_length=64)]


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
        improvements = None
        if scope == "ALL":
            groups, totals = await repository.improvements(conn, p)
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
        unconfirmed=UNCONFIRMED,
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
    async with connection(request.app.state.settings) as conn:
        rows = await repository.owners(conn, repository.params(start, end, dept))
    return respond(
        request, OwnerList(period=window, items=[OwnerLoad.model_validate(r) for r in rows])
    )


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
    if not 0 < len(customer_external_id) <= 64:
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
    data = CustomerHistory(
        customer_external_id=customer_external_id,
        period=window,
        received=counts["received"],
        by_state={state: states.get(state, 0) for state in STATES},
        by_product=products,
        tickets=[CustomerTicket.model_validate(t) for t in tickets],
        units=[CustomerUnit.model_validate(u) for u in units],
        improvement_requests=[CustomerRequest.model_validate(r) for r in requests],
        unconfirmed=UNCONFIRMED,
    )
    return respond(request, data)
