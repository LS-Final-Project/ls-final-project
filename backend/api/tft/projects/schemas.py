"""TFT project read models (KAN-119).

Only the project and its RFP requirements remain. The staffing stages (required roles,
staffing requests, recommendations, checks, compositions, responsible decisions) were
removed from the API in KAN-181; their tables stay in the database.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.api.tft.rfp.schemas import (
    IntegrationResponsibility,
    IntegrationSummary,
    JobRef,
    JudgementStatus,
    OffListPermission,
    OutputStatus,
    PendingByOrigin,
    Purpose,
    RequirementCounts,
)

DataOrigin = Literal["synthetic", "demo", "real"]
DerivationKind = Literal["PROVIDED", "DERIVED", "SYSTEM", "ASSUMED"]
FieldTag = Literal[
    "DESIGN", "CIVIL_STRUCTURE", "CONSTRUCTION_TRANSPORT", "QUALITY_TEST", "PROJECT_MANAGEMENT"
]
ProjectStatus = Literal["INITIAL_REVIEW", "STAFFING", "COMPOSING", "CONFIRMED"]


class ProjectSummary(BaseModel):
    supply_region: str | None = None
    customer_manufacturer_list: list[str] | None = None
    off_list_permission: OffListPermission = "UNSTATED"
    id: UUID
    external_id: str | None
    name: str
    company_code: str
    status: ProjectStatus
    data_origin: DataOrigin
    derivation_kind: DerivationKind
    responsible_user_id: UUID | None = Field(description="Login user ID, never a candidate ID.")
    sales_user_id: UUID | None = Field(
        default=None, description="Responsible sales user (login user ID) who issues requests."
    )
    created_at: datetime
    # RFP API spec §4.1. The fields above stay until the old screens are replaced.
    version: int
    known_at: datetime
    client_name: str | None
    output_status: OutputStatus
    judgement_status: JudgementStatus
    requirements_count: int
    unresolved_mandatory_count: int
    pending_proposals_count: int
    open_questions_count: int
    strategic_customer: bool | None = None
    integration_responsibility: IntegrationSummary


class ProjectList(BaseModel):
    items: list[ProjectSummary]
    next_cursor: str | None


class RfpRequirement(BaseModel):
    id: UUID
    requirement_code: str
    field_tag: FieldTag | None
    title: str
    description: str | None
    document_name: str | None
    document_id: UUID | None
    document_version_id: UUID | None
    clause: str | None
    page: int | None
    excerpt: str | None
    data_origin: DataOrigin
    derivation_kind: DerivationKind


class ProjectDetail(BaseModel):
    supply_region: str | None = None
    customer_manufacturer_list: list[str] | None = None
    off_list_permission: OffListPermission = "UNSTATED"
    id: UUID
    external_id: str | None
    version: int
    data_origin: DataOrigin
    derivation_kind: DerivationKind
    known_at: datetime
    created_at: datetime
    company_code: str
    name: str
    client_name: str | None
    status: ProjectStatus
    responsible_user_id: UUID | None
    sales_user_id: UUID | None = Field(
        default=None, description="Responsible sales user (login user ID) who issues requests."
    )
    rfp_requirements: list[RfpRequirement]
    # RFP API spec §4.1. status, external_id and rfp_requirements stay until the old screens go.
    output_status: OutputStatus
    judgement_status: JudgementStatus
    purpose: Purpose
    customer_goal: str | None
    review_departments: list[str]
    requirements_count: int
    unresolved_mandatory_count: int
    pending_proposals_count: int
    open_questions_count: int
    requirement_counts: RequirementCounts
    pending_proposals_by_origin: PendingByOrigin
    latest_job: JobRef | None
    draft_version: int | None
    review_version: int | None
    documents_count: int
    strategic_customer: bool | None = None
    strategic_customer_note: str | None = None
    strategic_customer_set_by: UUID | None = None
    integration_responsibility: IntegrationResponsibility | None = Field(
        default=None,
        description=(
            "Assembly integration responsibility (RFP API spec v1.3 §7), computed on read. "
            "Null when migration T12 is not applied yet, which is not the same as not required."
        ),
    )
