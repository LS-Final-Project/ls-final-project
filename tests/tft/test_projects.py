"""KAN-119 project list and detail routes, with the database replaced by in-memory rows."""

import base64
import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.tft.main import create_app
from backend.api.tft.projects import repository, routes
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

BASE = datetime(2026, 10, 1, tzinfo=UTC)
SALES = UUID(int=0x3)
MANAGER = UUID(int=0x4)
DEPT = UUID(int=0x2)


def project_row(index: int) -> dict:
    return {
        "id": UUID(int=index + 1),
        "external_id": f"RFP-{index:04d}",
        "version": 1,
        "name": f"project {index}",
        "company_code": "HOST",
        "status": "INITIAL_REVIEW",
        "data_origin": "synthetic",
        "derivation_kind": "ASSUMED",
        "known_at": BASE + timedelta(hours=index),
        "created_at": BASE + timedelta(hours=index),
        "client_name": None,
        "responsible_user_id": SALES if index == 0 else None,
        "sales_user_id": SALES if index == 0 else None,
        "current_composition_version": 1 if index == 0 else None,
    }


ROWS = [project_row(i) for i in range(5)]
PROJECT = ROWS[0]
PROJECTS_MANAGER = Principal(
    user_id=uuid4(), roles={"manager"}, managed_projects={r["id"] for r in ROWS}
)
ROLE_ID, REQ_ID, REQUEST_ID, RECOMMEND_ID, VERSION_ID = (UUID(int=100 + i) for i in range(5))
ORIGIN = {"data_origin": "synthetic", "derivation_kind": "SYSTEM"}

REQUIREMENTS = [
    {
        "id": REQ_ID,
        "requirement_code": "R-01",
        "field_tag": "CONSTRUCTION_TRANSPORT",
        "title": "marine installation",
        "description": None,
        "document_name": None,
        "document_id": None,
        "document_version_id": None,
        "clause": "3.5.2",
        "page": 14,
        "excerpt": None,
        **ORIGIN,
    }
]
ROLES = [
    {
        "id": ROLE_ID,
        "role_code": "ROLE-CONSTRUCTION",
        "title": "marine construction",
        "field_tag": "CONSTRUCTION_TRANSPORT",
        "tasks": None,
        "experience_condition": None,
        "is_core": True,
        "headcount": None,
        "period_start": date(2027, 3, 1),
        "period_end": date(2027, 9, 30),
        "allocation_rate": None,
        "status": "DEPT_VERIFIED",
        "reviewed_by_user_id": SALES,
        "reviewed_at": BASE,
        "next_action": None,
        "next_action_organization_id": None,
        "reply_due_at": None,
        **ORIGIN,
    }
]
LINKS = [{"required_role_id": ROLE_ID, "requirement_id": REQ_ID, "rationale": "3.5.2"}]
COMPETENCIES = [
    {"id": UUID(int=200), "required_role_id": ROLE_ID, "kind": "REQUIRED", "description": "marine"}
]
REQUESTS = [
    {
        "id": REQUEST_ID,
        "project_id": PROJECT["id"],
        "required_role_id": ROLE_ID,
        "role_name": "marine construction",
        "organization_id": UUID(int=300),
        "organization_name": "construction",
        "organization_dept_code": "FIELD_SVC",
        "participation": "LEAD",
        "status": "ANSWERED",
        "version": 2,
        "issued_by_user_id": SALES,
        "issued_at": BASE,
        "due_at": None,
        "due_undecided": True,
        "message": None,
        "created_at": BASE,
        "updated_at": BASE,
        **ORIGIN,
    }
]
RECOMMENDATIONS = [
    {
        "id": RECOMMEND_ID,
        "staffing_request_id": REQUEST_ID,
        "required_role_id": ROLE_ID,
        "candidate_id": UUID(int=400),
        "candidate_name": "candidate D",
        "organization_id": UUID(int=300),
        "recommended_by_user_id": DEPT,
        "recommended_at": BASE,
        "rationale": None,
        "status": "RECOMMENDED",
        **ORIGIN,
    }
]
EVIDENCE = [
    {
        "id": UUID(int=500),
        "recommendation_id": RECOMMEND_ID,
        "summary": "onshore only",
        "project_name": None,
        "role_title": None,
        "period_start": None,
        "period_end": None,
        "evidence_source": None,
        "status": "NEEDS_CHECK",
        "verified_by_user_id": None,
        "verified_at": None,
        "basis": None,
        **ORIGIN,
    }
]
COMPETENCY_CHECKS = [
    {
        "id": UUID(int=600),
        "recommendation_id": RECOMMEND_ID,
        "competency_id": UUID(int=200),
        "status": "NEEDS_CHECK",
        "checked_by_user_id": None,
        "checked_at": None,
        "basis": None,
        **ORIGIN,
    }
]
AVAILABILITY = [
    {
        "id": UUID(int=700),
        "recommendation_id": RECOMMEND_ID,
        "period_start": None,
        "period_end": None,
        "allocation_rate": None,
        "constraints_note": None,
        "checked_by_user_id": None,
        "checked_at": None,
        "basis": None,
        "status": "UNSPECIFIED",
        **ORIGIN,
    }
]
VERSION = {
    "id": VERSION_ID,
    "version": 1,
    "created_by_user_id": SALES,
    "created_at": BASE,
    "note": None,
    **ORIGIN,
}
MEMBERS = [
    {
        "id": UUID(int=800),
        "required_role_id": ROLE_ID,
        "recommendation_id": RECOMMEND_ID,
        "status": "NEEDS_REINFORCEMENT",
        "rationale": None,
        "remaining_conditions": "marine competency",
        "gap_note": None,
        "next_action_organization_id": None,
        "follow_up_due_at": None,
    }
]
RESPONSIBLE = {
    "new_user_id": SALES,
    "changed_by_user_id": MANAGER,
    "reason": "assigned",
    "occurred_at": BASE,
}


