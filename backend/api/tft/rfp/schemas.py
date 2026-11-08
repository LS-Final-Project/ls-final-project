"""RFP 자사 대응·협력사 보완의 상태 값과 HTTP 모델(KAN-182, A01).

원본 합의는 "RFP 자사 대응·협력사 보완 API 명세 v1.1"(contracts/tft/rfp-data.md에서 링크)이다.
이 파일이 OpenAPI 초안의 원본이며, 경로 구현은 KAN-188(A07) 등 각 작업이 한다.

- 요구의 판단은 점수 하나가 아니라 네 가지 상태(`RequirementStatus`)로 둔다.
- 모든 업무 행은 판본(`version`)과 두 출처 축(`data_origin`, `derivation_kind`), `known_at`을 가진다.
- 사람 판단과 AI 결과는 `provenance_kind`로 구분한다.
"""

from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from backend.api.tft.partners.grading import Grade
from backend.api.tft.partners.schemas import Attribute, LeadTime, ProfileSource, Scope
from backend.api.tft.rfp.integration import FallbackPath
from backend.engine.documents.diagrams import DiagramGraph
from backend.engine.rfp.vocabulary import AttributeKeyOrOther, FamilyOrOther

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
# DRAFT: 처리 함수가 만든 초안. 사람이 확인(경로 31)해야 OPEN이 되고, 그 전에는 보낸 질문이 아니다.
QuestionStatus = Literal["DRAFT", "OPEN", "REPLIED"]
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
JobKind = Literal["rfp.process", "rfp.export", "rfp.annotate", "rfp.draft_rewrite", "rfp.context"]
JobStep = Literal[
    "context_map",
    "context_link",
    "context_validate",
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
# XLSX: 편차표·질의서·현황표·요구별 네 시트(KAN-221). PDF는 받지 않는다(422).
ExportFormat = Literal["DOCX", "PDF", "XLSX"]
Purpose = Literal["INITIAL_PROPOSAL"]
ConfirmationStatus = Literal["UNCONFIRMED", "CONTACT_PLANNED", "CONFIRMED"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextProcess(Model):
    """Reorganize existing requirements; never upload or re-extract the source."""

    reason: str | None = Field(default=None, max_length=2000)


class Provenance(Model):
    """모든 업무 행에 붙는 판본과 출처 축."""

    version: int = Field(ge=1)
    data_origin: DataOrigin
    derivation_kind: DerivationKind
    known_at: datetime


OffListPermission = Literal["ALLOWED", "FORBIDDEN", "UNSTATED"]
ConstraintText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
ManufacturerList = Annotated[list[ConstraintText], Field(min_length=1, max_length=100)]


# --- 프로젝트 (§4.1) --------------------------------------------------------------------


class ProjectCreate(Model):
    supply_region: ConstraintText | None = None
    customer_manufacturer_list: ManufacturerList | None = None
    off_list_permission: OffListPermission = "UNSTATED"
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
    # KAN-228: 제안서 제출 마감(입력 안 하면 null).
    submission_due_at: date | None = None


class IntegrationUpdate(Model):
    """통합 책임 지정(사람의 결정, 바로 human_input 판본). required·근거 조항은 서버가 계산한다."""

    holder: IntegrationHolder
    holder_candidate_codes: list[str] = Field(default_factory=list, max_length=10)
    note: str | None = Field(default=None, min_length=1, max_length=1000)


class ProjectUpdate(Model):
    supply_region: ConstraintText | None = None
    customer_manufacturer_list: ManufacturerList | None = None
    off_list_permission: OffListPermission = Field(
        default_factory=lambda: "UNSTATED",
        description="생략하면 기존 값을 보존합니다. UNSTATED를 명시하면 미정으로 변경합니다.",
    )
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    client_name: str | None = Field(default=None, max_length=200)
    customer_goal: str | None = Field(default=None, max_length=4000)
    review_departments: list[str] | None = Field(default=None, max_length=20)
    # 본문에 있는 필드만 바꾼다(strategic_customer: null은 미입력으로 되돌림).
    strategic_customer: bool | None = None
    strategic_customer_note: str | None = Field(default=None, min_length=1, max_length=200)
    integration_responsibility: IntegrationUpdate | None = None
    # null이면 마감을 지운다.
    submission_due_at: date | None = None


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
    """최신 처리 작업. 상태는 플랫폼 소유라 tft 응답에서는 비어 있다(작업 상태 경로로 읽는다)."""

    job_id: UUID
    status: str | None
    step: JobStep | None


class IntegrationSummary(Model):
    holder: IntegrationHolder = "UNRESOLVED"


class IntegrationResponsibility(IntegrationSummary):
    """명세 v1.3 §7. required·basis_clause_ids는 서버 계산(rfp/integration.py), 나머지는 사람 입력."""

    required: bool = False
    holder_candidate_codes: list[str] = Field(default_factory=list)
    basis_clause_ids: list[UUID] = Field(default_factory=list)
    # 사람이 근거 조항을 볼 수 있게 basis_clause_ids와 같은 길이·순서로 조항 번호를 낸다(서버 계산).
    basis_clause_nos: list[str] = Field(default_factory=list)
    fallback_paths: list[FallbackPath] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    note: str | None = None
    set_by: UUID | None = None
    set_at: datetime | None = None


class RfpProjectSummary(Provenance):
    supply_region: ConstraintText | None = None
    customer_manufacturer_list: ManufacturerList | None = None
    off_list_permission: OffListPermission = "UNSTATED"
    id: UUID
    # 접수 표시 번호(P26-0001). 번호 발급 전 프로젝트는 null이다.
    external_id: str | None = None
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
    open_questions_count: int
    created_at: datetime
    # 명세 v1.3 §4 #1: 화면은 "전략 고객(영업 입력, 가설)" 배지로만 보여 준다.
    strategic_customer: bool | None = None
    integration_responsibility: IntegrationSummary = Field(default_factory=IntegrationSummary)
    submission_due_at: date | None = None


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
    decision_gate: "DecisionGate" = Field(default_factory=lambda: DecisionGate())


# --- 문서·조항 (§4.2) -------------------------------------------------------------------


# 저장 행에서 그대로 옮기는 프로젝트 칸. 집계 칸은 routes_write.project_detail이 채운다.
PROJECT_COLUMNS = (
    "submission_due_at",
    "external_id",
    "id",
    "version",
    "data_origin",
    "derivation_kind",
    "known_at",
    "name",
    "client_name",
    "company_code",
    "output_status",
    "judgement_status",
    "sales_user_id",
    "responsible_user_id",
    "created_at",
    "strategic_customer",
    "supply_region",
    "customer_manufacturer_list",
    "off_list_permission",
    "purpose",
    "customer_goal",
    "review_departments",
    "draft_version",
    "review_version",
    "documents_count",
    "requirements_count",
    "unresolved_mandatory_count",
    "pending_proposals_count",
    "open_questions_count",
    "strategic_customer_note",
    "strategic_customer_set_by",
)


class UploadForm(Model):
    """경로 6 multipart 폼 칸(routes.UPLOAD_BODY와 같다). 파일은 따로 읽는다."""

    kind: DocumentKind
    title: str = Field(min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=2000)
    supersedes_document_id: UUID | None = None
    base_document_id: UUID | None = None
    data_origin: DataOrigin


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
    extraction_warnings: list[str] = Field(default_factory=list)


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


class ExportAccepted(JobAccepted):
    """DOCX 접수 결과의 파일 식별자. process/annotate의 응답은 바꾸지 않는다."""

    export_id: UUID


# --- 요구 (§4.4) ------------------------------------------------------------------------


class Condition(Model):
    name: str
    value: str
    unit: str | None


class OwnValue(Model):
    """자사 보증값(KAN-227). 저장된 비교의 대표 후보에서 읽는다(충족 → 다름 → 비교 불가 → 자료 없음)."""

    product_code: str
    variant: str | None
    value: str
    unit: str | None
    conditions: list[Condition]
    result: Literal["MEETS", "DIFFERS", "NOT_COMPARABLE", "NO_DATA"]
    # "형식 미확인": 요구의 변압기 절연 형식(건식·유입)을 몰라 형식이 다른 후보가 함께 남은 경우(KAN-217).
    note: str | None = None


class RequirementGroup(Model):
    """요구가 속한 설비 묶음(KAN-231). 최신 맥락 결과(KAN-216)의 묶음 ID·원문 제목 태그·이름."""

    group_id: str
    tag: str | None
    name: str


class DuplicateInfo(Model):
    """원문 여러 곳에 반복된 같은 요구(KAN-223). 대표만 결정하고 구성원은 대표를 따른다(KAN-231)."""

    representative_id: UUID
    member_count: int
    is_representative: bool


# 설비 계열 묶음(KAN-231): 중전압·변압기·저압·그 밖의 계열·공통(계열 없음, 과업, 제출물).
Bucket = Literal["MV", "TR", "LV", "OTHER", "COMMON"]


class Equipment(Model):
    """프로젝트 설비 하나(KAN-227). tag는 원문 제목의 설비 태그(KAN-216), 없으면 null."""

    tag: str | None
    name: str


class EquipmentList(Model):
    # context: 최신 맥락 결과의 설비 묶음. requirements: 맥락이 없어 요구 판본의 설비 칸을 모은 것.
    source: Literal["context", "requirements"]
    items: list[Equipment]


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
    # 비교 어휘 키. 화면 라벨용이고 원문 표현은 attribute다.
    attribute_key: str | None = None
    value: str | None
    unit: str | None
    # 원문 허용 오차 표기 그대로. null은 '없음'이 아니라 없거나 T15 전 판본(모름)이다.
    tolerance: str | None = None
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
    # 비교 어휘 키. 화면 라벨용이고 원문 표현은 attribute다.
    attribute_key: str | None = None
    value_text: str | None
    necessity: Necessity
    status: RequirementStatus
    path: SupplementPath | None
    open_questions: int
    needs_rereview: bool
    # 시스템 표시(추출 허용 오차 불일치·목록 밖 키 등). 요구 내용이 아니라 사람이 볼 요구를 가리킨다.
    needs_review: bool = False
    version: int
    provenance_kind: ProvenanceKind
    rnd_check: RndCheck = "NOT_REQUIRED"
    is_designated_item: bool = False
    bid_verdict: "BidVerdict | None" = None
    decision: "DecisionState | None" = None
    # KAN-227: 비교에 쓴 자사 값. 비교가 없거나 자사 값이 없으면 null.
    own_value: OwnValue | None = None
    # KAN-231: 설비 계열 묶음은 늘 있다. 묶음·중복·context_stale은 맥락이 없으면 null(규칙은 rfp-data.md).
    bucket: Bucket = "COMMON"
    group: RequirementGroup | None = None
    duplicate: DuplicateInfo | None = None
    context_stale: bool | None = None


class RequirementList(Model):
    items: list[RequirementSummary]
    next_cursor: str | None


class RequirementWorkflow(Model):
    comparison_stale: bool = False
    supplement_stale: bool = False
    draft_sentence_ids: list[UUID] = Field(default_factory=list)


class RequirementDetail(Provenance):
    workflow: RequirementWorkflow = Field(default_factory=RequirementWorkflow)
    requirement_id: UUID
    code: str
    origin: RequirementOrigin
    spec: RequirementSpec
    judgement: Judgement
    links: RequirementLinks
    needs_rereview: bool
    needs_review: bool = False
    bid_verdict: "BidVerdict | None" = None
    decision: "DecisionState | None" = None
    # KAN-227: 비교에 쓴 자사 값. 비교가 없거나 자사 값이 없으면 null.
    own_value: OwnValue | None = None
    # KAN-231: 설비 계열 묶음은 늘 있다. 묶음·중복·context_stale은 맥락이 없으면 null(규칙은 rfp-data.md).
    bucket: Bucket = "COMMON"
    group: RequirementGroup | None = None
    duplicate: DuplicateInfo | None = None
    context_stale: bool | None = None


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


class SupplementBatchItem(Model):
    requirement_id: UUID
    version: int = Field(ge=1)


class SupplementBatchSelection(Model):
    """보완 경로 묶음 선택. 건마다 읽은 요구 판본을 받고 나머지 칸은 단건 선택과 같은 규칙이다."""

    dry_run: bool
    path: SupplementPath
    candidate_codes: list[str] = Field(default_factory=list, max_length=10)
    role_split: str = Field(min_length=1, max_length=2000)
    conditions: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=1000)
    rnd_check_waiver_reason: str | None = Field(default=None, min_length=20, max_length=1000)
    items: list[SupplementBatchItem] = Field(min_length=1, max_length=50)


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


class SupplementBatchAllowed(Model):
    requirement_id: UUID


class SupplementBatchSaved(Model):
    requirement_id: UUID
    selection: SupplementSelected


class SupplementBatchBlocked(Model):
    requirement_id: UUID
    code: str
    message: str


class SupplementBatchResult(Model):
    """dry_run이면 saved가 비고 allowed에 담긴다. 저장이면 allowed가 비고 saved에 담긴다."""

    saved: list[SupplementBatchSaved]
    allowed: list[SupplementBatchAllowed]
    blocked: list[SupplementBatchBlocked]


class MandatoryCheck(Model):
    passed: list[str]
    failed: list[str]
    unknown: list[str]


class CandidateGrade(Model):
    requirement_version: int | None = None
    profile_version: int | None = None
    version: int | None = None
    known_at: datetime | None = None
    requirement_id: UUID
    # 후보 × 요구마다 계산한 등급. 유사도 점수는 내지 않는다.
    grade: Grade
    reasons: list[str]
    exclusion_reasons: list[str]
    mandatory_check: MandatoryCheck


class PartnerCandidate(Model):
    candidate_code: str
    display_name: str
    profile_source: ProfileSource
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
    input_project_version: int | None = None
    manufacturer_list_basis: Literal["CURRENT_PROJECT_INPUT"] = "CURRENT_PROJECT_INPUT"
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
    # 후속 질의(TQ-1-2): 같은 프로젝트의 원래 질의(KAN-224).
    follows_question_id: UUID | None = None

    @model_validator(mode="after")
    def check_target(self) -> Self:
        if (self.target == "INTERNAL") != (self.target_dept is not None):
            raise ValueError("target_dept is required for INTERNAL questions only")
        if self.issue_type in INTERNAL_ISSUES and self.target != "INTERNAL":
            raise ValueError("This issue type is for internal questions")
        return self


class QuestionBatchCreate(Model):
    """질문 묶음 작성. 모든 건에 requirement_id가 있어야 한다(서버가 건별로 검사한다)."""

    items: list[QuestionCreate] = Field(min_length=1, max_length=50)


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


class RecordedReply(Model):
    reply_id: UUID
    reply_text: str
    source_ref: str | None
    received_at: datetime
    recorded_at: datetime
    recorded_by: UUID
    rnd_feasibility: RndFeasibility | None = None
    earliest_available_on: date | None = None
    schedule_fits: bool | None = None


class ReplyValueCandidate(Model):
    """회신에서 읽은 새 요구값 후보(KAN-224). 후보일 뿐 요구값을 바꾸지 않는다.

    method: rule(규칙), ai(AI, 근거 검사 통과), none(못 읽음, reason에 이유).
    """

    method: Literal["rule", "ai", "none"]
    value: str | None = None
    unit: str | None = None
    conditions: list[Condition] = Field(default_factory=list)
    quote: str | None = None
    reason: (
        Literal[
            "NO_VALUE",
            "MULTIPLE_VALUES",
            "CORRECTION",
            "CONDITIONAL",
            "MULTIPLE_QUESTIONS",
            "AI_UNGROUNDED",
            "AI_FAILED",
        ]
        | None
    ) = None
    # AI 읽기 작업(worker rfp.reply_read) 상태. QUEUED 대기, UNAVAILABLE 접수 못 함(다시 읽기 가능),
    # DONE 끝(method가 ai 또는 none), FAILED 모델 호출 실패. 규칙만 쓴 후보는 null이다.
    ai: Literal["QUEUED", "UNAVAILABLE", "DONE", "FAILED"] | None = None


class Question(Provenance):
    question_id: UUID
    requirement_id: UUID | None
    origin: ClauseRef
    issue_type: QuestionIssue
    question_text: str
    internal_assumption: InternalAssumption | None
    status: QuestionStatus
    reply_received_at: datetime | None
    reply: RecordedReply | None = None
    created_by: UUID
    created_at: datetime
    target: QuestionTarget = "CUSTOMER"
    target_dept: str | None = None
    rnd_feasibility: RndFeasibility | None = None
    follows_question_id: UUID | None = None
    # 요구에 이어진 CUSTOMER 질의의 회신에서 읽은 새 요구값 후보(KAN-224). 회신 전이나 대상 밖이면 없다.
    value_candidate: ReplyValueCandidate | None = None
    # KAN-228: 질의서를 메일로 처음 보낸 시각과, 보냈는데 회신이 없으면 보낸 뒤 지난 날수(질문 목록에서만 채운다).
    sent_at: datetime | None = None
    waiting_days: int | None = None


class QuestionList(Model):
    items: list[Question]


# --- 변경 제안 (§4.8) -------------------------------------------------------------------


class ProposedSpec(Model):
    value: str | None = Field(max_length=4000)
    unit: str | None = Field(max_length=128)
    operator: Operator
    tolerance: str | None = Field(max_length=1000)


class ProposedChange(Model):
    spec: ProposedSpec | None = None
    status: RequirementStatus | None = None
    necessity: Necessity | None = None
    reason: str | None = None
    conditions: list[Condition] | None = None
    unresolved: list[str] | None = None
    # 담당 부서(영업·기술·구매·고객·R&D). 값이 오면 승인한 판본의 next_owner_field가 된다.
    next_owner_field: NextOwner | None = None


class ProposalCreate(Model):
    requirement_id: UUID
    requirement_version: int | None = Field(default=None, ge=1)
    source_question_id: UUID | None = None
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
    evidence_refs: list[str] = Field(default_factory=list)
    current: ProposedChange
    proposed: ProposedChange
    rationale: str
    proposer_user_id: UUID | None
    proposer_kind: ProposerKind
    status: ProposalStatus
    decided_by: UUID | None
    decided_at: datetime | None
    # 승인 메모 또는 기각 사유. 결정 전이거나 메모 없이 승인했으면 null이다.
    decision_note: str | None = None


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
    template_text: str | None = Field(default=None, max_length=4000)
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


class DraftFreshness(Model):
    status: Literal["CURRENT", "STALE", "UNKNOWN"] = "UNKNOWN"
    changed_requirement_ids: list[UUID] = Field(default_factory=list)
    preserved_human_sentence_ids: list[UUID] = Field(default_factory=list)


class Draft(Provenance):
    freshness: DraftFreshness = Field(default_factory=DraftFreshness)
    project_id: UUID
    sections: list[Section]
    # 고정 문구: 검토용 초안입니다. 고객 제출 전 담당자 확인이 필요합니다.
    disclaimer: str
    evidence_missing_count: int
    # 근거 ID → 사람이 읽는 번호. 조회 응답에만 채우고 판본에 저장하지 않는다.
    evidence_labels: dict[str, str] = Field(default_factory=dict)


class SentenceEdit(Model):
    sentence_id: UUID | None
    text: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=500)
    section: DraftSection | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class DraftEdit(Model):
    version: int = Field(ge=1)
    edits: list[SentenceEdit] = Field(min_length=1, max_length=50)


