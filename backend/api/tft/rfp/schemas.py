"""RFP 자사 대응·협력사 보완의 상태 값과 HTTP 모델(KAN-182, A01).

원본 합의는 "RFP 자사 대응·협력사 보완 API 명세 v1.1"(contracts/tft/rfp-data.md에서 링크)이다.
이 파일이 OpenAPI 초안의 원본이며, 경로 구현은 KAN-188(A07) 등 각 작업이 한다.

- 요구의 판단은 점수 하나가 아니라 네 가지 상태(`RequirementStatus`)로 둔다.
- 모든 업무 행은 판본(`version`)과 두 출처 축(`data_origin`, `derivation_kind`), `known_at`을 가진다.
- 사람 판단과 AI 결과는 `provenance_kind`로 구분한다.
"""

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.tft.partners.grading import Grade
from backend.api.tft.partners.schemas import Attribute, LeadTime, Scope

# --- 공통 축 -----------------------------------------------------------------------------

DataOrigin = Literal["synthetic", "demo", "real"]
DerivationKind = Literal["PROVIDED", "DERIVED", "SYSTEM", "ASSUMED"]
ProvenanceKind = Literal["ai_draft", "human_input", "system_record"]

# --- 상태 값 (명세 §2.1) ----------------------------------------------------------------

