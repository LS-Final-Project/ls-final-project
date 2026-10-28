"""Validated synthetic examples, exported only for local frontend mocking.

A submarine-cable RFP project with two requirements and a second project without any.
Users are the local seed accounts (infra/local/seed_demo.sql).
"""

from datetime import UTC, datetime
from uuid import UUID

from backend.api.tft.projects.schemas import (
    ProjectDetail,
    ProjectList,
    ProjectStatus,
    ProjectSummary,
    RfpRequirement,
)
from backend.shared.models import ResponseMeta, SuccessResponse

META = ResponseMeta(request_id=UUID("11111111-1111-4111-8111-111111111111"))
PROJECT_ID = UUID("33333333-3333-4333-8333-333333333333")
SECOND_PROJECT_ID = UUID("33333333-3333-4333-8333-333333333334")
CREATED_AT = datetime(2026, 10, 20, 9, 0, 0, tzinfo=UTC)

SALES_USER = UUID("00000000-0000-4000-8000-000000000003")

REQ_INSTALL = UUID("77777777-7777-4777-8777-777777777701")
REQ_DESIGN = UUID("77777777-7777-4777-8777-777777777702")


def summary(
    project_id: UUID,
    name: str,
    status: ProjectStatus,
    created_at: datetime,
    responsible: UUID | None,
) -> ProjectSummary:
    return ProjectSummary(
        id=project_id,
        external_id=None,
        name=name,
        company_code="HOST",
        status=status,
        responsible_user_id=responsible,
        sales_user_id=SALES_USER,
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
        sales_user_id=SALES_USER,
        rfp_requirements=requirements,
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
                    PROJECT_ID,
                    "합성 RFP: 해저케이블 공급·설치 (예시)",
                    "COMPOSING",
                    CREATED_AT,
                    SALES_USER,
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
