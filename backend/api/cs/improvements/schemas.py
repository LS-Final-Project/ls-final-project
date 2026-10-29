"""Improvement requests (C/S proposal 4.4, 6.1; permissions.md 2.2).

A request is written and versioned here. State transitions, delivery, department replies
and actions are recorded by KAN-116 through the same tables.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.shared.departments import TransferDepartment

Text = Annotated[str, Field(min_length=1, max_length=10_000)]
Short = Annotated[str, Field(min_length=1, max_length=200)]
Reason = Annotated[str, Field(min_length=1, max_length=2_000)]
RequestType = Literal["FAULT", "DOCUMENT"]
DocSignal = Literal["NOT_FOUND", "VALUE_CONFLICT", "BROKEN_LINK", "REPEATED_SPEC"]
RequestStatus = Literal[
    "DRAFT",
    "REVIEWED",
    "SENT",
    "ACKNOWLEDGED",
    "ADOPTED",
    "INFO_REQUESTED",
    "REJECTED",
    "ACTION_TAKEN",
    "FOLLOW_UP",
    "COMPLETED",
]
DeptStatus = Literal[
    "SENT", "ACKNOWLEDGED", "ADOPTED", "INFO_REQUESTED", "REJECTED", "ACTION_TAKEN"
]
ConditionStatus = Literal["CONFIRMED", "UNKNOWN"]

# Content a person writes and versions. Group keys (type, product, fault, signal) are fixed
# once created; a different group is a different request.
CONTENT_FIELDS = (
    "problem_statement",
    "confirmed_facts",
    "inconsistencies",
    "missing_info",
    "not_claimed",
    "review_task",
    "proposed_depts",
    "condition_status",
    "condition_summary",
    "fw_fixed_version",
    "fw_released_on",
    "doc_id",
)


class Content(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_statement: Text = Field(
        description="What to investigate, in one sentence. It does not declare a cause."
    )
    confirmed_facts: Text | None = None
    inconsistencies: Text | None = None
    missing_info: Text | None = None
    not_claimed: Text | None = Field(default=None, description="Conclusions not to draw.")
    review_task: Text | None = None
    proposed_depts: list[TransferDepartment] = Field(default_factory=list, max_length=12)
    condition_status: ConditionStatus = "UNKNOWN"
    condition_summary: Text | None = None
    fw_fixed_version: Short | None = None
    fw_released_on: date | None = None
    doc_id: Short | None = None

    @model_validator(mode="after")
    def check_departments(self) -> Self:
        if len(set(self.proposed_depts)) != len(self.proposed_depts):
            raise ValueError("A department is proposed twice.")
        return self


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID
    case_id: UUID | None = Field(
        default=None, description="The ticket's resolution case. Required for a fault request."
    )


class RequestCreate(Content):
    """A request written by a C/S agent. System candidates come from KAN-113."""

    request_type: RequestType
    product_code: Short
    fault_id: str | None = Field(default=None, pattern=r"^FLT-[0-9]{3}$")
    doc_signal: DocSignal | None = None
    evidence: list[EvidenceInput] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def check_group(self) -> Self:
        if self.request_type == "FAULT":
            if self.fault_id is None or self.doc_signal is not None:
                raise ValueError("A fault request needs fault_id and no doc_signal.")
            if not self.proposed_depts:
                raise ValueError("A fault request needs at least one proposed department.")
        elif self.doc_signal is None or self.fault_id is not None:
            raise ValueError("A document request needs doc_signal and no fault_id.")
        tickets = [e.ticket_id for e in self.evidence]
        if len(set(tickets)) != len(tickets):
            raise ValueError("A ticket is listed twice as evidence.")
        return self


class RequestUpdate(BaseModel):
    """Changes to the content. `version` must match; each change makes a new version."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    problem_statement: Text | None = None
    confirmed_facts: Text | None = None
    inconsistencies: Text | None = None
    missing_info: Text | None = None
    not_claimed: Text | None = None
    review_task: Text | None = None
    proposed_depts: list[TransferDepartment] | None = Field(default=None, max_length=12)
    condition_status: ConditionStatus | None = None
    condition_summary: Text | None = None
    fw_fixed_version: Short | None = None
    fw_released_on: date | None = None
    doc_id: Short | None = None

    @model_validator(mode="after")
    def check_fields(self) -> Self:
        sent = self.model_fields_set & set(CONTENT_FIELDS)
        if not sent:
            raise ValueError("At least one content field is required.")
        for name in ("problem_statement", "proposed_depts", "condition_status"):
            if name in sent and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        if self.proposed_depts and len(set(self.proposed_depts)) != len(self.proposed_depts):
            raise ValueError("A department is proposed twice.")
        return self

    def changed_values(self) -> dict[str, Any]:
        return {
            name: getattr(self, name) for name in CONTENT_FIELDS if name in self.model_fields_set
        }


class EvidenceAdd(EvidenceInput):
    version: int = Field(ge=1)


class EvidenceRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason: Reason


class OwnerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    owner_user_id: UUID