# 산출물이 서비스 안에서 어디까지 왔는지. 입찰 통과·수주와 별개다.
OutputStatus = Literal["INTAKE", "ANALYZING", "REVIEW_DRAFT"]
# 프로젝트 단위 사람 판단 요약. 산출물 상태와 서로 대체하지 않는다.
JudgementStatus = Literal["NEEDS_REVIEW", "CONDITION_CHECK", "OWNER_CONFIRMED"]
# 요구 하나의 판단. "자료 없음"은 판단 근거 부족이지 미충족이 아니다.
RequirementStatus = Literal["EVIDENCE_FOUND", "CONDITIONAL", "SPEC_GAP", "INSUFFICIENT_EVIDENCE"]
# 요구 성격. 원문이 구분하지 않으면 UNDETERMINED. 요구 상태 CONDITIONAL과 다른 단어다.
Necessity = Literal["MANDATORY", "PREFERRED", "CONDITION_BOUND", "UNDETERMINED"]
SupplementPath = Literal[
    "OTHER_OWN_PRODUCT",
    # 자사 개발·수행 가능 여부(R&D 확인), 자사 상업 조건 변경 검토(KAN-186, 기획서 최종본 V_1 4.4).
    "OWN_DEVELOPMENT",
    # OWN_TERMS_REVIEW는 명세 v1.3에 추가 요청 중이다(KAN-186 댓글). 나머지는 명세 v1.3 §2의 8값이다.
    "OWN_TERMS_REVIEW",
    "ALTERNATIVE_SPEC",
    "EXTERNAL_PRODUCT",
    "TASK_SUBCONTRACT",
    "JOINT_PROPOSAL",
    "CUSTOMER_SPECIFIED_PROCUREMENT",
    "NONE",
]
DeficiencyCause = Literal[
    "RATING",
    "CERTIFICATION",
    "FORM",
    "TASK",
    "DESIGNATED_MODEL",
    "COMMERCIAL",
    "CORE_EQUIPMENT_MISSING",
]
# 요구 단위 R&D 확인 상태(명세 v1.3 §5.2). 서버가 R&D 질문·답변에서 계산하고 WAIVED만 사람이 넣는다.
RndCheck = Literal[
    "NOT_REQUIRED",
    "PENDING",
    "CONFIRMED_FEASIBLE",
    "CONFIRMED_NOT_FEASIBLE",
    "CONFIRMED_SCHEDULE_MISMATCH",
    "WAIVED",
]
CustomerPermission = Literal["ALLOWED", "FORBIDDEN", "UNSTATED"]
QuestionStatus = Literal["OPEN", "REPLIED"]
QuestionIssue = Literal[
    "EMPTY_VALUE",
    "CONFLICT",
    "SCOPE_UNCLEAR",
    "CONDITION_MISSING",
    # 내부 자문 질문(target=INTERNAL)에만 쓴다(명세 v1.3 §4 #19).
    "RND_FEASIBILITY",
    "TECH_ADVICE",
    "PURCHASE_TERMS",
]
INTERNAL_ISSUES = frozenset({"RND_FEASIBILITY", "TECH_ADVICE", "PURCHASE_TERMS"})
QuestionTarget = Literal["CUSTOMER", "INTERNAL"]
# 명세 v1.3 §7: 조립품의 조립·시험·연동·보증 책임자. 사람이 정하고 기본은 미확보다.
IntegrationHolder = Literal["OWN", "SINGLE_PARTNER", "JOINT", "UNRESOLVED"]
# 명세 v1.3 §5.1: 지정품 처리 방식. 기본은 지정품 조달이고 바꾸는 입력은 변경 제안이다.
DesignatedProcurement = Literal["SPECIFIED_PROCUREMENT", "ALTERNATIVE_PROPOSAL", "UNDECIDED"]
RndFeasibility = Literal["FEASIBLE", "FEASIBLE_WITH_SCHEDULE", "NOT_FEASIBLE", "UNDETERMINED"]
DepartmentKind = Literal["SALES", "RND", "TECH", "PURCHASE", "OTHER"]
ProposalOrigin = Literal["web", "customer_reply", "pdf_annot"]
ProposalStatus = Literal["PENDING", "APPROVED", "REJECTED"]
ProposerKind = Literal["user", "system"]
JobKind = Literal["rfp.process", "rfp.export", "rfp.annotate"]
JobStep = Literal[
    "parse",
    "extract",
    "search",
    "compare",
    "supplement",
    "candidates",
    "draft",
    "export",
    "annotate",
]
DocumentKind = Literal["RFP_SPEC", "DRAWING", "ADDENDUM", "DATASHEET", "ANNOTATED_PDF"]
ClauseFlag = Literal["HIGHLIGHT", "TEMPLATE_PLACEHOLDER", "LINE_BREAK_REJOINED"]
Operator = Literal["EQ", "GTE", "LTE", "RANGE", "TEXT"]
StandardSystem = Literal["IEC", "IEEE", "UL", "NEMA", "NONE"]
NextOwner = Literal["SALES", "TECH", "PURCHASE", "CUSTOMER", "RND"]
ComparisonResult = Literal["MEETS", "DIFFERS", "NOT_COMPARABLE", "NO_DATA"]
DraftSection = Literal[
    "CUSTOMER_GOAL_SCOPE",
    "OUR_RESPONSE",
    "DEVIATIONS",
    "PARTNER_SUPPLEMENT",
    "ASSUMPTIONS_EXCLUSIONS",
    "QUESTIONS_NEXT_ACTIONS",
]
EvidenceType = Literal["requirement", "product", "partner", "clause", "question"]
ExportFormat = Literal["DOCX", "PDF"]
Purpose = Literal["INITIAL_PROPOSAL"]
ConfirmationStatus = Literal["UNCONFIRMED", "CONTACT_PLANNED", "CONFIRMED"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provenance(Model):
    """모든 업무 행에 붙는 판본과 출처 축."""

    version: int = Field(ge=1)
    data_origin: DataOrigin
    derivation_kind: DerivationKind
    known_at: datetime


# --- 프로젝트 (§4.1) --------------------------------------------------------------------


class ProjectCreate(Model):
    name: str = Field(min_length=1, max_length=200)
    client_name: str | None = Field(default=None, max_length=200)
    company_code: str = Field(min_length=1, max_length=40)
    purpose: Purpose = "INITIAL_PROPOSAL"
    customer_goal: str | None = Field(default=None, max_length=4000)
    review_departments: list[str] = Field(default_factory=list, max_length=20)
    # 기본값 없이 호출자가 밝힌다.
    data_origin: DataOrigin
    # 영업 입력 표시값(가설). 상태·등급·경로 순위·검토본 조건·평가 어디에도 입력으로 쓰지 않는다(명세 v1.3 §6).
    strategic_customer: bool | None = None
    strategic_customer_note: str | None = Field(default=None, min_length=1, max_length=200)


class IntegrationUpdate(Model):
    """통합 책임 지정(사람의 결정, 바로 human_input 판본). required·근거 조항은 서버가 계산한다."""

    holder: IntegrationHolder
    holder_candidate_codes: list[str] = Field(default_factory=list, max_length=10)
    note: str | None = Field(default=None, min_length=1, max_length=1000)


class ProjectUpdate(Model):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    client_name: str | None = Field(default=None, max_length=200)
    customer_goal: str | None = Field(default=None, max_length=4000)
    review_departments: list[str] | None = Field(default=None, max_length=20)
    # 본문에 있는 필드만 바꾼다(strategic_customer: null은 미입력으로 되돌림).
    strategic_customer: bool | None = None
    strategic_customer_note: str | None = Field(default=None, min_length=1, max_length=200)
    integration_responsibility: IntegrationUpdate | None = None


class Assignment(Model):
    version: int = Field(ge=1)
    sales_user_id: UUID | None
    responsible_user_id: UUID | None
    reason: str = Field(min_length=1, max_length=500)


class RequirementCounts(Model):
    by_status: dict[RequirementStatus, int]
    by_necessity: dict[Necessity, int]
    needs_rereview: int
    open_questions: int


class PendingByOrigin(Model):
    web: int
    customer_reply: int
    pdf_annot: int


class JobRef(Model):
    job_id: UUID
    status: str
    step: JobStep | None


class IntegrationSummary(Model):
    holder: IntegrationHolder = "UNRESOLVED"


class IntegrationResponsibility(IntegrationSummary):
    """명세 v1.3 §7. required·basis_clause_ids는 서버 계산(rfp/integration.py), 나머지는 사람 입력."""

    required: bool = False
    holder_candidate_codes: list[str] = Field(default_factory=list)
    basis_clause_ids: list[UUID] = Field(default_factory=list)
    note: str | None = None
    set_by: UUID | None = None
    set_at: datetime | None = None


class RfpProjectSummary(Provenance):
    id: UUID
    name: str
    client_name: str | None
    company_code: str
    output_status: OutputStatus
    judgement_status: JudgementStatus
    sales_user_id: UUID | None
    responsible_user_id: UUID | None
    requirements_count: int
    unresolved_mandatory_count: int
    pending_proposals_count: int
    created_at: datetime
    # 명세 v1.3 §4 #1: 화면은 "전략 고객(영업 입력, 가설)" 배지로만 보여 준다.
    strategic_customer: bool | None = None
    integration_responsibility: IntegrationSummary = Field(default_factory=IntegrationSummary)


class RfpProjectList(Model):
    items: list[RfpProjectSummary]
    next_cursor: str | None


class RfpProjectDetail(RfpProjectSummary):
    purpose: Purpose
    customer_goal: str | None
    review_departments: list[str]
    requirement_counts: RequirementCounts
    pending_proposals_by_origin: PendingByOrigin
    latest_job: JobRef | None
    draft_version: int | None
    review_version: int | None
    documents_count: int
    strategic_customer_note: str | None = None
    strategic_customer_set_by: UUID | None = None
    integration_responsibility: IntegrationResponsibility = Field(
        default_factory=IntegrationResponsibility
    )


# --- 문서·조항 (§4.2) -------------------------------------------------------------------


class DocumentVersion(Provenance):
    document_id: UUID
    document_version_id: UUID
    kind: DocumentKind
    title: str
    mime: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    # DOCX는 편집 흔적(취소선)을 읽을 수 있고 PDF는 확인 불가다.
    edit_trace_support: bool
    # 새 판본 업로드(supersedes_document_id)는 같은 document_id 아래 version+1로 쌓인다.
    base_document_id: UUID | None
    uploaded_by: UUID
    uploaded_at: datetime


class DocumentList(Model):
    items: list[DocumentVersion]


class DocumentDetail(DocumentVersion):
    clauses_count: int
    deleted_values_count: int


class DeletedValue(Model):
    """취소선으로 지운 값. 현행 텍스트에 섞지 않는다("5033%" 금지)."""

    text: str
    position: int = Field(ge=0)


class CrossReference(Model):
    target: str
    resolved_clause_id: UUID | None


class Clause(Model):
    clause_id: UUID
    clause_no: str
    parent_clause_id: UUID | None
    depth: int = Field(ge=0)
    title: str | None
    text: str
    page: int | None
    deleted_values: list[DeletedValue]
    flags: list[ClauseFlag]
    cross_references: list[CrossReference]
    requirement_ids: list[UUID]


class ClauseList(Model):
    items: list[Clause]
    next_cursor: str | None


# --- 처리 작업 (§4.3) -------------------------------------------------------------------


class ProcessRequest(Model):
    document_version_ids: list[UUID] = Field(default_factory=list)
    steps: list[JobStep] = Field(default_factory=list)
    # 두 번째 실행부터 필수(서버가 검사한다).
    reason: str | None = Field(default=None, max_length=500)


class JobAccepted(Model):
    job_id: UUID
    status_url: str
    # 새 작업은 QUEUED, 같은 Idempotency-Key 재전송은 기존 작업의 지금 상태다(플랫폼 작업 상태 6값).
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]


