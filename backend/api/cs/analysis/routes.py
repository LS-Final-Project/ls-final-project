"""Analysis view and human corrections of a ticket (KAN-107, C/S proposal 4.1 gate ⑥).

Checks follow permissions.md 1.3: scope (404), role (403), state (409), version (409),
input (422).
"""

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request

from backend.api.cs.analysis import repository, service
from backend.api.cs.analysis.jobs import input_version, platform_jobs
from backend.api.cs.analysis.schemas import AnalysisJob, CorrectionRequest, TicketAnalysis
from backend.api.cs.tickets import repository as tickets
from backend.api.cs.tickets.access import require_ticket_write
from backend.api.cs.tickets.routes import PARTIAL, check_visible, not_found, respond
from backend.api.cs.workflow import repository as workflow
from backend.api.cs.workflow.mail import PlatformRejected, PlatformUnknown, PlatformUnreachable
from backend.api.cs.workflow.service import json_value, notify_cs
from backend.engine.cs.analysis import CORRECTABLE_FIELDS
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.departments import CS_DEPARTMENT
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind_path_ids
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    prefix="/api/v1/cs/tickets/{ticket_id}/analysis",
    tags=["analysis"],
    dependencies=[Depends(bind_path_ids)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
# A correction needs a ticket under review and not yet closed (same as return-subtype).
CORRECTABLE_STATES = ("IN_REVIEW", "TRANSFERRED", "WAITING_CUSTOMER", "ANSWERED")


@router.get(
    "",
    response_model=SuccessResponse[TicketAnalysis],
    operation_id="cs_get_ticket_analysis",
    openapi_extra=PARTIAL,
)
async def get_analysis(
    ticket_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[TicketAnalysis]:
    """AI runs, drafts, corrections, the effective values with their source, and the rules
    computed from the AI and the effective values. Same read scope as the ticket."""
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, ticket_id)
        data = await service.build(conn, ticket_id)
    return respond(request, data)


Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
# Platform refusals passed through as they are; anything else is the platform being unavailable.
PASSED_THROUGH = {409: ("CS_JOB_IN_PROGRESS", "IDEMPOTENCY_CONFLICT")}


@router.post(
    "",
    status_code=202,
    response_model=SuccessResponse[AnalysisJob],
    operation_id="cs_start_ticket_analysis",
    # Off until the operator connects the platform job (CS_ANALYSIS_ENABLED); 404 while off.
    openapi_extra={**PARTIAL, "x-activation": "CS_ANALYSIS_ENABLED"},
)
async def start_analysis(
    ticket_id: UUID,
    request: Request,
    idempotency_key: Key,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[AnalysisJob]:
    """A C/S agent asks for an AI analysis (classification, rules, evidence, three drafts).

    The platform runs it as a job and the graph stops before human review; results appear in
    `GET …/analysis`. One job per ticket at a time (409 `CS_JOB_IN_PROGRESS`); resending the
    same Idempotency-Key returns the earlier job. Not for a closed ticket.
    """
    client = platform_jobs(request)
    async with connection(request.app.state.settings) as conn:
        await check_visible(conn, principal, ticket_id)
        ticket = await tickets.get_ticket(conn, ticket_id)
        if ticket is None:
            raise not_found()
        require_ticket_write(principal)
        state = await workflow.get_workflow(conn, ticket_id)
        if state is not None and state["state"] == "CLOSED":
            raise ApplicationError(
                409, "CS_INVALID_TRANSITION", "The ticket state does not allow this action."
            )
    body = {
        "ticket_id": str(ticket_id),
        "input_version": input_version(ticket),
        "idempotency_key": idempotency_key,
        "requested_by": str(principal.user_id),
    }
    try:
        data = await client.create_cs_job(body)
    except PlatformRejected as error:
        if error.code in PASSED_THROUGH.get(error.status, ()):
            raise ApplicationError(
                error.status, error.code, "The platform refused the job."
            ) from None
        if error.status == 503 or error.code == "CS_PROCESSOR_NOT_CONNECTED":
            raise ApplicationError(
                503, "CS_ANALYSIS_UNAVAILABLE", "AI analysis is not connected yet."
            ) from None
        raise ApplicationError(
            503, "CS_PLATFORM_UNAVAILABLE", "The platform did not accept the job."
        ) from None
    except (PlatformUnreachable, PlatformUnknown):
        raise ApplicationError(
            503, "CS_PLATFORM_UNAVAILABLE", "The platform did not answer the job request."
        ) from None
    job = AnalysisJob.model_validate(
        {k: data.get(k) for k in ("job_id", "status", "status_url", "created")}
    )
    return respond(request, job)


@router.post(
    "/corrections",
    response_model=SuccessResponse[TicketAnalysis],
    operation_id="cs_correct_ticket_classification",
    openapi_extra=PARTIAL,
)
async def correct(
    ticket_id: UUID,
    payload: CorrectionRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[TicketAnalysis]:
    """A C/S agent corrects category, secondary category, urgency or provided information.

    Stored as its own `human_input` row; the AI row is not changed and a later AI run does not
    replace it. Only fields that differ from the current effective value are stored. The
    return subtype is confirmed through workflow/return-subtype, not here.
    """
    async with connection(request.app.state.settings) as conn:
        # 1.3 order: scope (404) before role (403), so an out-of-scope ticket looks missing.
        await check_visible(conn, principal, ticket_id)
        # The ticket lock orders this correction against AI runs stored by record_analysis
        # and against workflow actions, which take the same lock first.
        if await tickets.lock_ticket(conn, ticket_id) is None:
            raise not_found()
        require_ticket_write(principal)
        # RECEIVED (no workflow row yet) and CLOSED refuse, as return-subtype does.
        state = await workflow.lock_workflow(conn, ticket_id)
        if state is None or state["state"] not in CORRECTABLE_STATES:
            raise ApplicationError(
                409, "CS_INVALID_TRANSITION", "The ticket state does not allow this action."
            )
        current = await service.build(conn, ticket_id)
        # Two checks: `version` catches another person's correction, `analysis_id` a new AI
        # run the person has not seen (KAN-73 mail_triage uses the same read-version rule).
        if payload.version != current.version:
            raise ApplicationError(409, "CS_ANALYSIS_VERSION_CONFLICT", "The analysis has changed.")
        if payload.analysis_id != current.latest_valid_run_id:
            raise ApplicationError(
                409, "CS_ANALYSIS_STALE", "A newer analysis run exists. Read it first."
            )
        # Only fields present in the body count: an omitted field keeps its value, while an
        # explicit `secondary_category: null` clears it.
        given = payload.model_fields_set & set(CORRECTABLE_FIELDS)
        changed: dict[str, Any] = {}
        for name in CORRECTABLE_FIELDS:
            if name not in given:
                continue
            value = getattr(payload, name)
            # provided_info is a set: order and repeats are not a change.
            if name == "provided_info" and value is not None:
                value = sorted(set(value))
            if name in ("category_code", "urgency_flag", "provided_info") and value is None:
                raise ApplicationError(422, "CS_CORRECTION_INVALID", f"{name} cannot be cleared.")
            before = current.fields[name].effective_value
            if name == "provided_info" and before is not None:
                before = sorted(before)
            # A value equal to the current effective one is not stored, so the record shows
            # only what the person actually changed.
            if value != before:
                changed[name] = value
        if not changed:
            raise ApplicationError(
                422, "CS_CORRECTION_EMPTY", "The correction does not change any value."
            )
        # Same relation the classification output enforces (ClassificationOutput).
        category = changed.get("category_code", current.fields["category_code"].effective_value)
        secondary = changed.get(
            "secondary_category", current.fields["secondary_category"].effective_value
        )
        if secondary is not None and secondary == category:
            raise ApplicationError(
                422, "CS_CORRECTION_INVALID", "The secondary category must differ."
            )
        seq = current.version + 1
        await repository.insert_correction(
            conn,
            {
                "id": uuid4(),
                "ticket_id": ticket_id,
                "seq": seq,
                "based_on_analysis_id": payload.analysis_id,
                "fields": changed,
                "reason": payload.reason,
                "corrected_by": principal.user_id,
            },
        )
        # The audit keeps from/to of the fields and of what they moved: priority and the
        # deadlines in effect, so a later reader sees why a deadline changed.
        after = await service.build(conn, ticket_id)
        audit: dict[str, Any] = {
            name: {"from": current.fields[name].effective_value, "to": value}
            for name, value in changed.items()
        }
        before_rules, after_rules = current.rules, after.rules
        audit["priority"] = {
            "from": before_rules.effective.priority if before_rules.effective else None,
            "to": after_rules.effective.priority if after_rules.effective else None,
        }
        for name in ("first_response_due", "resolution_due"):
            audit[name] = {
                "from": json_value(getattr(before_rules, name).effective),
                "to": json_value(getattr(after_rules, name).effective),
            }
        audit["reason"] = {"to": payload.reason}
        audit["based_on_analysis_id"] = {"to": json_value(payload.analysis_id)}
        audit["provenance_kind"] = {"to": "human_input"}
        await workflow.insert_audit(
            conn, uuid4(), "ticket_analysis", ticket_id, "update", seq, principal.user_id, audit
        )
        if changed.get("urgency_flag") == "SAFETY":
            audience: list[dict[str, Any]] = (
                [{"user": state["owner_user_id"]}] if state["owner_user_id"] else []
            )
            audience.extend([{"role": "cs_agent"}, {"manager_dept": CS_DEPARTMENT}])
            await notify_cs(
                conn,
                kind="cs.safety",
                ticket_id=ticket_id,
                audience=audience,
                actor_id=principal.user_id,
                dedupe_key=f"cs.safety:{ticket_id}",
            )
    return respond(request, after)