class ImprovementRequest(BaseModel):
    id: UUID
    request_type: RequestType
    origin: Literal["SYSTEM_CANDIDATE", "MANUAL"]
    data_origin: Literal["synthetic", "demo", "real"]
    derivation_kind: Literal["PROVIDED", "DERIVED", "SYSTEM", "ASSUMED"]
    product_code: str
    fault_id: str | None
    doc_signal: DocSignal | None
    doc_id: str | None
    condition_status: ConditionStatus
    condition_summary: str | None
    fw_fixed_version: str | None
    fw_released_on: date | None
    status: RequestStatus
    version: int
    current_round: int
    cs_owner: UUID | None
    problem_statement: str
    confirmed_facts: str | None
    inconsistencies: str | None
    missing_info: str | None
    not_claimed: str | None
    review_task: str | None
    proposed_depts: list[str]
    receiver_unconfirmed: bool = Field(
        description="No receiving department yet. A document request's receiver needs "
        "confirmation with the business before it can be sent."
    )
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    sent_at: datetime | None
    followup_plan: str | None
    followup_result: str | None
    completed_by: UUID | None
    completed_at: datetime | None
    created_by: UUID | None
    created_at: datetime


class RequestSummary(BaseModel):
    id: UUID
    request_type: RequestType
    product_code: str
    fault_id: str | None
    doc_signal: DocSignal | None
    status: RequestStatus
    version: int
    cs_owner: UUID | None
    problem_statement: str
    evidence_count: int
    created_at: datetime


class RequestList(BaseModel):
    items: list[RequestSummary]


class Evidence(BaseModel):
    id: UUID
    ticket_id: UUID
    ticket_external_id: str | None
    case_id: UUID | None
    case_external_id: str | None
    added_by: UUID | None
    added_at: datetime
    removed_by: UUID | None
    removed_at: datetime | None
    removal_reason: str | None


class RequestDepartment(BaseModel):
    round: int
    dept_code: str
    status: DeptStatus
    carried_over: bool
    sent_version: int | None
    reply_due_at: datetime | None
    rejection_reviewed_by: UUID | None
    rejection_reviewed_at: datetime | None


class DeptReply(BaseModel):
    id: UUID
    round: int
    dept_code: str
    request_version: int
    reply_type: Literal["ACKNOWLEDGED", "ADOPTED", "INFO_REQUESTED", "REJECTED"]
    reason: str | None
    replied_by: UUID
    replied_at: datetime


class RequestAction(BaseModel):
    id: UUID
    round: int
    dept_code: str
    action: str
    action_scope: str
    action_date: date
    action_evidence: str
    recorded_by: UUID
    recorded_at: datetime


NextTask = Literal[
    "REVIEW",
    "CONFIRM_RECEIVER",
    "SEND",
    "ACKNOWLEDGE",
    "REPLY",
    "RECORD_ACTION",
    "SUPPLEMENT",
    "REVIEW_REJECTION",
    "PLAN_FOLLOWUP",
    "RECORD_FOLLOWUP",
]


class NextOwner(BaseModel):
    """Who acts next and on what. The C/S owner may still be unassigned (null)."""

    actor: Literal["CS_OWNER", "DEPARTMENT"]
    user_id: UUID | None
    dept_code: str | None
    task: NextTask


class RequestDetail(BaseModel):
    request: ImprovementRequest
    evidence: list[Evidence] = Field(description="Active links first, then removed ones.")
    departments: list[RequestDepartment] = Field(description="Departments of the current round.")
    replies: list[DeptReply]
    actions: list[RequestAction]
    next_owners: list[NextOwner]


class RequestVersion(BaseModel):
    version: int
    content: dict[str, Any]
    changed_by: UUID | None
    changed_at: datetime


class RequestVersionList(BaseModel):
    items: list[RequestVersion]


# --- KAN-116: review, delivery, department replies, actions, follow-up -------------------


class Versioned(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, description="The request version the user decided on.")


class ImprovementReviewRequest(Versioned):
    """DRAFT → REVIEWED. The agent may fix the proposed departments while reviewing."""

    proposed_depts: list[TransferDepartment] | None = None


class SendRequest(Versioned):
    """REVIEWED → first round. Target departments default to the proposed ones."""

    dept_codes: list[TransferDepartment] | None = None
    reply_due_days: int = Field(default=7, ge=1, le=90)


class DeptReplyRequest(Versioned):
    reply_type: Literal["ADOPTED", "INFO_REQUESTED", "REJECTED"]
    reason: Reason | None = None

    @model_validator(mode="after")
    def check_reason(self) -> Self:
        if self.reply_type == "REJECTED" and self.reason is None:
            raise ValueError("A rejection needs a reason.")
        return self


class DeptActionRequest(Versioned):
    """A department's improvement action, kept apart from any one customer's repair."""

    action: Text
    action_scope: Text
    action_date: date
    action_evidence: Text


class ResendRequest(Versioned):
    """Calls one department again inside the current round (supplement, re-request after a
    rejection with new evidence, or a reminder after the reply deadline)."""

    reason: Reason
    reply_due_days: int = Field(default=7, ge=1, le=90)


class FollowupPlanRequest(Versioned):
    followup_plan: Text


class CompleteRequest(Versioned):
    followup_result: Text