# --- 요구 (§4.4) ------------------------------------------------------------------------


class Condition(Model):
    name: str
    value: str
    unit: str | None


class RequirementOrigin(Model):
    document_version_id: UUID
    clause_id: UUID | None
    clause_no: str | None
    page: int | None
    quote: str
    deleted_values: list[DeletedValue]
    edit_trace_support: bool


class DesignatedItem(Model):
    manufacturer: str | None
    model: str
    clause_id: UUID | None
    procurement: DesignatedProcurement = "SPECIFIED_PROCUREMENT"
    approval_question_id: UUID | None = None


class RequirementSpec(Model):
    equipment: str | None
    task: str | None
    attribute: str | None
    value: str | None
    unit: str | None
    operator: Operator
    conditions: list[Condition]
    standard_system: StandardSystem
    necessity: Necessity
    # 타사 지정 모델. 없으면 null.
    designated_model: str | None
    undetermined_items: list[str]
    # 명세 v1.3 §5.1: designated_model이 null이면 null이다. 추출(KAN-185)이 채운다.
    designated_item: DesignatedItem | None = None


class Judgement(Model):
    status: RequirementStatus
    reason: str
    unresolved: list[str]
    path: SupplementPath | None
    next_owner_field: NextOwner | None
    provenance_kind: ProvenanceKind
    decided_by: UUID | None
    decided_at: datetime | None
    # 명세 v1.3 §5.2: R&D 질문·답변에서 서버가 계산한다. 열린 R&D 질문(없으면 마지막 R&D 질문).
    rnd_check: RndCheck = "NOT_REQUIRED"
    rnd_question_id: UUID | None = None