class DraftRewrite(Model):
    version: int = Field(ge=1)


class RewriteSummary(Model):
    """모델 작성 결과. failures는 양식으로 남은 문장의 고정 사유 코드별 건수다."""

    job_id: UUID
    outcome: Literal["PENDING", "SUCCEEDED", "PARTIAL", "FAILED", "DISCARDED"]
    result_version: int | None
    model_sentences: int = 0
    template_sentences: int = 0
    human_sentences: int = 0
    failures: dict[str, int] = Field(default_factory=dict)


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


class ReviewVersionItem(Model):
    """검토본 이력 한 줄. tft에는 사용자 이름이 없어 이름은 null이다."""

    review_version: int
    draft_version: int
    note: str | None
    output_status: OutputStatus
    judgement_status: JudgementStatus
    saved_by: UUID
    saved_by_name: str | None = None
    saved_at: datetime


class ReviewVersionList(Model):
    items: list[ReviewVersionItem]


class DraftRegenerate(Model):
    """화면이 본 최신 초안 판본. 초안이 아직 없으면 0이다."""

    version: int = Field(ge=0)


class ExportRequest(Model):
    review_version: int = Field(ge=1)
    format: ExportFormat


class ExportItem(Model):
    """내보내기 이력 한 줄. tft는 완료만 기록하므로 끝나지 않은 작업은 QUEUED다."""

    export_id: UUID
    review_version: int
    format: ExportFormat
    status: Literal["QUEUED", "SUCCEEDED"]
    requested_by: UUID
    requested_by_name: str | None = None
    requested_at: datetime
    finished_at: datetime | None
    file_name: str | None


