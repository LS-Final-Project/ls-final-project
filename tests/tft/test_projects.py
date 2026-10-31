"""KAN-119 project list and detail routes, with the database replaced by in-memory rows."""

import base64
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.tft.main import create_app
from backend.api.tft.projects import repository, routes
from backend.api.tft.rfp import routes_write
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

BASE = datetime(2026, 10, 1, tzinfo=UTC)
SALES = UUID(int=0x3)
# Assigned as the sales user of the second project without being its responsible user.
ASSIGNED_SALES = UUID(int=0x6)


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
        "sales_user_id": {0: SALES, 1: ASSIGNED_SALES}.get(index),
        "output_status": "INTAKE",
        "judgement_status": "NEEDS_REVIEW",
        "purpose": "INITIAL_PROPOSAL",
        "customer_goal": None,
        "supply_region": None,
        "customer_manufacturer_list": None,
        "off_list_permission": "UNSTATED",
        "review_departments": [],
        "requirements_count": 0,
        "unresolved_mandatory_count": 0,
        "pending_proposals_count": 0,
        "open_questions_count": 0,
        "documents_count": 0,
        "draft_version": None,
        "review_version": None,
        "strategic_customer": None,
        "strategic_customer_note": None,
        "strategic_customer_set_by": None,
        "integration_holder": "UNRESOLVED",
        "latest_job_id": None,
    }


ROWS = [project_row(i) for i in range(5)]
PROJECT = ROWS[0]
PROJECTS_MANAGER = Principal(
    user_id=uuid4(), roles={"manager"}, managed_projects={r["id"] for r in ROWS}
)
REQ_ID = UUID(int=100)
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


HOLDER = {
    "integration_holder": "UNRESOLVED",
    "integration_holder_candidates": [],
    "integration_note": None,
    "integration_set_by": None,
    "integration_set_at": None,
}
CLAUSE_ID = UUID(int=200)
INTEGRATION_ROWS = [
    {"clause_id": CLAUSE_ID, "clause_no": "2.3.A", "task": None, "assembly_clause": True},
    {"clause_id": UUID(int=201), "clause_no": "2.5.K", "task": None, "assembly_clause": False},
]


def only_for(project_id: UUID, rows: list) -> list:
    return rows if project_id == PROJECT["id"] else []


@pytest.fixture
def fakes(monkeypatch):
    @asynccontextmanager
    async def fake_connection(settings):
        yield object()

    async def list_projects(
        conn, limit, after, managed_projects, user_id, department=None, **filters
    ):
        rows = [
            r
            for r in ROWS
            if r["id"] in managed_projects
            or r["responsible_user_id"] == user_id
            or r["sales_user_id"] == user_id
        ]
        rows = sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)
        if after:
            rows = [r for r in rows if (r["created_at"], r["id"]) < after]
        return rows[: limit + 1]

    async def get_project(conn, project_id):
        return next((dict(r) for r in ROWS if r["id"] == project_id), None)

    async def integration_state(conn, project_id):
        return HOLDER, only_for(project_id, INTEGRATION_ROWS)

    async def list_rfp_requirements(conn, project_id):
        return only_for(project_id, REQUIREMENTS)

    async def review_departments(conn, project_id):
        return []

    async def project_detail_parts(conn, project_id):
        return next(r for r in ROWS if r["id"] == project_id), [], {}

    monkeypatch.setattr(routes_write.repository, "project_detail_parts", project_detail_parts)
    monkeypatch.setattr(routes_write, "integration_state", integration_state)
    monkeypatch.setattr(repository, "review_departments", review_departments)
    monkeypatch.setattr(routes, "connection", fake_connection)
    monkeypatch.setattr(repository, "list_projects", list_projects)
    monkeypatch.setattr(repository, "get_project", get_project)
    monkeypatch.setattr(repository, "list_rfp_requirements", list_rfp_requirements)
    monkeypatch.setattr(repository, "integration_state", integration_state)


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


def test_list_summary_carries_owners_without_composition_pointer(client):
    items = client.get("/api/v1/tft/projects").json()["data"]["items"]
    first = next(item for item in items if item["external_id"] == "RFP-0000")
    assert first["responsible_user_id"] == str(SALES)
    assert first["sales_user_id"] == str(SALES)
    # KAN-181 removed the composition stage, so the summary no longer points at it.
    assert "current_composition_version" not in first
    other = next(item for item in items if item["external_id"] == "RFP-0001")
    assert other["responsible_user_id"] is None


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


