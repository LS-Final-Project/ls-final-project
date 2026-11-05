"""Fault catalog for choosing a resolution case fault ID (C/S proposal ⑧).

The catalog is provided reference data, copied unmodified into backend/engine/cs/policy and
pinned by hash. It is the same for every user, so any C/S role may read it.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from backend.api.cs.faults.schemas import FaultEntry, FaultList
from backend.api.cs.tickets.access import require_ticket_list
from backend.api.cs.tickets.routes import PARTIAL, respond
from backend.engine.cs.rules import fault_catalog
from backend.shared.access import Principal, current_principal
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    tags=["faults"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
SOURCE = "ref/fault_catalog.csv"


@router.get(
    "/api/v1/cs/faults",
    response_model=SuccessResponse[FaultList],
    operation_id="cs_list_faults",
    openapi_extra=PARTIAL,
)
async def list_faults(
    request: Request,
    product_code: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[Any]:
    """Faults in the catalog, all of them or those that apply to one product.

    A product with no catalog entry gives an empty list, not an error: the person then writes
    the resolution case without a catalog hint.
    """
    require_ticket_list(principal)
    items = [
        FaultEntry(
            fault_id=fault.fault_id,
            family=fault.family,
            applies_to=list(fault.applies_to),
            error_code=fault.error_code,
            symptom=fault.symptom,
            root_cause=fault.root_cause,
            diagnosis=fault.diagnosis,
            action=fault.action,
            severity_hint=fault.severity_hint,
            needs_onsite=fault.needs_onsite,
            needs_rma=fault.needs_rma,
            replacement_part=fault.replacement_part,
            fw_fix_version=fault.fw_fix_version,
            typical_resolution_hours=fault.typical_resolution_hours,
        )
        for fault in fault_catalog()
        if product_code is None or product_code in fault.applies_to
    ]
    return respond(request, FaultList(items=items, source=SOURCE))
