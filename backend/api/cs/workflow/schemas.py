"""Ticket workflow: review, transfer, department results and closing (permissions.md 2.1).

A department result is received, then reviewed by a C/S agent, and only an accepted result
counts toward closing. Every action names the workflow version it was decided on.
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.cs.tickets.schemas import WorkflowState
from backend.shared.departments import TransferDepartment

Code = Annotated[str, Field(min_length=1, max_length=64)]

Text = Annotated[str, Field(min_length=1, max_length=10_000)]
Reason = Annotated[str, Field(min_length=1, max_length=2_000)]
# An info_field_codebook code (eval/policy/cs_required_info.csv required_fields).
InfoCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
TransferStatus = Literal["OPEN", "RESULT_SUBMITTED", "RETURNED_BY_DEPT", "RECALLED"]
ReviewStatus = Literal["SUBMITTED", "ACCEPTED", "REWORK_REQUESTED"]
ReturnSubtype = Literal["DEFECT", "NON_DEFECT"]
# KAN-193: who is responsible for a returned product, decided by a person after inspection.
# UNDETERMINED records that inspection could not decide; it confirms no subtype.
Responsibility = Literal["MANUFACTURING", "NON_MANUFACTURING", "UNDETERMINED"]
# The resolution-case cause types. No new value for transport damage: that is
# NON_MANUFACTURING with `non_manufacturing_reason`.
CauseType = Literal["SITE_CONDITION", "HARDWARE", "SETTING", "WEAR_PART", "FIRMWARE"]


class Transfer(BaseModel):
    id: UUID
    round: int
    dept_code: str
    status: TransferStatus
    reason: str
    transferred_by: UUID
    transferred_at: datetime
    ended_by: UUID | None
    ended_at: datetime | None
    end_reason: str | None


class DeptResult(BaseModel):
    id: UUID
    transfer_id: UUID
    dept_code: str
    version: int
    result: str
    reply_content: str | None
    submitted_by: UUID
    submitted_at: datetime
    review_status: ReviewStatus
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    review_note: str | None


class Workflow(BaseModel):
    ticket_id: UUID
    state: WorkflowState
    version: int = Field(description="0 until the first action. Send it with every action.")
    owner_user_id: UUID | None
    customer_sent_at: datetime | None
    closed_at: datetime | None
    closed_by: UUID | None
    # KAN-193: what a person confirmed after inspection. The LLM `return_subtype` on the
    # ticket is the customer's stated reason at intake and never fills these.
    return_responsibility: Responsibility | None = Field(
        default=None, description="Confirmed by a C/S agent after inspection; null until then."
    )
    return_cause_type: CauseType | None = None
    return_non_manufacturing_reason: str | None = None
    return_response_note: str | None = None
    return_subtype_confirmed: ReturnSubtype | None = Field(
        default=None,
        description="Derived from responsibility for routing: MANUFACTURING is DEFECT, "
        "NON_MANUFACTURING is NON_DEFECT, UNDETERMINED and unconfirmed are null.",
    )
    return_subtype_confirmed_by: UUID | None = None
    return_subtype_confirmed_at: datetime | None = None
    transfers: list[Transfer] = Field(description="Oldest first.")
    dept_results: list[DeptResult] = Field(description="Oldest first.")
    # Customer mail (KAN-110). Shown to users who see every ticket; empty for department users.
    mail_origin: "MailOrigin | None" = None
    customer_sends: list["CustomerSend"] = Field(default_factory=list, description="Oldest first.")
    customer_replies: list["CustomerReply"] = Field(
        default_factory=list, description="Oldest first."
    )


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0, description="The workflow version the user decided on.")


class TransferRequest(Action):
    dept_code: TransferDepartment
    reason: Reason


class ReasonRequest(Action):
    reason: Reason


class DeptResultRequest(Action):
    result: Text
    reply_content: Text | None = None


class ReviewRequest(Action):
    result_version: int = Field(ge=1, description="The result version that was reviewed.")
    decision: Literal["ACCEPT", "REWORK"]
    note: Reason | None = None

    @model_validator(mode="after")
    def check_note(self) -> Self:
        if self.decision == "REWORK" and self.note is None:
            raise ValueError("A rework request needs a note for the department.")
        return self


class OwnerRequest(Action):
    owner_user_id: UUID


class ReturnConfirmationRequest(Action):
    """A person's inspection result for a return (KAN-193). Always human_input; a model
    result cannot be submitted here."""

    responsibility: Responsibility
    cause_type: CauseType | None = None
    non_manufacturing_reason: Reason | None = Field(
        default=None, description="Only with NON_MANUFACTURING, e.g. damaged in transport."
    )
    response_note: Reason = Field(description="What was done or will be done for the customer.")

    @model_validator(mode="after")
    def check_reason(self) -> Self:
        if self.non_manufacturing_reason is not None and self.responsibility != "NON_MANUFACTURING":
            raise ValueError("A non-manufacturing reason needs NON_MANUFACTURING responsibility.")
        return self


def confirmed_subtype(responsibility: Responsibility | None) -> ReturnSubtype | None:
    """The subtype routing reads (rules.route `confirmed_subtype`). UNDETERMINED confirms none,
    so routing keeps the customer's stated value with its review reason."""
    return {"MANUFACTURING": "DEFECT", "NON_MANUFACTURING": "NON_DEFECT"}.get(  # type: ignore[return-value]
        responsibility or ""
    )