REMOVED_STAGES = {
    "required_roles",
    "staffing_requests",
    "recommendations",
    "current_composition",
    "responsible",
    "final_confirmation",
}


def test_repository_detail_selects_saved_constraint_values():
    import asyncio
    from unittest.mock import AsyncMock

    stored = {
        **PROJECT,
        "supply_region": "한국",
        "customer_manufacturer_list": ["제조사 A", "제조사 B"],
        "off_list_permission": "FORBIDDEN",
    }

    async def execute(query, params):
        assert params == (PROJECT["id"],)
        # GET 조립 단계의 다른 조회로 누락을 가리지 않고 이 SELECT의 실제 투영을 확인한다.
        columns = query.removeprefix("SELECT ").split(" FROM ", 1)[0].split(", ")
        cursor = AsyncMock()
        cursor.fetchone.return_value = {column: stored[column] for column in columns}
        return cursor

    conn = AsyncMock()
    conn.execute.side_effect = execute
    row = asyncio.run(repository.get_project(conn, PROJECT["id"]))
    assert row["supply_region"] == "한국"
    assert row["customer_manufacturer_list"] == ["제조사 A", "제조사 B"]
    assert row["off_list_permission"] == "FORBIDDEN"


def test_detail_response_preserves_saved_constraint_values(client, monkeypatch):
    expected = {
        "supply_region": "한국",
        "customer_manufacturer_list": ["제조사 A", "제조사 B"],
        "off_list_permission": "FORBIDDEN",
    }
    for name, value in expected.items():
        monkeypatch.setitem(PROJECT, name, value)
    response = client.get(f"/api/v1/tft/projects/{PROJECT['id']}")
    assert response.status_code == 200, response.text
    detail = response.json()["data"]
    assert {name: detail[name] for name in expected} == expected


def test_detail_is_the_project_with_its_rfp_requirements_only(client):
    body = client.get(f"/api/v1/tft/projects/{PROJECT['id']}").json()["data"]
    assert body["sales_user_id"] == str(SALES)
    assert body["responsible_user_id"] == str(SALES)
    assert [r["id"] for r in body["rfp_requirements"]] == [str(REQ_ID)]
    assert body["rfp_requirements"][0]["clause"] == "3.5.2"
    assert not REMOVED_STAGES & set(body)


def test_detail_of_project_without_requirements_has_an_empty_list(client):
    body = client.get(f"/api/v1/tft/projects/{ROWS[1]['id']}").json()["data"]
    assert body["rfp_requirements"] == []
    assert body["responsible_user_id"] is None
    assert body["integration_responsibility"]["required"] is False
    assert body["integration_responsibility"]["basis_clause_ids"] == []


def test_detail_shows_the_integration_responsibility_with_its_basis_clause(client):
    """명세 v1.3 §7(KAN-199): required·근거 조항은 읽을 때 계산하고, 사람이 볼 수 있게 조항 ID와 번호를 함께 낸다."""
    body = client.get(f"/api/v1/tft/projects/{PROJECT['id']}").json()["data"]
    integration = body["integration_responsibility"]
    assert (integration["required"], integration["holder"]) == (True, "UNRESOLVED")
    assert integration["basis_clause_ids"] == [str(CLAUSE_ID)]
    assert integration["basis_clause_nos"] == ["2.3.A"]
    assert [p["holder"] for p in integration["fallback_paths"]] == [
        "OWN",
        "SINGLE_PARTNER",
        "JOINT",
        "HOLD",
    ]
    assert integration["unresolved"]


def test_detail_survives_a_database_without_the_t12_migration(client, monkeypatch):
    """T12 전 DB에서도 프로젝트 조회는 200이고, 통합 책임은 '불필요'가 아니라 null이다(#340)."""

    async def missing(conn, project_id):
        return None

    monkeypatch.setattr(routes_write, "integration_state", missing)
    result = client.get(f"/api/v1/tft/projects/{PROJECT['id']}")
    assert result.status_code == 200
    assert result.json()["data"]["integration_responsibility"] is None


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


def test_assigned_sales_user_sees_the_project_without_being_responsible(client):
    headers = principal_header(Principal(user_id=ASSIGNED_SALES, roles={"sales"}))
    items = client.get("/api/v1/tft/projects", headers=headers).json()["data"]["items"]
    assert [i["id"] for i in items] == [str(ROWS[1]["id"])]
    assert client.get(f"/api/v1/tft/projects/{ROWS[1]['id']}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/tft/projects/{PROJECT['id']}", headers=headers).status_code == 404


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