class ExportList(Model):
    items: list[ExportItem]


class AnnotationProcess(Model):
    document_version_id: UUID
    base_document_version_id: UUID


# Diagram candidates are source observations, never confirmed electrical topology.
class DiagramRequirementLink(Model):
    node_id: str
    requirement_id: UUID
    requirement_version: int = Field(ge=1)
    code: str
    equipment: str | None = None


class DiagramItem(Model):
    source_id: str
    location: str
    page: int | None
    mime: Literal["application/pdf", "image/png", "image/jpeg", "image/webp"]
    sha256: str
    size: int
    graph: DiagramGraph | None
    requirement_links: list[DiagramRequirementLink]


class DiagramList(Model):
    document_id: UUID
    document_version_id: UUID
    processing_status: Literal["NOT_ANALYZED", "ANALYZED", "PARTIAL"]
    warnings: list[str]
    items: list[DiagramItem]


class RecompareRequest(Model):
    version: int = Field(ge=1)


class RecompareResult(Model):
    comparison: Comparison
    proposal: Proposal | None = None


# --- 결정 패널 (KAN-219) -------------------------------------------------------------------

# 입찰 판정. 충족·충족 확인·편차·질의·보완·배정.
BidVerdict = Literal["MET", "CONFIRM", "DEVIATION", "QUERY", "SUPPLEMENT", "ASSIGN"]
DecisionAction = Literal[
    "confirm_met",
    "record_deviation",
    "request_check",
    "add_query",
    "select_partner",
    "ask_rnd",
    "assign",
    "change_verdict",
    "undo",
    "review_confirm",
    "review_return",
    # KAN-225: 요구에서 빼기·되살리기.
    "exclude_requirement",
    "restore_requirement",
    # KAN-227: 도면 출처 요구를 규격서와 맞는다고 확인. 다르면 질의서에 넣는다(add_query).
    "confirm_drawing",
]
# 기록에 남는 행동. 원문에서 직접 추가(KAN-225)는 결정 API가 아니라 요구 추가 경로로만 한다.
RecordedAction = Literal[DecisionAction, "add_requirement"]
# 과업 수행 주체. 공동 수행(JOINT_PROPOSAL)은 과업 요구의 보완 경로가 아니라 받지 않는다(supplement.FIRST_PATHS).
ExecutionHolder = Literal["OWN", "PARTNER"]
AssignOwner = Literal["SALES", "TECH", "PURCHASE"]
REQUIRED_BY_ACTION: dict[str, tuple[str, ...]] = {
    "record_deviation": ("text",),
    "request_check": ("text", "target_dept"),
    "select_partner": ("text", "candidate_codes"),
    "assign": ("holder", "owner"),
    "change_verdict": ("verdict",),
    "undo": ("decision_id",),
    "review_confirm": ("decision_id",),
    "review_return": ("decision_id",),
    # 빼는 사유는 필수다(text).
    "exclude_requirement": ("text",),
}


