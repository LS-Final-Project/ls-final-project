"""TFT project read models (KAN-119).

The six stages of the KAN-57 workflow are separate records, each with its own status:
initial review (requirements, required roles, staffing requests), department
recommendation, experience evidence, availability checks, composition versions and the
responsible-user decision (assignment history, final confirmation).
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

DataOrigin = Literal["synthetic", "demo", "real"]
DerivationKind = Literal["PROVIDED", "DERIVED", "SYSTEM", "ASSUMED"]
FieldTag = Literal[
    "DESIGN", "CIVIL_STRUCTURE", "CONSTRUCTION_TRANSPORT", "QUALITY_TEST", "PROJECT_MANAGEMENT"
]
ProjectStatus = Literal["INITIAL_REVIEW", "STAFFING", "COMPOSING", "CONFIRMED"]
RoleStatus = Literal[
    "DRAFTING",
    "ISSUED",
    "RECOMMENDATIONS_RECEIVED",
    "DEPT_VERIFIED",
    "COMPARING",
    "IN_COMPOSITION",
    "CONFIRMED",
]
RequestStatus = Literal["DRAFT", "ISSUED", "ANSWERED", "CLOSED"]
Participation = Literal["LEAD", "SUPPORT"]
RecommendationStatus = Literal["RECOMMENDED", "WITHDRAWN"]
# UNSPECIFIED: nothing recorded. NEEDS_CHECK: not confirmed yet (never "lacking").
# CONFIRMED / UNMET: checked by a department user at a time with a basis.
CheckStatus = Literal["UNSPECIFIED", "NEEDS_CHECK", "CONFIRMED", "UNMET"]
MemberStatus = Literal["PROPOSED", "NEEDS_REINFORCEMENT", "CONFIRMED"]


class ProjectSummary(BaseModel):
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
    current_composition_version: int | None
    created_at: datetime


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


class RoleRequirementLink(BaseModel):
    requirement_id: UUID
    rationale: str | None


class RoleCompetency(BaseModel):
    id: UUID
    kind: Literal["REQUIRED", "PREFERRED"]
    description: str


class RequiredRole(BaseModel):
    id: UUID
    role_code: str
    title: str
    field_tag: FieldTag | None
    tasks: str | None
    experience_condition: str | None
    is_core: bool
    headcount: int | None = Field(description="Null means not decided yet.")
    period_start: date | None
    period_end: date | None
    allocation_rate: float | None
    status: RoleStatus
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    next_action: str | None
    next_action_organization_id: UUID | None
    reply_due_at: datetime | None
    requirements: list[RoleRequirementLink]
    competencies: list[RoleCompetency]
    data_origin: DataOrigin
    derivation_kind: DerivationKind


class StaffingRequest(BaseModel):
    """Department staffing request (KAN-122). Shared by the project detail and the
    staffing-request routes, so both read the same columns."""

    id: UUID
    project_id: UUID
    required_role_id: UUID
    role_name: str
    organization_id: UUID
    organization_name: str
    organization_dept_code: str | None = Field(
        description="identity.departments.code of the organization, when linked."
    )
    participation: Participation
    status: RequestStatus
    version: int = Field(description="Sent back as ETag and required in If-Match for writes.")
    due_at: datetime | None
    due_undecided: bool = Field(description="Explicit 'undecided' mark, apart from a null due_at.")
    message: str | None
    issued_by_user_id: UUID | None
    issued_at: datetime | None
    created_at: datetime
    updated_at: datetime
    data_origin: DataOrigin
    derivation_kind: DerivationKind


class ExperienceEvidence(BaseModel):
    id: UUID
    summary: str
    project_name: str | None
    role_title: str | None
    period_start: date | None
    period_end: date | None
    evidence_source: str | None
    status: CheckStatus
    verified_by_user_id: UUID | None
    verified_at: datetime | None
    basis: str | None
    data_origin: DataOrigin
    derivation_kind: DerivationKind


class CompetencyCheck(BaseModel):
    id: UUID
    competency_id: UUID
    status: CheckStatus
    checked_by_user_id: UUID | None
    checked_at: datetime | None
    basis: str | None
    data_origin: DataOrigin
    derivation_kind: DerivationKind


class AvailabilityCheck(BaseModel):
    id: UUID
    period_start: date | None
    period_end: date | None
    allocation_rate: float | None = Field(description="Percent of working time, 0 to 100.")
    constraints_note: str | None
    checked_by_user_id: UUID | None
    checked_at: datetime | None
    basis: str | None
    status: CheckStatus
    data_origin: DataOrigin
    derivation_kind: DerivationKind


class CandidateRecommendation(BaseModel):
    id: UUID
    staffing_request_id: UUID
    required_role_id: UUID
    candidate_id: UUID = Field(description="Candidate personnel ID, never a login user ID.")
    candidate_name: str
    organization_id: UUID
    recommended_by_user_id: UUID
    recommended_at: datetime
    rationale: str | None
    status: RecommendationStatus
    experience_evidence: list[ExperienceEvidence]
    competency_checks: list[CompetencyCheck]
    availability_checks: list[AvailabilityCheck]
    data_origin: DataOrigin
    derivation_kind: DerivationKind


class CompositionMember(BaseModel):
    id: UUID
    required_role_id: UUID
    recommendation_id: UUID | None = Field(description="Null records the role as an open gap.")
    status: MemberStatus
    rationale: str | None
    remaining_conditions: str | None
    gap_note: str | None
    next_action_organization_id: UUID | None
    follow_up_due_at: datetime | None


class CompositionVersion(BaseModel):
    id: UUID
    version: int
    created_by_user_id: UUID
    created_at: datetime
    note: str | None
    members: list[CompositionMember]
    data_origin: DataOrigin
    derivation_kind: DerivationKind


class ResponsibleSummary(BaseModel):
    """Latest assignment of the responsible user, from the change history."""

    user_id: UUID
    assigned_by_user_id: UUID
    assigned_at: datetime
    reason: str


class FinalConfirmation(BaseModel):
    id: UUID
    composition_version_id: UUID
    composition_version: int
    confirmed_by_user_id: UUID
    confirmed_at: datetime
    note: str | None


class ProjectDetail(BaseModel):
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
    responsible: ResponsibleSummary | None
    rfp_requirements: list[RfpRequirement]
    required_roles: list[RequiredRole]
    staffing_requests: list[StaffingRequest]
    recommendations: list[CandidateRecommendation]
    current_composition: CompositionVersion | None
    final_confirmation: FinalConfirmation | None
