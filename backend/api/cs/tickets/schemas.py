"""C/S intake records and resolution cases (C/S proposal 6.1).

Classification, rule results and workflow status belong to later tickets.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.shared.models import CUSTOMER_ID

DataOrigin = Literal["synthetic", "demo", "real"]
DerivationKind = Literal["PROVIDED", "DERIVED", "SYSTEM", "ASSUMED"]
Channel = Literal["EMAIL", "PARTNER_PORTAL", "WEB_FORM", "PHONE", "CHAT"]
# Live workflow state (KAN-108). The provided `status` is source history, not this value.
WorkflowState = Literal[
    "RECEIVED", "IN_REVIEW", "WAITING_CUSTOMER", "ANSWERED", "TRANSFERRED", "CLOSED"
]
Code = Annotated[str, Field(min_length=1, max_length=64)]
CustomerId = Annotated[str, Field(pattern=f"^{CUSTOMER_ID}$")]
Subject = Annotated[str, Field(min_length=1, max_length=500)]


class TicketSummary(BaseModel):
    id: UUID
    # Chat inquiries have no subject in the provided data.
    subject: str | None
    data_origin: DataOrigin
    # Manual input may have no source ID; the other fields are NOT NULL in cs.tickets.
    external_id: str | None
    derivation_kind: DerivationKind
    received_at: datetime
    channel: Channel
    product_code: str
    workflow_state: WorkflowState


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
    dept_result_id: UUID | None = Field(
        default=None,
        description="Accepted department result the case was written from (KAN-108). "
        "Null for provided cases.",
    )


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
    workflow_state: WorkflowState
    resolution_case: ResolutionCase | None


class TicketCreate(BaseModel):
    """Manual intake. Source IDs, origin and timestamps are set by the server."""

    model_config = ConfigDict(extra="forbid")

    channel: Channel
    body_language: str = Field(min_length=2, max_length=16)
    customer_external_id: CustomerId
    product_code: Code
    subject: Subject | None = None
    body: str = Field(min_length=1, max_length=50_000)
    fw_version_reported: Code | None = None
    error_code_reported: Code | None = None


# The customer's original text, channel and receipt time are never rewritten.
EDITABLE_FIELDS = (
    "customer_external_id",
    "product_code",
    "subject",
    "fw_version_reported",
    "error_code_reported",
)


class TicketUpdate(BaseModel):
    """Correction of identifying fields. `version` must match the stored version."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    customer_external_id: CustomerId | None = None
    product_code: Code | None = None
    subject: Subject | None = None
    fw_version_reported: Code | None = None
    error_code_reported: Code | None = None

    @model_validator(mode="after")
    def check_fields(self) -> "TicketUpdate":
        sent = self.model_fields_set & set(EDITABLE_FIELDS)
        if not sent:
            raise ValueError("At least one editable field is required.")
        # Omitted means unchanged; these two columns are NOT NULL and cannot be cleared.
        for name in ("customer_external_id", "product_code"):
            if name in sent and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self

    def changed_values(self) -> dict[str, Any]:
        return {
            name: getattr(self, name) for name in EDITABLE_FIELDS if name in self.model_fields_set
        }


class AuditEvent(BaseModel):
    id: UUID
    action: Literal["create", "update"]
    version: int
    actor_user_id: UUID | None
    changes: dict[str, dict[str, Any]]
    occurred_at: datetime


class AuditList(BaseModel):
    items: list[AuditEvent]
