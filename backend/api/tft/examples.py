"""Validated synthetic examples, exported only for local frontend mocking.

The scenario follows the TFT proposal's demo flow (4.6): a submarine-cable RFP, a
design role with a confirmed candidate and a construction role whose only candidate has
onshore experience, so the required competency stays NEEDS_CHECK and the role is a gap.
Users are the local seed accounts (infra/local/seed_demo.sql); candidates are not users.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from backend.api.tft.projects.schemas import (
    AvailabilityCheck,
    CandidateRecommendation,
    CompetencyCheck,
    CompositionMember,
    CompositionVersion,
    ExperienceEvidence,
    ProjectDetail,
    ProjectList,
    ProjectStatus,
    ProjectSummary,
    RequiredRole,
    ResponsibleSummary,
    RfpRequirement,
    RoleCompetency,
    RoleRequirementLink,
    StaffingRequest,
)
from backend.shared.models import ResponseMeta, SuccessResponse

META = ResponseMeta(request_id=UUID("11111111-1111-4111-8111-111111111111"))
PROJECT_ID = UUID("33333333-3333-4333-8333-333333333333")
SECOND_PROJECT_ID = UUID("33333333-3333-4333-8333-333333333334")
CREATED_AT = datetime(2026, 10, 20, 9, 0, 0, tzinfo=UTC)

SALES_USER = UUID("00000000-0000-4000-8000-000000000003")
MANAGER_USER = UUID("00000000-0000-4000-8000-000000000004")
DEPT_USER = UUID("00000000-0000-4000-8000-000000000002")

DESIGN_ORG = UUID("66666666-6666-4666-8666-666666666601")
CONSTRUCTION_ORG = UUID("66666666-6666-4666-8666-666666666602")
REQ_INSTALL = UUID("77777777-7777-4777-8777-777777777701")
REQ_DESIGN = UUID("77777777-7777-4777-8777-777777777702")
ROLE_DESIGN = UUID("88888888-8888-4888-8888-888888888801")
ROLE_CONSTRUCTION = UUID("88888888-8888-4888-8888-888888888802")
COMPETENCY_REQUIRED = UUID("88888888-8888-4888-8888-888888888811")
COMPETENCY_PREFERRED = UUID("88888888-8888-4888-8888-888888888812")
REQUEST_DESIGN = UUID("99999999-9999-4999-8999-999999999901")
REQUEST_CONSTRUCTION = UUID("99999999-9999-4999-8999-999999999902")
CANDIDATE_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01")
CANDIDATE_D = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa04")
RECOMMEND_A = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb01")
RECOMMEND_D = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb04")
VERSION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccc01")

# Fixture rows (project, requirements, roles) are ASSUMED for the demo; stage records
# (requests, recommendations, checks, versions) are SYSTEM records of the workflow.


def summary(
    project_id: UUID,
    name: str,
    status: ProjectStatus,
    created_at: datetime,
    version: int | None,
) -> ProjectSummary:
    return ProjectSummary(
        id=project_id,
        external_id=None,
        name=name,
        company_code="HOST",
        status=status,
        responsible_user_id=SALES_USER if version else None,
        current_composition_version=version,
        created_at=created_at,
        data_origin="synthetic",
        derivation_kind="ASSUMED",
    )


def detail() -> ProjectDetail:
    requirements = [
        RfpRequirement(
            id=REQ_INSTALL,
            requirement_code="R-01",
            field_tag="CONSTRUCTION_TRANSPORT",
            title="해상 설치 및 매설 공사 포함",
            description="공급자는 해저케이블의 해상 운송, 설치와 매설을 수행한다.",
            document_name="합성 RFP 해저케이블 공급·설치 (예시)",
            document_id=None,
            document_version_id=None,
            clause="3.5.2",
            page=14,
            excerpt="The supplier shall perform marine installation and burial.",
            data_origin="synthetic",
            derivation_kind="ASSUMED",
        ),
        RfpRequirement(
            id=REQ_DESIGN,
            requirement_code="R-02",
            field_tag="DESIGN",
            title="케이블 시스템 설계 제출",
            description="케이블 단면, 접속함과 보호 설계를 제출한다.",
            document_name="합성 RFP 해저케이블 공급·설치 (예시)",
            document_id=None,
            document_version_id=None,
            clause="4.1",
            page=19,
            excerpt=None,
            data_origin="synthetic",
            derivation_kind="ASSUMED",
        ),
    ]
    roles = [
        RequiredRole(
            id=ROLE_DESIGN,
            role_code="ROLE-DESIGN",
            title="케이블 시스템 설계",
            field_tag="DESIGN",
            tasks="단면 설계, 접속함 설계, 보호 설계 검토",
            experience_condition="HVDC 또는 HVAC 해저케이블 설계 참여",
            is_core=True,
            headcount=1,
            period_start=date(2027, 1, 4),
            period_end=date(2027, 6, 30),
            allocation_rate=50,
            status="IN_COMPOSITION",
            reviewed_by_user_id=SALES_USER,
            reviewed_at=datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC),
            next_action=None,
            next_action_organization_id=None,
            reply_due_at=None,
            requirements=[
                RoleRequirementLink(requirement_id=REQ_DESIGN, rationale="4.1 설계 제출")
            ],
            competencies=[],
            data_origin="synthetic",
            derivation_kind="ASSUMED",
        ),
        RequiredRole(
            id=ROLE_CONSTRUCTION,
            role_code="ROLE-CONSTRUCTION",
            title="해상 시공 관리",
            field_tag="CONSTRUCTION_TRANSPORT",
            tasks="해상 설치 조건 검토, 시공 위험 검토, 설치 공정 관리",
            experience_condition="해저케이블 해상 시공 참여",
            is_core=True,
            headcount=None,
            period_start=date(2027, 3, 1),
            period_end=date(2027, 9, 30),
            allocation_rate=None,
            status="DEPT_VERIFIED",
            reviewed_by_user_id=SALES_USER,
            reviewed_at=datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC),
            next_action="시공 부서에 자료 보완 또는 다른 후보 요청",
            next_action_organization_id=CONSTRUCTION_ORG,
            reply_due_at=datetime(2026, 11, 6, 9, 0, 0, tzinfo=UTC),
            requirements=[
                RoleRequirementLink(requirement_id=REQ_INSTALL, rationale="3.5.2 해상 설치 포함")
            ],
            competencies=[
                RoleCompetency(
                    id=COMPETENCY_REQUIRED,
                    kind="REQUIRED",
                    description="해상 설치 조건·시공 위험 검토 역량",
                ),
                RoleCompetency(
                    id=COMPETENCY_PREFERRED,
                    kind="PREFERRED",
                    description="유사 해저 시공 참여 경험",
                ),
            ],
            data_origin="synthetic",
            derivation_kind="ASSUMED",
        ),
    ]
    requests = [
        StaffingRequest(
            id=REQUEST_DESIGN,
            required_role_id=ROLE_DESIGN,
            organization_id=DESIGN_ORG,
            organization_name="합성 설계팀",
            participation="LEAD",
            status="ANSWERED",
            issued_by_user_id=SALES_USER,
            issued_at=datetime(2026, 10, 21, 9, 0, 0, tzinfo=UTC),
            due_at=datetime(2026, 10, 30, 9, 0, 0, tzinfo=UTC),
            message="케이블 시스템 설계 역할 후보를 추천해 주세요.",
            data_origin="synthetic",
            derivation_kind="SYSTEM",
        ),
        StaffingRequest(
            id=REQUEST_CONSTRUCTION,
            required_role_id=ROLE_CONSTRUCTION,
            organization_id=CONSTRUCTION_ORG,
            organization_name="합성 시공팀",
            participation="LEAD",
            status="ANSWERED",
            issued_by_user_id=SALES_USER,
            issued_at=datetime(2026, 10, 21, 9, 0, 0, tzinfo=UTC),
            due_at=datetime(2026, 10, 30, 9, 0, 0, tzinfo=UTC),
            message="해상 시공 관리 역할 후보를 추천해 주세요. 필수: 해상 설치 조건·시공 위험 검토.",
            data_origin="synthetic",
            derivation_kind="SYSTEM",
        ),
    ]
    recommendations = [
        CandidateRecommendation(
            id=RECOMMEND_A,
            staffing_request_id=REQUEST_DESIGN,
            required_role_id=ROLE_DESIGN,
            candidate_id=CANDIDATE_A,
            candidate_name="가상 후보 A",
            organization_id=DESIGN_ORG,
            recommended_by_user_id=DEPT_USER,
            recommended_at=datetime(2026, 10, 23, 2, 0, 0, tzinfo=UTC),
            rationale="최근 HVDC 해저케이블 설계 과업 수행",
            status="RECOMMENDED",
            experience_evidence=[
                ExperienceEvidence(
                    id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddd01"),
                    summary="HVDC 해저케이블 단면·접속함 설계 담당",
                    project_name="합성 프로젝트 X (2024)",
                    role_title="설계 담당",
                    period_start=date(2024, 3, 1),
                    period_end=date(2025, 2, 28),
                    evidence_source="부서 보유 과업 기록 (가상)",
                    status="CONFIRMED",
                    verified_by_user_id=DEPT_USER,
                    verified_at=datetime(2026, 10, 23, 2, 10, 0, tzinfo=UTC),
                    basis="과업 기록과 산출물 목록 확인",
                    data_origin="synthetic",
                    derivation_kind="SYSTEM",
                )
            ],
            competency_checks=[],
            availability_checks=[
                AvailabilityCheck(
                    id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeee01"),
                    period_start=date(2027, 1, 4),
                    period_end=date(2027, 6, 30),
                    allocation_rate=50,
                    constraints_note=None,
                    checked_by_user_id=DEPT_USER,
                    checked_at=datetime(2026, 10, 23, 2, 10, 0, tzinfo=UTC),
                    basis="부서 투입 계획표 확인",
                    status="CONFIRMED",
                    data_origin="synthetic",
                    derivation_kind="SYSTEM",
                )
            ],
            data_origin="synthetic",
            derivation_kind="SYSTEM",
        ),
        CandidateRecommendation(
            id=RECOMMEND_D,
            staffing_request_id=REQUEST_CONSTRUCTION,
            required_role_id=ROLE_CONSTRUCTION,
            candidate_id=CANDIDATE_D,
            candidate_name="가상 후보 D",
            organization_id=CONSTRUCTION_ORG,
            recommended_by_user_id=DEPT_USER,
            recommended_at=datetime(2026, 10, 23, 3, 0, 0, tzinfo=UTC),
            rationale="육상 케이블 시공 관리 경험",
            status="RECOMMENDED",
            experience_evidence=[
                ExperienceEvidence(
                    id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddd04"),
                    summary="육상 송전케이블 설치 공정 관리",
                    project_name="합성 프로젝트 Y (2023)",
                    role_title="시공 관리",
                    period_start=date(2023, 1, 1),
                    period_end=date(2023, 12, 31),
                    evidence_source="부서 보유 과업 기록 (가상)",
                    # Onshore experience only: similar experience is recorded but the
                    # submarine requirement is not shown as met.
                    status="NEEDS_CHECK",
                    verified_by_user_id=None,
                    verified_at=None,
                    basis=None,
                    data_origin="synthetic",
                    derivation_kind="SYSTEM",
                )
            ],
            competency_checks=[
                CompetencyCheck(
                    id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddd14"),
                    competency_id=COMPETENCY_REQUIRED,
                    status="NEEDS_CHECK",
                    checked_by_user_id=None,
                    checked_at=None,
                    basis=None,
                    data_origin="synthetic",
                    derivation_kind="SYSTEM",
                )
            ],
            availability_checks=[
                AvailabilityCheck(
                    id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeee04"),
                    period_start=None,
                    period_end=None,
                    allocation_rate=None,
                    constraints_note=None,
                    checked_by_user_id=None,
                    checked_at=None,
                    basis=None,
                    status="UNSPECIFIED",
                    data_origin="synthetic",
                    derivation_kind="SYSTEM",
                )
            ],
            data_origin="synthetic",
            derivation_kind="SYSTEM",
        ),
    ]
    composition = CompositionVersion(
        id=VERSION_ID,
        version=1,
        created_by_user_id=SALES_USER,
        created_at=datetime(2026, 10, 23, 5, 0, 0, tzinfo=UTC),
        note="초안. 해상 시공 역할은 보강 필요.",
        members=[
            CompositionMember(
                id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccc11"),
                required_role_id=ROLE_DESIGN,
                recommendation_id=RECOMMEND_A,
                status="PROPOSED",
                rationale="설계 경험과 가용성 확인 완료",
                remaining_conditions=None,
                gap_note=None,
                next_action_organization_id=None,
                follow_up_due_at=None,
            ),
            CompositionMember(
                id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccc12"),
                required_role_id=ROLE_CONSTRUCTION,
                recommendation_id=RECOMMEND_D,
                status="NEEDS_REINFORCEMENT",
                rationale="육상 시공 경험만 확인",
                remaining_conditions="해상 설치 조건·시공 위험 검토 역량 추가 확인, 가용성 미기재",
                gap_note="자료 보완 또는 다른 후보 추천 필요",
                next_action_organization_id=CONSTRUCTION_ORG,
                follow_up_due_at=datetime(2026, 11, 6, 9, 0, 0, tzinfo=UTC),
            ),
        ],
        data_origin="synthetic",
        derivation_kind="SYSTEM",
    )
    return ProjectDetail(
        id=PROJECT_ID,
        external_id=None,
        version=1,
        known_at=CREATED_AT,
        created_at=CREATED_AT,
        company_code="HOST",
        name="합성 RFP: 해저케이블 공급·설치 (예시)",
        client_name="가상 발주처",
        status="COMPOSING",
        responsible_user_id=SALES_USER,
        responsible=ResponsibleSummary(
            user_id=SALES_USER,
            assigned_by_user_id=MANAGER_USER,
            assigned_at=datetime(2026, 10, 20, 9, 30, 0, tzinfo=UTC),
            reason="담당 sales를 프로젝트 책임자로 지정",
        ),
        rfp_requirements=requirements,
        required_roles=roles,
        staffing_requests=requests,
        recommendations=recommendations,
        current_composition=composition,
        final_confirmation=None,
        data_origin="synthetic",
        derivation_kind="ASSUMED",
    )


def examples() -> list[dict]:
    listing: SuccessResponse[ProjectList] = SuccessResponse(
        data=ProjectList(
            items=[
                summary(
                    SECOND_PROJECT_ID,
                    "합성 RFP: GIS 단말 키트 공급·설치 (예시)",
                    "INITIAL_REVIEW",
                    datetime(2026, 10, 22, 9, 0, 0, tzinfo=UTC),
                    None,
                ),
                summary(
                    PROJECT_ID, "합성 RFP: 해저케이블 공급·설치 (예시)", "COMPOSING", CREATED_AT, 1
                ),
            ],
            next_cursor=None,
        ),
        meta=META,
    )
    project: SuccessResponse[ProjectDetail] = SuccessResponse(data=detail(), meta=META)
    return [
        {
            "method": "GET",
            "path": "/api/v1/tft/projects",
            "status": 200,
            "body": listing.model_dump(mode="json"),
        },
        {
            "method": "GET",
            "path": f"/api/v1/tft/projects/{PROJECT_ID}",
            "status": 200,
            "body": project.model_dump(mode="json"),
        },
    ]