class RequirementLinks(Model):
    comparison_summary: str | None
    supplement_summary: str | None
    partner_candidate_ids: list[str]
    question_ids: list[UUID]
    pending_proposal_ids: list[UUID]
    draft_sentence_ids: list[UUID]


class RequirementSummary(Model):
    requirement_id: UUID
    code: str
    equipment: str | None
    task: str | None
    attribute: str | None
    value_text: str | None
    necessity: Necessity
    status: RequirementStatus
    path: SupplementPath | None
    open_questions: int
    needs_rereview: bool
    version: int
    rnd_check: RndCheck = "NOT_REQUIRED"
    is_designated_item: bool = False


class RequirementList(Model):
    items: list[RequirementSummary]
    next_cursor: str | None


class RequirementDetail(Provenance):
    requirement_id: UUID
    code: str
    origin: RequirementOrigin
    spec: RequirementSpec
    judgement: Judgement
    links: RequirementLinks
    needs_rereview: bool


# --- 자사 비교 (§4.5) -------------------------------------------------------------------


class ComparisonEvidence(Model):
    document: str
    clause: str | None
    version: str | None
    applicability: str | None
    limits: str | None


class AttributeMatch(Model):
    matched_attribute: str | None
    our_value: str | None
    our_unit: str | None
    our_conditions: list[Condition]
    # 같은 속성·단위·조건끼리만 비교한다. 단시간·순시 내전류, 내부 아크처럼 다르면 false.
    comparable: bool
    result: ComparisonResult
    evidence: list[ComparisonEvidence]


class ComparisonCandidate(Model):
    product_code: str
    variant: str | None
    scope: list[str]
    lifecycle_status: str | None
    attribute_match: AttributeMatch


class SearchTrace(Model):
    filters: list[str]
    documents_searched: int
    search_failed: bool


