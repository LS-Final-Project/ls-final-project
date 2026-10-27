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
TransferStatus = Literal["OPEN", "RESULT_SUBMITTED", "RETURNED_BY_DEPT", "RECALLED"]
ReviewStatus = Literal["SUBMITTED", "ACCEPTED", "REWORK_REQUESTED"]


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
    transfers: list[Transfer] = Field(description="Oldest first.")
    dept_results: list[DeptResult] = Field(description="Oldest first.")


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


class ResolutionCaseCreate(Action):
    """A resolution case for a closed ticket, written by the C/S agent (workflow ⑧).

    Product, origin and times come from the ticket and the server. RMA numbers, visit dates
    and warranty decisions are entered by people, never generated.
    """

    fault_id: str = Field(pattern=r"^FLT-[0-9]{3}$")
    fw_version_at_issue: Code | None = None
    symptom: Text
    root_cause: Text
    root_cause_type: Literal["SITE_CONDITION", "HARDWARE", "SETTING", "WEAR_PART", "FIRMWARE"]
    action_taken: Text
    onsite_visit: bool
    rma_no: Code | None = None
    fw_fixed_version: Code | None = None
    resolved_by_dept: TransferDepartment | Literal["GCS_L1"]
    recurrence_prevention: Text | None = None
    dept_result_id: UUID | None = Field(
        default=None, description="An accepted department result of this ticket."
    )
