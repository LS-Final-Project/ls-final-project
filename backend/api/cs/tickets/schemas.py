"""C/S intake records and resolution cases (C/S proposal 6.1).

Classification, rule results and workflow status belong to later tickets.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

DataOrigin = Literal["synthetic", "demo", "real"]
DerivationKind = Literal["PROVIDED", "DERIVED", "SYSTEM", "ASSUMED"]
Channel = Literal["EMAIL", "PARTNER_PORTAL", "WEB_FORM", "PHONE", "CHAT"]


class TicketSummary(BaseModel):
    id: UUID
    # Chat inquiries have no subject in the provided data.
    subject: str | None
    data_origin: DataOrigin
    external_id: str | None = None
    derivation_kind: DerivationKind = "PROVIDED"
    received_at: datetime | None = None
    channel: Channel | None = None
    product_code: str | None = None


class TicketList(BaseModel):
    items: list[TicketSummary]
    next_cursor: str | None


class ResolutionCase(BaseModel):
    id: UUID
    external_id: str | None
    data_origin: DataOrigin
    derivation_kind: DerivationKind
    resolved_at: datetime | None
    known_at: datetime = Field(description="Earliest time this case may be used as evidence.")
    product_code: str
    fault_id: str
    fw_version_at_issue: str | None
    symptom: str
    root_cause: str
    root_cause_type: str
    action_taken: str
    onsite_visit: bool
    rma_no: str | None
    fw_fixed_version: str | None
    resolved_by_dept: str
    recurrence_prevention: str | None


class TicketDetail(BaseModel):
    id: UUID
    external_id: str | None
    version: int
    data_origin: DataOrigin
    derivation_kind: DerivationKind
    received_at: datetime
    known_at: datetime
    channel: Channel
    body_language: str
    customer_external_id: str
    product_code: str
    subject: str | None
    body: str
    fw_version_reported: str | None
    error_code_reported: str | None
    resolution_case: ResolutionCase | None
