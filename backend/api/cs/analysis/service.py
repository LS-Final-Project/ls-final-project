"""Builds the analysis view and stores results (KAN-107).

`record_analysis` and `record_draft` are the storage side of the processing job: the job
runner (KAN-69, cs_runner.TicketSource) calls them once connected; tests and demos call them
with synthetic rows. They lock the ticket row so run numbers stay in order.
"""

from typing import Any
from uuid import UUID

from backend.api.cs.analysis import repository
from backend.api.cs.analysis.schemas import (
    AnalysisRun,
    Correction,
    DeadlineView,
    DraftRun,
    FieldView,
    Rules,
    RuleView,
    TicketAnalysis,
)
from backend.api.cs.tickets import repository as tickets
from backend.api.cs.workflow import repository as workflow
from backend.engine.cs import analysis
from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.drafting import DraftOutput
from backend.engine.cs.rules import RuleDecision
from backend.shared.errors import ApplicationError

Row = dict[str, Any]


def ticket_not_found() -> ApplicationError:
    return ApplicationError(404, "CS_TICKET_NOT_FOUND", "The ticket was not found.")


async def record_analysis(conn: Any, ticket_id: UUID, values: Row, *, lock: bool = True) -> Row:
    """Stores one classification run (`outcome`, `classification` when VALID, input version and
    hash, model and prompt version, error code, optional `job_id`). Returns the stored row.

    A VALID classification is checked against the engine's output schema again, so a caller
    cannot store fields the validator would have refused. `lock=False` is for the worker
    (worker_source.py), which may not lock ticket rows; UNIQUE(ticket_id, run_seq) still stops
    two runs from taking the same number.
    """
    if values.get("classification") is not None:
        ClassificationOutput.model_validate(values["classification"], strict=False)
    if lock and await tickets.lock_ticket(conn, ticket_id) is None:
        raise ticket_not_found()
    return await repository.insert_run(conn, ticket_id, values)


async def record_draft(conn: Any, ticket_id: UUID, values: Row, *, lock: bool = True) -> Row:
    """Stores one draft run (three options when VALID). Returns the stored row.

    The draft is checked against the engine's output schema (three options, one per label).
    `lock` is as in `record_analysis`.
    """
    if values.get("draft") is not None:
        DraftOutput.model_validate(values["draft"])
    if lock and await tickets.lock_ticket(conn, ticket_id) is None:
        raise ticket_not_found()
    # The FK only says the run exists; it must also be a run of this ticket.
    if values.get("analysis_id") is not None and not await repository.run_belongs(
        conn, ticket_id, values["analysis_id"]
    ):
        raise ValueError("CS_DRAFT_ANALYSIS_MISMATCH")
    return await repository.insert_draft(conn, ticket_id, values)


def rule_view(decision: RuleDecision | None) -> RuleView | None:
    if decision is None:
        return None
    return RuleView(
        policy_version=decision.policy_version,
        priority=decision.priority.priority,
        priority_rule_id=decision.priority.rule_id,
        product_family=decision.product_family,
        dept=decision.routing.dept,
        dept_rule_id=decision.routing.rule_id,
        escalation_dept=decision.routing.escalation_dept,
        escalation_condition=decision.routing.escalation_condition,
        review_reasons=list(decision.routing.review_reasons),
        sla_id=decision.sla.sla_id,
        first_response_hours=decision.sla.first_response_hours,
        resolution_hours=decision.sla.resolution_hours,
        first_response_due=decision.first_response_due,
        resolution_due=decision.resolution_due,
    )


def latest_valid(rows: list[Row]) -> Row | None:
    return next((r for r in reversed(rows) if r["outcome"] == "VALID"), None)


async def build(conn: Any, ticket_id: UUID) -> TicketAnalysis:
    """The analysis view of one ticket. Scope checks are the caller's."""
    ticket = await tickets.get_ticket(conn, ticket_id)
    if ticket is None:
        raise ticket_not_found()
    runs = await repository.list_runs(conn, ticket_id)
    drafts = await repository.list_drafts(conn, ticket_id)
    corrections = await repository.list_corrections(conn, ticket_id)
    state = await workflow.get_workflow(conn, ticket_id)
    tier = await repository.customer_tier(conn, ticket_id)
    # AI values come from the latest VALID run only. A failed later run does not hide them.
    valid = latest_valid(runs)
    ai = valid["classification"] if valid else None
    fields = analysis.effective_fields(ai, [c["fields"] for c in corrections])
    common = {
        "product_code": ticket["product_code"],
        "customer_tier": tier,
        "return_subtype": ai.get("return_subtype") if ai else None,
        # A person's inspection result (KAN-193) always beats the stated return reason.
        "confirmed_subtype": state["return_subtype_confirmed"] if state else None,
        "received_at": ticket["received_at"],
    }
    ai_rules, ai_error = (
        analysis.decide_or_none(ai, **common) if ai else (None, "CS_ANALYSIS_MISSING")
    )
    effective_values = {name: f.effective_value for name, f in fields.items()}
    # Without a correction both sides are the AI rules, and no corrected deadline exists.
    corrected = any(f.corrected for f in fields.values())
    if corrected:
        effective_rules, effective_error = analysis.decide_or_none(effective_values, **common)
    else:
        effective_rules, effective_error = ai_rules, ai_error
    # Each deadline separately: the earlier of AI and corrected (2026-10-29 decision).
    first = analysis.merge_deadline(
        ai_rules.first_response_due if ai_rules else None,
        effective_rules.first_response_due if corrected and effective_rules else None,
    )
    resolution = analysis.merge_deadline(
        ai_rules.resolution_due if ai_rules else None,
        effective_rules.resolution_due if corrected and effective_rules else None,
    )
    valid_draft = latest_valid(drafts)
    return TicketAnalysis(
        ticket_id=ticket_id,
        version=len(corrections),
        latest_run=AnalysisRun.model_validate(runs[-1]) if runs else None,
        latest_valid_run_id=valid["id"] if valid else None,
        runs=[AnalysisRun.model_validate(r) for r in runs],
        drafts=[DraftRun.model_validate(d) for d in drafts],
        latest_valid_draft_id=valid_draft["id"] if valid_draft else None,
        corrections=[Correction.model_validate(c) for c in corrections],
        fields={name: FieldView(**vars(value)) for name, value in fields.items()},
        return_subtype_stated=common["return_subtype"],
        rules=Rules(
            ai=rule_view(ai_rules),
            ai_error=ai_error,
            effective=rule_view(effective_rules),
            effective_error=effective_error,
            first_response_due=DeadlineView(**vars(first)),
            resolution_due=DeadlineView(**vars(resolution)),
        ),
    )