class Comparison(Provenance):
    requirement_id: UUID
    requirement_version: int
    candidates: list[ComparisonCandidate]
    search_trace: SearchTrace
    provenance_kind: ProvenanceKind


# --- 보완 경로·협력사 (§4.6) ------------------------------------------------------------


class OptionGate(Model):
    """협력사 계열 경로는 자사 개발 확인(R&D)을 거친다(명세 v1.3 §5.2)."""

    requires: Literal["OWN_DEVELOPMENT_CHECK", "NONE"]
    status: RndCheck


class SupplementOption(Model):
    path: SupplementPath
    # path_rank: 1 자사 계열 · 2 자사 개발 · 3 협력사 계열 · 4 지정품 조달 · 9 없음. 저장하지 않는 계산값이다.
    rank: int
    gate: OptionGate
    # 고객이 금지한 경로, 지정품 요구의 협력사 경로(DESIGNATED_ITEM), 자사 개발이 가능한데 협력사 경로
    # (OWN_DEVELOPMENT_FEASIBLE)는 false로 내고 추천하지 않는다.
    allowed: bool
    conditions_to_confirm: list[str]
    required_expertise: list[str]
    candidate_codes: list[str]
    blocked_reason: str | None


class SupplementOptions(Provenance):
    requirement_id: UUID
    deficiency_cause: DeficiencyCause | None
    customer_permission: CustomerPermission
    permission_clause_id: UUID | None
    options: list[SupplementOption]


class SupplementSelection(Model):
    version: int = Field(ge=1)
    path: SupplementPath
    candidate_codes: list[str] = Field(default_factory=list, max_length=10)
    role_split: str = Field(min_length=1, max_length=2000)
    conditions: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=1000)
    # 열린 R&D 질문이 있는데 협력사 계열 경로를 고를 때만 쓴다(명세 v1.3 §5.2, 20자 이상).
    rnd_check_waiver_reason: str | None = Field(default=None, min_length=20, max_length=1000)


class SupplementSelected(Provenance):
    requirement_id: UUID
    path: SupplementPath
    candidate_codes: list[str]
    role_split: str
    conditions: list[str]
    note: str | None
    provenance_kind: ProvenanceKind
    decided_by: UUID
    decided_at: datetime


class MandatoryCheck(Model):
    passed: list[str]
    failed: list[str]
    unknown: list[str]


class CandidateGrade(Model):
    requirement_id: UUID
    # 후보 × 요구마다 계산한 등급. 유사도 점수는 내지 않는다.
    grade: Grade
    reasons: list[str]
    exclusion_reasons: list[str]
    mandatory_check: MandatoryCheck


class PartnerCandidate(Model):
    candidate_code: str
    display_name: str
    profile_source: Literal["synthetic"]
    profile_version: int = Field(ge=1)
    scope: list[Scope]
    spec_facts_attributes: list[Attribute]
    certifications: list[str]
    integrator_capable: dict[str, bool | None]
    on_customer_manufacturer_list: bool | None
    # KAN-184 프로필의 납기 객체 그대로(partner_profiles.lead_time jsonb). 요약 문자열로 바꾸지 않는다.
    lead_time: LeadTime | None
    warranty: str | None
    confirmation_status: ConfirmationStatus
    overall_grade: Grade
    conditions_to_confirm: list[str]
    grades: list[CandidateGrade]


class PartnerCandidateList(Model):
    items: list[PartnerCandidate]


# --- 고객 질문 (§4.7) -------------------------------------------------------------------


class ClauseRef(Model):
    clause_id: UUID | None
    page: int | None


class InternalAssumption(Model):
    """회신 전 내부 가정. derivation_kind=ASSUMED로 저장하고 충족 판정에 쓰지 않는다."""

    value: str
    basis_standard: str | None
    basis_clause: str | None
    approver_user_id: UUID | None
    shown_in_draft: bool


class QuestionCreate(Model):
    requirement_id: UUID | None
    origin: ClauseRef
    issue_type: QuestionIssue
    question_text: str = Field(min_length=1, max_length=4000)
    internal_assumption: InternalAssumption | None = None
    # 명세 v1.3 §5.3: INTERNAL은 프로젝트 검토 부서 안의 target_dept가 필요하다(서버가 범위를 검사한다).
    target: QuestionTarget = "CUSTOMER"
    target_dept: str | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def check_target(self) -> Self:
        if (self.target == "INTERNAL") != (self.target_dept is not None):
            raise ValueError("target_dept is required for INTERNAL questions only")
        if self.issue_type in INTERNAL_ISSUES and self.target != "INTERNAL":
            raise ValueError("This issue type is for internal questions")
        return self