def only_for(project_id: UUID, rows: list) -> list:
    return rows if project_id == PROJECT["id"] else []


@pytest.fixture
def fakes(monkeypatch):
    state = {"responsible": RESPONSIBLE, "confirmation": None}

    @asynccontextmanager
    async def fake_connection(settings):
        yield object()

    async def list_projects(conn, limit, after, managed_projects, user_id):
        rows = [
            r for r in ROWS if r["id"] in managed_projects or r["responsible_user_id"] == user_id
        ]
        rows = sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)
        if after:
            rows = [r for r in rows if (r["created_at"], r["id"]) < after]
        return rows[: limit + 1]

    async def get_project(conn, project_id):
        return next((dict(r) for r in ROWS if r["id"] == project_id), None)

    def scoped(rows):
        async def fake(conn, project_id):
            return only_for(project_id, rows)

        return fake

    async def current_composition(conn, project_id):
        return VERSION if project_id == PROJECT["id"] else None

    async def members(conn, version_id):
        return MEMBERS if version_id == VERSION_ID else []

    async def responsible(conn, project_id):
        return state["responsible"] if project_id == PROJECT["id"] else None

    async def confirmation(conn, project_id):
        return state["confirmation"] if project_id == PROJECT["id"] else None

    monkeypatch.setattr(routes, "connection", fake_connection)
    for name, fake in {
        "list_projects": list_projects,
        "get_project": get_project,
        "list_rfp_requirements": scoped(REQUIREMENTS),
        "list_required_roles": scoped(ROLES),
        "list_role_requirement_links": scoped(LINKS),
        "list_role_competencies": scoped(COMPETENCIES),
        "list_staffing_requests": scoped(REQUESTS),
        "list_recommendations": scoped(RECOMMENDATIONS),
        "list_experience_evidence": scoped(EVIDENCE),
        "list_competency_checks": scoped(COMPETENCY_CHECKS),
        "list_availability_checks": scoped(AVAILABILITY),
        "get_current_composition": current_composition,
        "list_composition_members": members,
        "get_latest_responsible_change": responsible,
        "get_final_confirmation": confirmation,
    }.items():
        monkeypatch.setattr(repository, name, fake)
    return state


