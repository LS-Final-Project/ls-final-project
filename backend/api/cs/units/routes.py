"""Unit check and same-unit history for a ticket (C/S proposal 4.3).

A unit can be linked only when its customer and product match the ticket. History moves
between tickets only through a confirmed link, so another unit's record is never copied.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request

from backend.api.cs.tickets import repository as tickets
from backend.api.cs.tickets.access import require_ticket_write
from backend.api.cs.tickets.routes import PARTIAL, not_found, require_full_scope, respond
from backend.api.cs.tickets.schemas import ResolutionCase
from backend.api.cs.units import repository
from backend.api.cs.units.schemas import (
    HistoryTicket,
    InstalledUnit,
    TicketUnit,
    TicketUnitList,
    UnitHistory,
    UnitLink,
    UnitLinkWrite,
)
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind_path_ids
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    prefix="/api/v1/cs/tickets/{ticket_id}",
    tags=["units"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse, "description": "No valid user context"},
        403: {"model": ErrorResponse, "description": "The role may not take this action"},
        404: {
            "model": ErrorResponse,
            "description": "Ticket or unit not found or outside the user's scope",
        },
        503: {"model": ErrorResponse},
    },
)

# Fields a link write may set. The route builds SQL column names only from this list.
LINK_FIELDS = ("link_status", "link_basis", "repeat_type", "decided_by", "decided_at")


def unit_not_found() -> ApplicationError:
    return ApplicationError(404, "CS_UNIT_NOT_FOUND", "The unit was not found.")


def matches(ticket: dict[str, Any], unit: dict[str, Any]) -> bool:
    return (
        unit["customer_external_id"] == ticket["customer_external_id"]
        and unit["product_code"] == ticket["product_code"]
    )


def link_model(row: dict[str, Any], prefix: str = "") -> UnitLink:
    return UnitLink(
        id=row[f"{prefix}id"],
        version=row[f"{prefix}version"],
        link_status=row["link_status"],
        link_basis=row["link_basis"],
        repeat_type=row["repeat_type"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
    )


@router.get(
    "/units",
    response_model=SuccessResponse[TicketUnitList],
    operation_id="cs_list_ticket_units",
    openapi_extra=PARTIAL,
)
async def list_ticket_units(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[TicketUnitList]:
    require_full_scope(principal)
    async with connection(request.app.state.settings) as conn:
        ticket = await tickets.get_ticket(conn, ticket_id)
        if ticket is None:
            raise not_found()
        rows = await repository.list_ticket_units(
            conn, ticket_id, ticket["customer_external_id"], ticket["product_code"]
        )
    items = [
        TicketUnit(
            unit=InstalledUnit.model_validate(row),
            matches_ticket=row["matches_ticket"],
            link=link_model(row, "link_") if row["link_id"] else None,
        )
        for row in rows
    ]
    return respond(request, TicketUnitList(items=items))


@router.put(
    "/units/{unit_id}",
    response_model=SuccessResponse[TicketUnit],
    operation_id="cs_set_ticket_unit_link",
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    openapi_extra=PARTIAL,
)
async def set_ticket_unit_link(
    ticket_id: UUID,
    unit_id: UUID,
    payload: UnitLinkWrite,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[TicketUnit]:
    """문의와 장비의 연결을 후보·확정·거절로 기록한다.

    고객·제품이 문의와 다른 장비는 후보나 확정으로 둘 수 없고 거절만 할 수 있다.
    한 문의에 확정 장비는 하나다. 문의 행을 잠가 정정·다른 연결과 순서대로 처리한다.

    Args:
        ticket_id: 대상 문의.
        unit_id: 연결할 장비.
        payload: 연결 상태·근거·반복 유형과 기존 연결의 판본.
        request: DB 연결을 제공하는 요청.
        principal: 검증된 사용자. cs_agent만 기록한다.

    Returns:
        SuccessResponse[TicketUnit]: 저장된 연결과 장비.
    """
    require_full_scope(principal)
    require_ticket_write(principal)
    async with connection(request.app.state.settings) as conn:
        # The ticket lock orders this write against ticket corrections and other links.
        ticket = await tickets.lock_ticket(conn, ticket_id)
        if ticket is None:
            raise not_found()
        unit = await repository.get_unit(conn, unit_id)
        if unit is None:
            raise unit_not_found()
        same = matches(ticket, unit)
        if payload.link_status != "REJECTED" and not same:
            raise ApplicationError(
                422,
                "CS_UNIT_MISMATCH",
                "The unit's customer or product differs from the ticket.",
            )
        current = await repository.lock_link(conn, ticket_id, unit_id)
        if (current is None) != (payload.version is None) or (
            current is not None and current["version"] != payload.version
        ):
            raise ApplicationError(409, "CS_UNIT_LINK_VERSION_CONFLICT", "The link has changed.")
        if payload.link_status == "CONFIRMED":
            other = await repository.confirmed_link(conn, ticket_id)
            if other is not None and other["unit_id"] != unit_id:
                raise ApplicationError(
                    409,
                    "CS_TICKET_UNIT_ALREADY_CONFIRMED",
                    "Another unit is already confirmed for this ticket.",
                )
        values: dict[str, Any] = {
            "link_status": payload.link_status,
            "link_basis": payload.link_basis,
            "repeat_type": payload.repeat_type,
            "decided_by": None,
            "decided_at": None,
        }
        if payload.link_status != "CANDIDATE":
            if current is not None and current["link_status"] == payload.link_status:
                # Same decision again: keep who made it and when.
                values["decided_by"] = current["decided_by"]
                values["decided_at"] = current["decided_at"]
            else:
                values["decided_by"] = principal.user_id
                values["decided_at"] = datetime.now(UTC)
        if current is None:
            link = {
                "id": uuid4(),
                "ticket_id": ticket_id,
                "unit_id": unit_id,
                "data_origin": ticket["data_origin"],
                "derivation_kind": "SYSTEM",
                **values,
            }
            await repository.insert_link(conn, link)
            link["version"] = 1
            changes = {name: {"to": json_value(values[name])} for name in LINK_FIELDS}
            await repository.insert_link_audit(
                conn, uuid4(), link["id"], "create", 1, principal.user_id, changes
            )
        else:
            changes = {
                name: {"from": json_value(current[name]), "to": json_value(values[name])}
                for name in LINK_FIELDS
                if current[name] != values[name]
            }
            link = {**current, **values}
            if changes:
                await repository.update_link(conn, current["id"], current["version"], values)
                link["version"] = current["version"] + 1
                await repository.insert_link_audit(
                    conn,
                    uuid4(),
                    current["id"],
                    "update",
                    link["version"],
                    principal.user_id,
                    changes,
                )
            else:
                # Nothing changed: keep the version and the earlier decision time.
                link = current
    return respond(
        request,
        TicketUnit(
            unit=InstalledUnit.model_validate(unit), matches_ticket=same, link=link_model(link)
        ),
    )


def json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


@router.get(
    "/unit-history",
    response_model=SuccessResponse[UnitHistory],
    operation_id="cs_get_ticket_unit_history",
    openapi_extra=PARTIAL,
)
async def get_ticket_unit_history(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[UnitHistory]:
    # History lists earlier tickets that may not be transferred to the user's department, so
    # department users (KAN-108 scope) get 404 here until the history is filtered by scope.
    require_full_scope(principal)
    async with connection(request.app.state.settings) as conn:
        ticket = await tickets.get_ticket(conn, ticket_id)
        if ticket is None:
            raise not_found()
        link = await repository.confirmed_link(conn, ticket_id)
        if link is None:
            return respond(request, UnitHistory(status="NO_CONFIRMED_UNIT", unit=None, items=[]))
        unit = await repository.get_unit(conn, link["unit_id"])
        if unit is None:
            return respond(request, UnitHistory(status="NO_CONFIRMED_UNIT", unit=None, items=[]))
        before = ticket["received_at"]
        rows = await repository.unit_history(conn, ticket_id, link["unit_id"], before)
        cases = await repository.known_cases(conn, [row["id"] for row in rows], before)
    items = [
        HistoryTicket.model_validate(
            {
                **row,
                "resolution_case": (
                    ResolutionCase.model_validate(cases[row["id"]]) if row["id"] in cases else None
                ),
            }
        )
        for row in rows
    ]
    return respond(
        request,
        UnitHistory(status="CONFIRMED", unit=InstalledUnit.model_validate(unit), items=items),
    )
