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


class CustomerLoad(BaseModel):
    customer_external_id: str
    received: int
    open: int


class ImprovementCounts(BaseModel):
    by_status: dict[str, int]
    by_type: dict[Literal["FAULT", "DOCUMENT"], int]
    open: int = Field(description="Not completed and not ended by rejection review.")
    receiver_unconfirmed: int


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


class OwnerLoad(BaseModel):
    owner_user_id: UUID | None = Field(description="Null groups tickets without an owner.")
    received: int
    closed: int
    open: int


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
    workflow_state: WorkflowState
    data_origin: str


class CustomerUnit(BaseModel):
    id: UUID
    serial_no: str
    product_code: str
    unit_status: str
    derivation_kind: str
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