class DecisionRequest(Model):
    """결정 하나. 누르면 바로 확정한다(제안 작성과 승인을 같은 트랜잭션에서)."""

    version: int = Field(ge=1)
    action: DecisionAction
    # 편차 내용·확인 요청·질의 문장·협력사 역할. AI 초안을 사람이 고친 한 줄.
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    target_dept: str | None = Field(default=None, min_length=1, max_length=32)
    candidate_codes: list[str] = Field(default_factory=list, max_length=10)
    holder: ExecutionHolder | None = None
    owner: AssignOwner | None = None
    verdict: BidVerdict | None = None
    decision_id: UUID | None = None

    @model_validator(mode="after")
    def check_action_fields(self) -> Self:
        missing = [f for f in REQUIRED_BY_ACTION.get(self.action, ()) if not getattr(self, f)]
        if missing:
            raise ValueError(f"{self.action} needs {', '.join(missing)}")
        return self


class DecisionResult(Model):
    decision_id: UUID
    action: RecordedAction
    summary: str
    requirement_id: UUID
    requirement_version: int
    question_id: UUID | None = None


class DecisionState(Model):
    """요구의 결정 상태(KAN-220). 저장된 결정 기록에서 계산한다."""

    # 충족은 결정이 필요 없다. 그 밖의 판정은 지금 판본에 결정 기록이 있어야 결정함이다.
    needed: bool
    decided: bool
    decision_id: UUID | None = None
    action: RecordedAction | None = None
    summary: str | None = None
    # 사람이 적은 편차 내용(record_deviation). summary는 행동 이름("편차표에 기재")이라 편차표 칸에는 이것을 쓴다.
    note: str | None = None
    decided_at: datetime | None = None
    # 책임자가 출력 전에 확인했는지.
    reviewed: bool = False
    # KAN-225: 원문에서 사람이 직접 추가한 요구인지, 요구에서 뺐는지(빼도 지우지 않는다).
    manually_added: bool = False
    # KAN-227: 도면 OCR 조항에서 나온 요구. 판정과 관계없이 도면 확인(맞음 confirm_drawing, 다름 add_query)이 필요하다.
    drawing_check: bool = False
    excluded: bool = False
    excluded_reason: str | None = None
    # 추가·빼기·되살리기 중 마지막 것. 책임자 확인 대상이다(review_confirm의 decision_id로 확인).
    change_id: UUID | None = None
    change_action: RecordedAction | None = None
    change_reviewed: bool = False
    # KAN-231: 중복 묶음의 구성원이면 대표 요구 ID. 대표가 결정하고 이 요구는 결정 대상이 아니다(대표를 빼면 null).
    follows: UUID | None = None


