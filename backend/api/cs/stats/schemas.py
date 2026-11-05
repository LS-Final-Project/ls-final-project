"""Aggregates over stored C/S records only (KAN-111).

Counts come from what the database holds. A metric whose source is not stored yet is listed
in `unconfirmed` instead of being estimated, and nothing before the first stored record is
reconstructed.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.api.cs.tickets.schemas import WorkflowState

Unconfirmed = Literal["SLA_DUE", "RULE_ASSIGNED_DEPT"]


class Period(BaseModel):
    start: datetime | None = Field(description="Inclusive. Null means from the first record.")
    end: datetime | None = Field(description="Exclusive. Null means up to now.")


class DataRange(BaseModel):
    first_received_at: datetime | None
    last_received_at: datetime | None


class OpenAge(BaseModel):
    """Open tickets by time since receipt, measured at the end of the period (or now)."""

    under_1_day: int
    from_1_to_3_days: int
    from_3_to_7_days: int
    over_7_days: int


class TicketCounts(BaseModel):
    received: int = Field(description="Received in the period.")
    closed: int = Field(description="Closed in the period, whenever received.")
    open: int = Field(description="Received in the period and not closed now.")
    by_state: dict[WorkflowState, int]
    by_origin: dict[Literal["synthetic", "demo", "real"], int]
    open_age: OpenAge


class DepartmentCount(BaseModel):
    dept_code: str
    transferred: int
    results_submitted: int
    results_accepted: int
    returned_or_recalled: int
    waiting: int | None = Field(
        default=None, description="Of these transfers, still OPEN (no result, not ended) now."
    )
    waiting_over_7_days: int | None = Field(
        default=None, description="Still OPEN now and transferred more than 7 days ago."
    )
    avg_reply_days: float | None = Field(
        default=None,
        description="Mean days from transfer to the department's submitted result, over the "
        "transfers with a result. Null without any result.",
    )


class CustomerLoad(BaseModel):
    customer_external_id: str
    received: int
    open: int


class RecurrenceCount(BaseModel):
    """KAN-117 recurrence signal of requests completed in the period (by completed_at),
    computed on read (nothing stored)."""

    checked: int = Field(description="Completed fault requests whose signal was computed.")
    recurrence: int = Field(description="Of those, requests whose signal is RECURRENCE.")
    not_applicable: int = Field(
        description="Completed document requests: the signal applies to fault requests only."
    )
    limited: bool = Field(
        default=False,
        description="True when more completed fault requests exist than were checked "
        "(only the most recent 200 by completion time are checked).",
    )


class ImprovementCounts(BaseModel):
    by_status: dict[str, int]
    by_type: dict[Literal["FAULT", "DOCUMENT"], int]
    open: int = Field(description="Not completed and not ended by rejection review.")
    receiver_unconfirmed: int
    completed_in_period: int | None = Field(
        default=None,
        description="COMPLETED now with completed_at in the period, whenever created.",
    )
    info_requested: int | None = Field(
        default=None,
        description="Open requests with a current-round department in INFO_REQUESTED.",
    )
    rejected: int | None = Field(
        default=None,
        description="Requests with a current-round department in REJECTED (including ended "
        "by rejection).",
    )
    reply_overdue: int | None = Field(
        default=None,
        description="Open requests with a current-round department past reply_due_at that "
        "has not replied in that round.",
    )
    recurrence_after_completion: RecurrenceCount | None = Field(
        default=None, description="Null when the signal could not be computed."
    )


class SlaCompliance(BaseModel):
    met: int
    breached: int
    pending: int = Field(
        default=0, description="First response only: no reply yet and not due yet."
    )
    unrecorded: int = Field(
        default=0,
        description="First response only: closed without any recorded customer message.",
    )


class SlaPriority(BaseModel):
    priority: Literal["P1", "P2", "P3", "P4"]
    met: int = Field(description="Closed in the period within the resolution deadline.")
    breached: int = Field(description="Closed in the period after the resolution deadline.")
    at_risk: int = Field(description="Open, 75% or more of the resolution target elapsed.")
    overdue: int = Field(description="Open, past the resolution deadline.")


class OverdueTicket(BaseModel):
    ticket_id: UUID
    external_id: str | None
    subject: str | None = Field(
        description="Null when the user may not open this ticket (aggregate-only scope)."
    )
    priority: Literal["P1", "P2", "P3", "P4"]
    resolution_due: datetime
    overdue_hours: float
    owner_user_id: UUID | None


class SlaSummary(BaseModel):
    """Computed on read from rule inputs, the same deadlines as the analysis view (KAN-233)."""

    measured_at: datetime = Field(description="Database time open tickets are measured at.")
    first_response: SlaCompliance = Field(
        description="Tickets received in the period. First response is the first customer "
        "message (answer or information request) the mail platform accepted and the workflow "
        "recorded."
    )
    resolution: SlaCompliance = Field(
        description="Tickets closed in the period, closing time against the resolution deadline."
    )
    at_risk: int = Field(description="Open, received in the period, 75% or more elapsed.")
    overdue: int = Field(description="Open, received in the period, past the resolution deadline.")
    unknown: int = Field(
        description="Received or closed in the period but without an SLA: no classification "
        "or a value outside the policy. Not in any other count."
    )
    by_priority: list[SlaPriority]
    overdue_tickets: list[OverdueTicket] = Field(description="Most overdue first, at most 10.")


class Overview(BaseModel):
    scope: Literal["ALL", "OWN"] = Field(
        description="ALL for a manager; OWN for a C/S agent, limited to tickets they own."
    )
    period: Period
    dept_code: str | None
    customer_external_id: str | None
    data_range: DataRange
    tickets: TicketCounts
    departments: list[DepartmentCount] = Field(description="By transfer department.")
    top_customers: list[CustomerLoad] = Field(description="Most received first, at most 10.")
    improvements: ImprovementCounts | None = Field(
        description="All requests created in the period. Null for the OWN scope."
    )
    unconfirmed: list[Unconfirmed] = Field(
        description="Metrics shown as unknown because their source is not stored yet."
    )
    sla: SlaSummary | None = Field(default=None, description="SLA figures (KAN-233).")


class OwnerLoad(BaseModel):
    owner_user_id: UUID | None = Field(description="Null groups tickets without an owner.")
    received: int = Field(description="Received in the period.")
    closed: int = Field(description="Closed in the period.")
    open: int = Field(
        description="Not closed now, whenever received: the owner's current load. Unlike "
        "the overview's open count, the period does not limit it."
    )
    by_state: dict[WorkflowState, int] = Field(
        description="Current state of the owner's tickets, whenever received (the period does "
        "not limit it). Every state is present."
    )
    due_today: int | None = Field(
        default=None,
        description="Open now with a resolution deadline later today (Asia/Seoul), not passed.",
    )
    overdue: int | None = Field(default=None, description="Open now, past the resolution deadline.")
    sla_unknown: int | None = Field(default=None, description="Open now without an SLA.")
    avg_handle_days: float | None = Field(
        default=None,
        description="Mean days from receipt to closing over tickets closed in the period.",
    )
    improvements_open: int | None = Field(
        default=None, description="Open improvement requests whose cs_owner is this user, now."
    )


class OwnerList(BaseModel):
    period: Period
    items: list[OwnerLoad]


class CustomerTicket(BaseModel):
    id: UUID
    external_id: str | None
    received_at: datetime
    channel: str
    product_code: str
    subject: str | None
    subject_ko: str | None = Field(
        default=None, description="Korean subject from the latest valid translation (KAN-213)."
    )
    workflow_state: WorkflowState
    data_origin: str


class CustomerUnit(BaseModel):
    id: UUID
    serial_no: str
    product_code: str
    unit_status: str
    derivation_kind: Literal["PROVIDED", "DERIVED", "SYSTEM", "ASSUMED"]
    confirmed_tickets: int


class CustomerRequest(BaseModel):
    id: UUID
    request_type: str
    product_code: str
    fault_id: str | None
    status: str
    evidence_tickets: int


class CustomerHistory(BaseModel):
    customer_external_id: str
    period: Period
    received: int
    by_state: dict[WorkflowState, int]
    by_product: dict[str, int]
    tickets: list[CustomerTicket] = Field(description="Newest first, at most 200.")
    units: list[CustomerUnit]
    improvement_requests: list[CustomerRequest]
    unconfirmed: list[Unconfirmed]
