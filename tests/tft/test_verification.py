"""KAN-125 rules: missing evidence, changed checks, overlap and partial confirmation."""

from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.tft.main import create_app
from backend.api.tft.projects.schemas import ProjectDetail
from backend.api.tft.verification import routes
from backend.api.tft.verification.rules import assess
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings
from tests.tft.test_projects import (
    AVAILABILITY,
    BASE,
    COMPETENCIES,
    COMPETENCY_CHECKS,
    EVIDENCE,
    MEMBERS,
    PROJECT,
    RECOMMENDATIONS,
    REQUESTS,
    REQUIREMENTS,
    ROLES,
    SALES,
    VERSION,
)


@pytest.fixture
def project():
    return ProjectDetail.model_validate(
        {
            **PROJECT,
            "responsible": None,
            "rfp_requirements": REQUIREMENTS,
            "required_roles": [
                {
                    **ROLES[0],
                    "headcount": 1,
                    "allocation_rate": 50,
                    "requirements": [],
                    "competencies": COMPETENCIES,
                }
            ],
            "staffing_requests": REQUESTS,
            "recommendations": [
                {
                    **RECOMMENDATIONS[0],
                    "experience_evidence": EVIDENCE,
                    "competency_checks": COMPETENCY_CHECKS,
                    "availability_checks": AVAILABILITY,
                }
            ],
            "current_composition": {**VERSION, "members": MEMBERS},
            "final_confirmation": None,
        }
    )


def verify(project):
    candidate = project.recommendations[0]
    for record in candidate.experience_evidence:
        record.status = "CONFIRMED"
        record.verified_by_user_id = SALES
        record.verified_at = BASE
        record.basis = "실제 수행 업무 확인"
    for record in candidate.competency_checks + candidate.availability_checks:
        record.status = "CONFIRMED"
        record.checked_by_user_id = SALES
        record.checked_at = BASE
        record.basis = "부서 확인 자료"
    availability = candidate.availability_checks[0]
    role = project.required_roles[0]
    availability.period_start, availability.period_end = role.period_start, role.period_end
    availability.allocation_rate = role.allocation_rate


def test_missing_evidence_is_not_unmet_or_complete(project):
    report = assess(project)
    assert not report.complete
    assert report.roles[0].candidates[0].required_competencies == "UNSPECIFIED"
    assert report.roles[0].next_action == "DEPARTMENT_CHECK"
    assert report.roles[0].next_organization_ids == [REQUESTS[0]["organization_id"]]


def test_no_candidate_and_undecided_headcount(project):
    project.recommendations = []
    project.required_roles[0].headcount = None
    role = assess(project).roles[0]
    assert role.reasons == ["NO_RECOMMENDATION", "HEADCOUNT_UNSPECIFIED"]
    assert not role.complete


def test_verified_is_only_ready_for_human_review(project):
    verify(project)
    report = assess(project)
    assert report.roles[0].candidates[0].ready_for_review
    assert report.roles[0].next_action == "RESPONSIBLE_REVIEW"
    assert report.roles[0].next_user_id == SALES
    assert not report.complete
    project.responsible_user_id = None
    assert assess(project).roles[0].next_action == "ASSIGN_RESPONSIBLE"


@pytest.mark.parametrize("field", ["basis", "checked_by_user_id"])
def test_claimed_confirmation_without_provenance_is_unknown(project, field):
    verify(project)
    setattr(project.recommendations[0].competency_checks[0], field, None)
    assert assess(project).roles[0].candidates[0].required_competencies == "NEEDS_CHECK"


def test_latest_availability_overrides_previous_confirmation(project):
    verify(project)
    old = project.recommendations[0].availability_checks[0]
    changed = old.model_copy(
        update={"id": uuid4(), "checked_at": BASE + timedelta(days=1), "status": "NEEDS_CHECK"}
    )
    project.recommendations[0].availability_checks.append(changed)
    project.current_composition.members[0].status = "CONFIRMED"
    role = assess(project).roles[0]
    assert role.candidates[0].availability == "NEEDS_CHECK"
    assert "CONFIRMATION_NEEDS_REVIEW" in role.reasons
    assert not role.complete
    assert old.status == "CONFIRMED"


@pytest.mark.parametrize(
    "change,expected", [("missing", "NEEDS_CHECK"), ("short", "UNMET"), ("rate", "UNMET")]
)
def test_availability_covers_full_role_period_and_rate(project, change, expected):
    verify(project)
    check = project.recommendations[0].availability_checks[0]
    if change == "missing":
        project.required_roles[0].period_end = None
    elif change == "short":
        check.period_end -= timedelta(days=1)
    else:
        check.allocation_rate = 10
    assert assess(project).roles[0].candidates[0].availability == expected