# 일괄 결정(KAN-228)으로 할 수 있는 행동. 묶음 되돌리기가 늘 되는 결정 행동만 받는다.
BULK_ACTIONS = frozenset(
    {
        "confirm_met",
        "record_deviation",
        "request_check",
        "add_query",
        "select_partner",
        "ask_rnd",
        "assign",
        "change_verdict",
        # 책임자 "모두 확인"(2026-11-05). 확인은 판단을 바꾸지 않아 묶음 되돌리기 대상이 아니다.
        "review_confirm",
    }
)


class BulkDecisionItem(DecisionRequest):
    requirement_id: UUID

    @model_validator(mode="after")
    def check_bulk_action(self) -> Self:
        if self.action not in BULK_ACTIONS:
            raise ValueError(f"{self.action} cannot be applied in bulk")
        return self


class BulkDecisionRequest(Model):
    """같은 프로젝트의 결정 여러 건을 한 트랜잭션으로. 한 건이라도 실패하면 모두 저장하지 않는다."""

    items: list[BulkDecisionItem] = Field(min_length=1, max_length=200)


class BulkDecisionResult(Model):
    # 같은 사용자·Idempotency-Key면 같은 값이다. 묶음 되돌리기에 쓴다.
    batch_id: UUID
    items: list[DecisionResult]