@pytest.fixture
def client(fakes):
    app = create_app(Settings(_env_file=None, environment="test"))
    with TestClient(app, headers=principal_header(PROJECTS_MANAGER)) as test_client:
        yield test_client


def test_list_pages_newest_first_without_gaps_or_duplicates(client):
    seen = []
    cursor = None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = client.get("/api/v1/tft/projects", params=params).json()["data"]
        seen.extend(item["external_id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [f"RFP-{i:04d}" for i in reversed(range(5))]


def test_list_summary_carries_stage_pointers(client):
    items = client.get("/api/v1/tft/projects").json()["data"]["items"]
    first = next(item for item in items if item["external_id"] == "RFP-0000")
    assert first["responsible_user_id"] == str(SALES)
    assert first["sales_user_id"] == str(SALES)
    assert first["current_composition_version"] == 1
    other = next(item for item in items if item["external_id"] == "RFP-0001")
    assert other["responsible_user_id"] is None
    assert other["current_composition_version"] is None


def b64(value) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!",
        b64({"c": "2026-10-01"}),
        b64({"c": "2026-10-01T00:00:00+00:00", "i": 5}),
        b64({"c": 1, "i": str(UUID(int=1))}),
        b64(["2026-10-01T00:00:00+00:00", str(UUID(int=1))]),
        b64({"c": "2026-10-01T00:00:00", "i": str(UUID(int=1))}),
        b64({"c": "2026-10-01T00:00:00+00:00", "i": str(UUID(int=1)), "x": 1}),
        b64({"c": "2026-10-01T00:00:00+00:00", "i": "not-a-uuid"}),
        b64({"c": "0001-01-01T00:00:00+23:59", "i": str(UUID(int=1))}),
        # The C/S cursor shape is not accepted by the TFT list.
        b64({"r": "2026-10-01T00:00:00+00:00", "i": str(UUID(int=1))}),
    ],
)
def test_invalid_or_naive_cursor_is_rejected(client, cursor):
    result = client.get("/api/v1/tft/projects", params={"cursor": cursor})
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "TFT_INVALID_CURSOR"


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_is_bounded(client, limit):
    assert client.get("/api/v1/tft/projects", params={"limit": limit}).status_code == 422


def test_detail_keeps_the_six_stages_as_separate_records(client):
    body = client.get(f"/api/v1/tft/projects/{PROJECT['id']}").json()["data"]
    # Initial review: requirement -> role link, review by sales, required competency.
    role = body["required_roles"][0]
    assert role["status"] == "DEPT_VERIFIED"
    assert role["reviewed_by_user_id"] == str(SALES)
    assert role["requirements"] == [{"requirement_id": str(REQ_ID), "rationale": "3.5.2"}]
    assert role["competencies"][0]["kind"] == "REQUIRED"
    assert body["staffing_requests"][0]["issued_by_user_id"] == str(SALES)
    assert body["staffing_requests"][0]["organization_dept_code"] == "FIELD_SVC"
    assert body["staffing_requests"][0]["due_undecided"] is True
    assert body["sales_user_id"] == str(SALES)
    # Department recommendation is a record of its own, apart from the checks.
    recommendation = body["recommendations"][0]
    assert recommendation["recommended_by_user_id"] == str(DEPT)
    assert recommendation["candidate_id"] == str(UUID(int=400))
    # Experience, competency and availability: unconfirmed states are distinguishable.
    assert recommendation["experience_evidence"][0]["status"] == "NEEDS_CHECK"
    assert recommendation["competency_checks"][0]["status"] == "NEEDS_CHECK"
    assert recommendation["availability_checks"][0]["status"] == "UNSPECIFIED"
    # Composition version and the responsible decision.
    assert body["current_composition"]["version"] == 1
    assert body["current_composition"]["members"][0]["status"] == "NEEDS_REINFORCEMENT"
    assert body["responsible"]["user_id"] == str(SALES)
    assert body["responsible"]["assigned_by_user_id"] == str(MANAGER)
    assert body["final_confirmation"] is None