class ResolutionCaseCreate(Action):
    """A resolution case for a closed ticket, written by the C/S agent (workflow ⑧).

    Product, origin and times come from the ticket and the server. RMA numbers, visit dates
    and warranty decisions are entered by people, never generated.
    """

    fault_id: str = Field(pattern=r"^FLT-[0-9]{3}$")
    fw_version_at_issue: Code | None = None
    symptom: Text
    root_cause: Text
    root_cause_type: CauseType
    action_taken: Text
    onsite_visit: bool
    rma_no: Code | None = None
    fw_fixed_version: Code | None = None
    resolved_by_dept: TransferDepartment | Literal["GCS_L1"]
    recurrence_prevention: Text | None = None
    dept_result_id: UUID | None = Field(
        default=None, description="An accepted department result of this ticket."
    )
    # KAN-193: optional. Not copied from the workflow confirmation; the person states it here.
    responsibility: Responsibility | None = None
    non_manufacturing_reason: Reason | None = None

    @model_validator(mode="after")
    def check_reason(self) -> Self:
        if self.non_manufacturing_reason is not None and self.responsibility != "NON_MANUFACTURING":
            raise ValueError("A non-manufacturing reason needs NON_MANUFACTURING responsibility.")
        return self


# --- Customer mail (KAN-110) ---------------------------------------------------------------

SendKind = Literal["ANSWER", "INFO_REQUEST"]
DraftLabel = Literal["CONCISE", "DETAILED", "INFO_FIRST"]
RequestStatus = Literal["PENDING", "REGISTERED", "REJECTED", "REQUEST_FAILED", "REQUEST_UNKNOWN"]
Address = Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")]


class CustomerSend(BaseModel):
    """An approved customer message and what the platform said about it so far.

    `request_status` is about handing the message to the platform; `send_status` is the
    platform's own state (QUEUED, SUBMITTING, ACCEPTED, UNKNOWN, FAILED). ACCEPTED means the
    mail provider took the message, not that the customer received it.
    """

    id: UUID
    kind: SendKind
    workflow_version: int
    mailbox_id: UUID
    subject: str
    body_type: Literal["text", "html"]
    body: str
    to_addresses: list[str]
    cc_addresses: list[str]
    payload_sha256: str
    approved_by: UUID
    approved_at: datetime
    request_status: RequestStatus
    request_error: str | None
    intent_id: UUID | None
    send_status: str | None
    send_error_code: str | None
    attempts: int
    accepted_at: datetime | None
    sent_copy_confirmed_at: datetime | None
    status_checked_at: datetime | None
    recorded_at: datetime | None = Field(
        description="When ACCEPTED moved the ticket to ANSWERED or WAITING_CUSTOMER."
    )
    draft_id: UUID | None = Field(default=None, description="Reviewed AI draft, if any.")
    requested_info_codes: list[str] = Field(
        default_factory=list, description="Information codes this message asked for."
    )
    draft_option: DraftLabel | None = None
    created_at: datetime


class MailOrigin(BaseModel):
    message_id: UUID
    mailbox_id: UUID
    sender_address: str
    received_at: datetime
    registered_by: UUID


class CustomerReply(BaseModel):
    message_id: UUID
    method: Literal["HEADER", "THREAD", "HUMAN"]
    received_at: datetime
    reopened: bool
    created_at: datetime


class CustomerSendRequest(Action):
    """A C/S agent approves an answer or an information request for the customer."""

    kind: SendKind
    mailbox_id: UUID = Field(description="The approver's connected mailbox (KAN-81).")
    subject: Annotated[str, Field(min_length=1, max_length=500)]
    body_type: Literal["text", "html"] = "text"
    body: Annotated[str, Field(min_length=1, max_length=200_000)]
    to: list[Address] | None = Field(
        default=None,
        max_length=50,
        description="Recipients. Omitted: the sender of the mail that opened the ticket.",
    )
    cc: list[Address] = Field(default_factory=list, max_length=50)
    # The reviewed AI draft this message came from (KAN-107 drafts). Both or neither: a message
    # the agent wrote without a draft names none. The body is what the agent approved, edited
    # or not; the draft row itself is never changed.
    draft_id: UUID | None = None
    draft_option: DraftLabel | None = None
    # Information codes this message asks the customer for (KAN-107). Omitted with a draft: the
    # codes of the chosen option's questions. Omitted without one: nothing asked. Later drafts
    # do not ask these again once the message was sent.
    requested_info_codes: list[InfoCode] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def no_duplicates(self) -> Self:
        addresses = [a.lower() for a in (self.to or []) + self.cc]
        if len(addresses) != len(set(addresses)):
            raise ValueError("Recipients must not repeat")
        if (self.draft_id is None) != (self.draft_option is None):
            raise ValueError("Give draft_id and draft_option together")
        codes = self.requested_info_codes or []
        if len(codes) != len(set(codes)):
            raise ValueError("Information codes must not repeat")
        return self


class CustomerReplyIn(BaseModel):
    """The platform reports a customer reply it linked to this ticket (KAN-75)."""

    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    method: Literal["HEADER", "THREAD", "HUMAN"]
    received_at: datetime


class CustomerReplyResult(BaseModel):
    ticket_id: UUID
    message_id: UUID
    state: WorkflowState
    reopened: bool
    recorded: bool = Field(description="False when this reply was recorded before.")