class QuestionsSent(Model):
    """질의서를 메일로 보냈다는 기록(KAN-228). 질문마다 처음 보낸 시각만 남는다."""

    question_ids: list[UUID] = Field(min_length=1, max_length=200)
    # 플랫폼 메일 발송 ID 등. 없으면 비운다.
    mail_ref: str | None = Field(default=None, min_length=1, max_length=200)


class QuestionSend(Model):
    question_id: UUID
    sent_at: datetime
    sent_by: UUID
    mail_ref: str | None = None


class QuestionSendList(Model):
    items: list[QuestionSend]


class DecisionGate(Model):
    """검토본 저장 조건과 책임자 확인 대기(KAN-220). 0이면 통과다."""

    undecided: int = 0
    deviation_missing: int = 0
    open_queries: int = 0
    review_pending: int = 0
    # 담당 sales와 책임자가 같은 사람이면 결정한 사람이 확인도 한다. 확인 대기는 그대로 두고 화면에 "본인 결정"으로 표시한다.
    self_review: bool = False


class ManualRequirementRequest(Model):
    """원문에서 고른 글로 요구를 직접 추가한다(KAN-225). 값은 사람이 넣고 AI는 쓰지 않는다."""

    clause_id: UUID
    # 고른 글 그대로. 조항 현행 텍스트 안에 있어야 한다(추출과 같은 근거 검사, 공백·따옴표 정규화).
    quote: str = Field(min_length=1, max_length=4000)
    kind: Literal["SPEC", "TASK", "DOCUMENT"]
    equipment: str | None = Field(default=None, max_length=200)
    task: str | None = Field(default=None, max_length=200)
    attribute: str = Field(min_length=1, max_length=200)
    # 비교 어휘 키·제품 계열. 고르지 않으면 OTHER·None이고 자사 비교를 하지 않는다.
    attribute_key: AttributeKeyOrOther = "OTHER"
    equipment_family: FamilyOrOther | None = None
    # 값은 인용 안에 있어야 한다. 값 없는 요구(과업·제출물)는 null.
    value: str | None = Field(default=None, min_length=1, max_length=200)
    unit: str | None = Field(default=None, max_length=32)
    operator: Operator = "EQ"
    conditions: list[Condition] = Field(default_factory=list, max_length=10)
    necessity: Necessity = "MANDATORY"
    # 설비·속성·값이 같은 요구가 있어도 추가한다(첫 요청은 409 TFT_REQUIREMENT_DUPLICATE로 알린다).
    allow_duplicate: bool = False


RequirementSummary.model_rebuild()
RequirementDetail.model_rebuild()
RfpProjectDetail.model_rebuild()
