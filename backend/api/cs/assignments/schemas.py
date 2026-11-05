"""Assignment API shapes (contracts/cs/assignment.md)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.engine.cs.assignment import AssignedReason, Reason, RequestReason

Trigger = Literal["INTAKE", "BATCH"]
ApplyOutcome = Literal[
    "ASSIGNED", "ALREADY_ASSIGNED", "NOT_RECEIVED", "NOT_FOUND", "PREVIEW_CHANGED", "NO_MATCH"
]


class AssignmentRecord(BaseModel):
    """A stored rule assignment. Rule results, not AI output."""

    id: UUID
    user_id: UUID
    rule_version: str
    reason_code: AssignedReason
    matched_language: str | None
    matched_family: str | None
    candidate_count: int = Field(description="Agents in the tier that decided.")
    chosen_open_count: int | None = Field(
        description="Open tickets of the chosen agent when decided, before this one."
    )
    trigger: Trigger
    decided_by: UUID | None = Field(description="Null for the system at intake.")
    created_at: datetime


class AgentSkill(BaseModel):
    user_id: UUID
    languages: list[str]
    families: list[str]
    active: bool
    open_count: int = Field(description="Owned tickets that are not closed, now.")
    open_request_count: int = Field(
        default=0, description="Owned improvement requests that are open (stats definition)."
    )
    data_origin: str
    derivation_kind: str = Field(description="ASSUMED: the employee table is assumed data.")


class AgentList(BaseModel):
    rule_version: str
    request_rule_version: str
    items: list[AgentSkill] = Field(description="By user_id.")


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=50, ge=1, le=200, description="Oldest unassigned tickets first.")


class Proposal(BaseModel):
    ticket_id: UUID
    external_id: str | None
    subject: str | None
    product_code: str
    family: str | None = Field(description="product_master family; null for an unknown product.")
    body_language: str
    received_at: datetime
    priority: str | None = Field(description="Rule priority from the latest analysis, if any.")
    user_id: UUID | None
    reason_code: Reason
    matched_language: str | None
    matched_family: str | None
    candidate_count: int
    chosen_open_count: int | None


class Preview(BaseModel):
    rule_version: str
    items: list[Proposal] = Field(description="In decision order: priority, then received.")


class ApplyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID
    user_id: UUID | None = Field(description="The previewed agent; null for NO_MATCH.")


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_version: str = Field(min_length=1, max_length=64)
    items: Annotated[list[ApplyItem], Field(min_length=1, max_length=200)]


class ApplyResult(BaseModel):
    ticket_id: UUID
    outcome: ApplyOutcome
    user_id: UUID | None = Field(description="Owner after the call (or the new proposal).")
    reason_code: Reason | None


class ApplyResponse(BaseModel):
    rule_version: str
    assigned: int
    skipped: int
    items: list[ApplyResult]


class UnmatchedTicket(BaseModel):
    """맞는 담당자 없는 문의 한 건(KAN-234). 규칙 결과이며 AI 출력이 아니다."""

    ticket_id: UUID
    external_id: str | None
    subject: str | None
    subject_ko: str | None = Field(description="Current Korean subject translation, if any.")
    channel: str
    product_code: str
    body_language: str
    received_at: datetime
    reason_code: Literal["NO_MATCH"]
    language: str = Field(description="Primary language no active agent read when decided.")
    family: str | None = Field(
        description="Product family no agent covered with English; null = product not in "
        "product_master (unregistered product)."
    )
    trigger: Trigger
    decided_at: datetime = Field(description="When the newest (NO_MATCH) decision was made.")


class UnmatchedTickets(BaseModel):
    items: list[UnmatchedTicket] = Field(description="Newest received first.")
    has_more: bool = Field(description="More tickets than `limit`.")


# --- Improvement requests (cs_owner) ------------------------------------------------------
RequestApplyOutcome = Literal[
    "ASSIGNED", "ALREADY_ASSIGNED", "NOT_OPEN", "NOT_FOUND", "PREVIEW_CHANGED", "NO_MATCH"
]


class RequestProposal(BaseModel):
    request_id: UUID
    request_type: str
    product_code: str
    fault_id: str | None
    family: str | None = Field(description="product_master family; null for an unknown product.")
    status: str
    created_at: datetime
    user_id: UUID | None
    reason_code: RequestReason
    matched_family: str | None
    candidate_count: int
    chosen_open_count: int | None = Field(description="Open requests of the chosen agent.")


class RequestPreview(BaseModel):
    rule_version: str
    items: list[RequestProposal] = Field(description="Oldest first, the decision order.")


class RequestApplyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    user_id: UUID | None = Field(description="The previewed agent; null for NO_MATCH.")


class RequestApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_version: str = Field(min_length=1, max_length=64)
    items: Annotated[list[RequestApplyItem], Field(min_length=1, max_length=200)]


class RequestApplyResult(BaseModel):
    request_id: UUID
    outcome: RequestApplyOutcome
    user_id: UUID | None = Field(description="Owner after the call (or the new proposal).")
    reason_code: RequestReason | None


class RequestApplyResponse(BaseModel):
    rule_version: str
    assigned: int
    skipped: int
    items: list[RequestApplyResult]


class UnmatchedRequest(BaseModel):
    """맞는 담당자 없는 개선요청 한 건(KAN-234)."""

    request_id: UUID
    request_type: str
    product_code: str
    fault_id: str | None
    status: str
    created_at: datetime
    reason_code: Literal["NO_MATCH"]
    family: str | None = Field(
        description="Family of the current product; null = product not in product_master."
    )
    trigger: Literal["CANDIDATE", "BATCH"]
    decided_at: datetime


class UnmatchedRequests(BaseModel):
    items: list[UnmatchedRequest] = Field(description="Newest created first.")
    has_more: bool = Field(description="More requests than `limit`.")