class QuestionReply(Model):
    version: int = Field(ge=1)
    reply_text: str = Field(min_length=1, max_length=20000)
    received_at: datetime
    # 메일 ID 등 참조만. 메일 본문은 저장하지 않는다.
    source_ref: str | None = Field(default=None, max_length=512)
    # R&D 부서 대상 내부 질문에만 받는다(아니면 422 TFT_QUESTION_FIELD_NOT_ALLOWED, rnd.check_reply_fields).
    rnd_feasibility: RndFeasibility | None = None
    earliest_available_on: date | None = None
    schedule_fits: bool | None = None


class Question(Provenance):
    question_id: UUID
    requirement_id: UUID | None
    origin: ClauseRef
    issue_type: QuestionIssue
    question_text: str
    internal_assumption: InternalAssumption | None
    status: QuestionStatus
    reply_received_at: datetime | None
    created_by: UUID
    created_at: datetime
    target: QuestionTarget = "CUSTOMER"
    target_dept: str | None = None
    rnd_feasibility: RndFeasibility | None = None


class QuestionList(Model):
    items: list[Question]


# --- 변경 제안 (§4.8) -------------------------------------------------------------------


class ProposedChange(Model):
    status: RequirementStatus | None = None
    necessity: Necessity | None = None
    reason: str | None = None
    conditions: list[Condition] | None = None
    unresolved: list[str] | None = None


class ProposalCreate(Model):
    requirement_id: UUID
    proposed: ProposedChange
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class ProposalDecision(Model):
    version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=1000)


class ProposalRejection(Model):
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class Proposal(Provenance):
    proposal_id: UUID
    origin: ProposalOrigin
    source_ref: dict[str, str | int | float | None] | None
    requirement_id: UUID | None
    # 조항에 맞출 수 없는 메모(pdf_annot)는 요구 없이 남긴다.
    clause_unresolved: bool
    current: ProposedChange
    proposed: ProposedChange
    rationale: str
    proposer_user_id: UUID | None
    proposer_kind: ProposerKind
    status: ProposalStatus
    decided_by: UUID | None
    decided_at: datetime | None


class ProposalList(Model):
    items: list[Proposal]


class QuestionReplyResult(Question):
    """20. 회신 결과: 회신된 질문. CUSTOMER 회신이 판단을 바꿀 제안을 만들었을 때만 created_proposal이 있다."""

    created_proposal: Proposal | None = None


# --- 초안·검토본 (§4.9) -----------------------------------------------------------------


class Evidence(Model):
    type: EvidenceType
    id: str


class Sentence(Model):
    sentence_id: UUID
    text: str
    evidence: list[Evidence]
    conditions: list[str]
    # 확약되지 않은 역할·조건을 담은 문장.
    unconfirmed: bool
    needs_rereview: bool
    evidence_missing: bool
    provenance_kind: ProvenanceKind


class Section(Model):
    section: DraftSection
    sentences: list[Sentence]


class Draft(Provenance):
    project_id: UUID
    sections: list[Section]
    # 고정 문구: 시연용 검토본이며 실제 제안서가 아니다.
    disclaimer: str
    evidence_missing_count: int


class SentenceEdit(Model):
    sentence_id: UUID | None
    text: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=500)
    section: DraftSection | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class DraftEdit(Model):
    version: int = Field(ge=1)
    edits: list[SentenceEdit] = Field(min_length=1, max_length=50)


class ReviewVersionCreate(Model):
    version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=1000)


class ReviewVersion(Model):
    project_id: UUID
    review_version: int
    draft_version: int
    output_status: OutputStatus
    judgement_status: JudgementStatus
    saved_by: UUID
    saved_at: datetime


class ExportRequest(Model):
    review_version: int = Field(ge=1)
    format: ExportFormat


class AnnotationProcess(Model):
    document_version_id: UUID
    base_document_version_id: UUID