def test_detail_of_project_without_stage_records_has_empty_sections(client):
    body = client.get(f"/api/v1/tft/projects/{ROWS[1]['id']}").json()["data"]
    assert body["required_roles"] == [] and body["recommendations"] == []
    assert body["current_composition"] is None
    assert body["responsible"] is None and body["responsible_user_id"] is None


def test_responsible_summary_requires_matching_history(client, fakes):
    fakes["responsible"] = {**RESPONSIBLE, "new_user_id": MANAGER}
    body = client.get(f"/api/v1/tft/projects/{PROJECT['id']}").json()["data"]
    assert body["responsible_user_id"] == str(SALES)
    assert body["responsible"] is None


def test_final_confirmation_names_the_confirmer_and_version(client, fakes):
    fakes["confirmation"] = {
        "id": UUID(int=900),
        "composition_version_id": VERSION_ID,
        "composition_version": 1,
        "confirmed_by_user_id": SALES,
        "confirmed_at": BASE,
        "note": None,
    }
    body = client.get(f"/api/v1/tft/projects/{PROJECT['id']}").json()["data"]
    assert body["final_confirmation"]["confirmed_by_user_id"] == str(SALES)
    assert body["final_confirmation"]["composition_version"] == 1


def test_unknown_project_returns_feature_error(client):
    result = client.get(f"/api/v1/tft/projects/{uuid4()}")
    assert result.status_code == 404
    assert result.json()["error"]["code"] == "TFT_PROJECT_NOT_FOUND"


def test_routes_are_closed_in_production_until_access_checks_exist(monkeypatch):
    async def must_not_query(*args):
        raise AssertionError("production must be rejected before any database access")

    monkeypatch.setattr(repository, "list_projects", must_not_query)
    monkeypatch.setattr(repository, "get_project", must_not_query)
    app = create_app(Settings(_env_file=None, environment="production"))
    with TestClient(app, headers=principal_header(PROJECTS_MANAGER)) as production:
        for path in ("/api/v1/tft/projects", f"/api/v1/tft/projects/{PROJECT['id']}"):
            result = production.get(path)
            assert result.status_code == 404
            assert result.json()["error"]["code"] == "NOT_FOUND"


def as_user(**fields) -> dict:
    return principal_header(Principal(user_id=uuid4(), **fields))


def test_manager_sees_only_managed_projects(client):
    headers = as_user(roles={"manager"}, managed_projects={ROWS[2]["id"]})
    items = client.get("/api/v1/tft/projects", headers=headers).json()["data"]["items"]
    assert [i["id"] for i in items] == [str(ROWS[2]["id"])]
    other = client.get(f"/api/v1/tft/projects/{ROWS[3]['id']}", headers=headers)
    assert other.status_code == 404
    assert other.json()["error"]["code"] == "TFT_PROJECT_NOT_FOUND"


def test_responsible_user_sees_own_project(client):
    headers = principal_header(Principal(user_id=SALES, roles={"sales"}))
    items = client.get("/api/v1/tft/projects", headers=headers).json()["data"]["items"]
    assert [i["id"] for i in items] == [str(PROJECT["id"])]
    assert client.get(f"/api/v1/tft/projects/{PROJECT['id']}", headers=headers).status_code == 200


@pytest.mark.parametrize(
    "user", [{"roles": {"sales"}}, {"roles": {"dept_staff"}, "dept": "TS_TRF"}]
)
def test_sales_and_department_staff_see_nothing_until_assignments_exist(client, user):
    headers = as_user(**user)
    assert client.get("/api/v1/tft/projects", headers=headers).json()["data"]["items"] == []
    assert client.get(f"/api/v1/tft/projects/{PROJECT['id']}", headers=headers).status_code == 404


def test_c_s_agent_has_no_tft_scope(client):
    headers = as_user(roles={"cs_agent"})
    assert client.get("/api/v1/tft/projects", headers=headers).status_code == 403
    assert client.get(f"/api/v1/tft/projects/{PROJECT['id']}", headers=headers).status_code == 403
