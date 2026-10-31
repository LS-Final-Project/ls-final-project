"""Analysis view and correction request (KAN-107).

`ai_draft` values come from a classification run, `human_input` values from a person. The
response shows both and the value the rules use, with its source. Corrections are not
evaluation answers (KAN-95): they are operational records only.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.engine.cs.classification import Category, Urgency
from backend.engine.cs.verification import SentenceVerdict, VerificationStatus

Source = Literal["ai_draft", "human_input"]
InfoCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Reason = Annotated[str, Field(min_length=1, max_length=2_000)]


class AnalysisRun(BaseModel):
    id: UUID
    run_seq: int
    job_id: UUID | None
    outcome: Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD"]
    classification: dict[str, Any] | None = Field(
        description="The five validated fields; only a VALID run has them."
    )
    input_version: str
    model_version: str | None
    prompt_version: str | None
    error_code: str | None
    provenance_kind: Literal["ai_draft"]
    created_at: datetime


class VerificationRun(BaseModel):
    id: UUID
    draft_id: UUID
    job_id: UUID | None
    attempt_number: int
    outcome: Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD"]
    sentences: list[SentenceVerdict]
    input_version: str
    model_version: str | None
    prompt_version: str
    error_code: str | None
    created_at: datetime


class DraftRun(BaseModel):
    id: UUID
    seq: int
    analysis_id: UUID | None
    job_id: UUID | None
    outcome: Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD"]
    draft: dict[str, Any] | None = Field(
        description="Three reply options as validated; only a VALID run has them."
    )
    input_version: str
    model_version: str | None
    prompt_version: str | None
    error_code: str | None
    provenance_kind: Literal["ai_draft"]
    created_at: datetime
    verification_status: VerificationStatus = "NOT_VERIFIED"
    verifications: list[VerificationRun] = Field(default_factory=list)


class Correction(BaseModel):
    id: UUID
    seq: int
    based_on_analysis_id: UUID | None
    fields: dict[str, Any] = Field(description="Only the fields this correction changed.")
    reason: str
    corrected_by: UUID
    provenance_kind: Literal["human_input"]
    created_at: datetime


class FieldView(BaseModel):
    ai_value: Any
    corrected_value: Any
    effective_value: Any
    source: Source | None
    corrected: bool


class RuleView(BaseModel):
    policy_version: str
    priority: str
    priority_rule_id: str
    product_family: str
    dept: str
    dept_rule_id: str
    escalation_dept: str | None
    escalation_condition: str | None
    review_reasons: list[str]
    sla_id: str
    first_response_hours: int
    resolution_hours: int
    first_response_due: datetime
    resolution_due: datetime


class DeadlineView(BaseModel):
    ai: datetime | None
    corrected: datetime | None
    effective: datetime | None = Field(description="The earlier of the two (2026-10-29).")
    source: Source | None


class Rules(BaseModel):
    ai: RuleView | None = Field(description="Rules from the latest valid AI run.")
    ai_error: str | None
    effective: RuleView | None = Field(
        description="Rules from the effective values. Priority, department and review reasons "
        "follow these; deadlines are below."
    )
    effective_error: str | None
    tier_note: str | None = Field(
        default=None,
        description="Set when the customer tier is unknown and the standard tier was used.",
    )
    first_response_due: DeadlineView
    resolution_due: DeadlineView


class MissingInfoView(BaseModel):
    category_code: Category = Field(description="Category whose required-info row was used.")
    required: list[str] = Field(
        description="Required info codes for this product family and category."
    )
    provided: list[str] = Field(description="Info codes the customer gave.")
    already_asked: list[str] = Field(
        description="Codes asked in messages that reached the customer."
    )
    missing: list[str] = Field(description="required − provided − already_asked, sorted.")


class MissingInfo(BaseModel):
    ai: MissingInfoView | None = Field(description="From the AI category and provided info.")
    effective: MissingInfoView | None = Field(
        description="From the effective (corrected) category and provided info. Follow this one."
    )
    error: str | None = Field(
        description="Why it cannot be counted: CS_ANALYSIS_MISSING (no valid run and no "
        "correction) or CS_RULE_INPUT (product outside the policy)."
    )


class TicketAnalysis(BaseModel):
    ticket_id: UUID
    version: int = Field(description="Number of corrections. Send it with a correction.")
    latest_run: AnalysisRun | None
    latest_valid_run_id: UUID | None = Field(
        description="Send it as analysis_id with a correction."
    )
    runs: list[AnalysisRun] = Field(description="Oldest first.")
    drafts: list[DraftRun] = Field(description="Oldest first.")
    latest_valid_draft_id: UUID | None
    corrections: list[Correction] = Field(description="Oldest first.")
    fields: dict[str, FieldView]
    return_subtype_stated: Literal["DEFECT", "NON_DEFECT"] | None = Field(
        description="What the customer stated (AI). Confirmed only through workflow/return-subtype."
    )
    rules: Rules
    missing_info: MissingInfo = Field(
        description="Info still needed from the customer. Recounted from corrected values."
    )


class CorrectionRequest(BaseModel):
    """Only the fields given are corrected. `secondary_category: null` clears it."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0, description="`version` of the analysis the person read.")
    analysis_id: UUID | None = Field(
        description="`latest_valid_run_id` the person read; null only when there is none."
    )
    reason: Reason
    category_code: Category | None = None
    secondary_category: Category | None = None
    urgency_flag: Urgency | None = None
    provided_info: list[InfoCode] | None = Field(default=None, max_length=50)


class AnalysisJob(BaseModel):
    """플랫폼에 등록된 AI 분석 작업(KAN-107). 등록이 처리 시작을 뜻하지 않는다."""

    job_id: UUID
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]
    status_url: str = Field(description="Platform job status; events at status_url + '/events'.")
    created: bool = Field(
        description="False when the same Idempotency-Key returned an earlier job."
    )