def test_same_candidate_overlapping_roles_warns_but_withdrawn_does_not(project):
    verify(project)
    other_role = project.required_roles[0].model_copy(deep=True, update={"id": uuid4()})
    project.required_roles.append(other_role)
    other = project.recommendations[0].model_copy(
        deep=True, update={"id": uuid4(), "required_role_id": other_role.id}
    )
    project.recommendations.append(other)
    assert "OVERLAPPING_ROLE_PROPOSALS" in assess(project).roles[0].reasons
    other.status = "WITHDRAWN"
    assert "OVERLAPPING_ROLE_PROPOSALS" not in assess(project).roles[0].reasons


def test_partial_confirmation_never_becomes_whole_completion(project):
    verify(project)
    project.current_composition.members[0].status = "CONFIRMED"
    project.required_roles.append(project.required_roles[0].model_copy(update={"id": uuid4()}))
    report = assess(project)
    assert report.state == "PARTIALLY_CONFIRMED"
    assert report.confirmed_role_count == 1 and report.total_role_count == 2
    assert not report.complete


def test_all_roles_still_requires_current_responsible_final_decision(project):
    verify(project)
    project.current_composition.members[0].status = "CONFIRMED"
    assert assess(project).state == "READY_FOR_FINAL_REVIEW"
    from backend.api.tft.projects.schemas import FinalConfirmation

    project.status = "CONFIRMED"
    project.final_confirmation = FinalConfirmation(
        id=uuid4(),
        composition_version_id=project.current_composition.id,
        composition_version=1,
        confirmed_by_user_id=SALES,
        confirmed_at=BASE,
        note=None,
    )
    assert assess(project).complete
    project.responsible_user_id = uuid4()
    assert not assess(project).complete


def test_empty_project_is_not_complete(project):
    project.required_roles = []
    assert not assess(project).complete


def test_http_scope_and_no_writes(project, monkeypatch):
    @asynccontextmanager
    async def connection(settings):
        yield object()

    async def get_project(conn, project_id):
        return PROJECT if project_id == project.id else None

    async def load_detail(conn, row):
        return project

    monkeypatch.setattr(routes, "connection", connection)
    monkeypatch.setattr(routes.repository, "get_project", get_project)
    monkeypatch.setattr(routes, "load_detail", load_detail)
    with TestClient(create_app(Settings(service_name="tft", environment="test"))) as client:
        url = f"/api/v1/tft/projects/{project.id}/verification"
        assert client.get(url).status_code == 401
        for principal, status in [
            (Principal(user_id=SALES, roles={"sales"}), 200),
            (Principal(user_id=uuid4(), roles={"sales"}), 404),
            (Principal(user_id=uuid4(), roles={"dept_staff"}, dept="FIELD_SVC"), 404),
            (Principal(user_id=uuid4(), roles={"cs_agent"}), 403),
        ]:
            response = client.get(url, headers=principal_header(principal))
            assert response.status_code == status


def test_new_confirmed_evidence_after_composition_still_requires_review(project):
    verify(project)
    project.current_composition.members[0].status = "CONFIRMED"
    project.recommendations[0].availability_checks[0].checked_at = BASE + timedelta(days=1)
    role = assess(project).roles[0]
    assert "CONFIRMATION_NEEDS_REVIEW" in role.reasons
    assert not role.complete


def test_same_person_in_two_requests_does_not_fill_two_positions(project):
    verify(project)
    project.required_roles[0].headcount = 2
    original = project.recommendations[0]
    other = original.model_copy(deep=True, update={"id": uuid4()})
    project.recommendations.append(other)
    member = project.current_composition.members[0]
    member.status = "CONFIRMED"
    project.current_composition.members.append(
        member.model_copy(update={"id": uuid4(), "recommendation_id": other.id})
    )
    role = assess(project).roles[0]
    assert role.confirmed_count == 1
    assert not role.complete


def test_nonoverlapping_roles_are_not_schedule_conflicts(project):
    verify(project)
    original_role = project.required_roles[0]
    other_role = original_role.model_copy(
        deep=True,
        update={
            "id": uuid4(),
            "period_start": original_role.period_end + timedelta(days=1),
            "period_end": original_role.period_end + timedelta(days=3),
        },
    )
    project.required_roles.append(other_role)
    project.recommendations.append(
        project.recommendations[0].model_copy(
            deep=True, update={"id": uuid4(), "required_role_id": other_role.id}
        )
    )
    assert "OVERLAPPING_ROLE_PROPOSALS" not in assess(project).roles[0].reasons
