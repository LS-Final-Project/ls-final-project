"""KAN-122 staffing request list, detail, edit and issue, with the database replaced by
an in-memory store. The caller is the relayed principal (backend/shared/access.py, KAN-78)."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.tft.main import create_app
from backend.api.tft.staffing_requests import repository, routes
from backend.shared.access import Principal, principal_header
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

BASE = datetime(2026, 10, 1, tzinfo=UTC)
UPDATED = datetime(2026, 10, 2, tzinfo=UTC)
SALES = UUID(int=0x3)
OTHER_SALES = UUID(int=0x5)
PROJECT_ID = UUID(int=1)
UNASSIGNED_PROJECT_ID = UUID(int=2)
ROLE_ID = UUID(int=100)
DRAFT_ID, ISSUED_ID, UNDECIDED_ID, NO_DUE_ID, ORPHAN_ID = (UUID(int=900 + i) for i in range(5))
CONSTRUCTION_ORG, QUALITY_ORG, OTHER_COMPANY_ORG = (UUID(int=300 + i) for i in range(3))

PROJECTS = {
    PROJECT_ID: {
        "id": PROJECT_ID,
        "company_code": "HOST",
        "responsible_user_id": None,
        "sales_user_id": SALES,
    },
    # Visible to its responsible user, but no sales user assigned yet.
    UNASSIGNED_PROJECT_ID: {
        "id": UNASSIGNED_PROJECT_ID,
        "company_code": "HOST",
        "responsible_user_id": SALES,
        "sales_user_id": None,
    },
}
ORGANIZATIONS = {
    CONSTRUCTION_ORG: {
        "id": CONSTRUCTION_ORG,
        "company_code": "HOST",
        "name": "construction",
        "dept_code": "FIELD_SVC",
    },
    QUALITY_ORG: {
        "id": QUALITY_ORG,
        "company_code": "HOST",
        "name": "quality",
        "dept_code": "QA_CLAIM",
    },
    OTHER_COMPANY_ORG: {
        "id": OTHER_COMPANY_ORG,
        "company_code": "AFFILIATE",
        "name": "affiliate",
        "dept_code": None,
    },
}
# The assigned sales user of PROJECT_ID (and responsible user of the unassigned project).
AS_SALES = principal_header(Principal(user_id=SALES, roles={"sales"}))
# A sales user without any assignment: the projects are out of scope (404).
AS_OTHER_SALES = principal_header(Principal(user_id=OTHER_SALES, roles={"sales"}))
# A manager who manages both projects: may read, may not edit or issue.
AS_MANAGER = principal_header(
    Principal(
        user_id=uuid4(), roles={"manager"}, managed_projects={PROJECT_ID, UNASSIGNED_PROJECT_ID}
    )
)
AS_CS_AGENT = principal_header(Principal(user_id=uuid4(), roles={"cs_agent"}))
V1 = {"If-Match": '"1"'}


def request_row(request_id: UUID, **overrides) -> dict:
    return {
        "id": request_id,
        "project_id": PROJECT_ID,
        "required_role_id": ROLE_ID,
        "role_name": "marine construction",
        "organization_id": CONSTRUCTION_ORG,
        "organization_name": "construction",
        "organization_dept_code": "FIELD_SVC",
        "participation": "LEAD",
        "status": "DRAFT",
        "version": 1,
        "due_at": BASE,
        "due_undecided": False,
        "message": "please recommend",
        "issued_by_user_id": None,
        "issued_at": None,
        "created_at": BASE,
        "updated_at": BASE,
        "data_origin": "synthetic",
        "derivation_kind": "SYSTEM",
        **overrides,
    }


def initial_rows() -> dict:
    rows = [
        request_row(DRAFT_ID),
        request_row(ISSUED_ID, status="ISSUED", version=3, issued_by_user_id=SALES, issued_at=BASE),
        request_row(UNDECIDED_ID, due_at=None, due_undecided=True),
        request_row(NO_DUE_ID, due_at=None),
        request_row(ORPHAN_ID, project_id=UNASSIGNED_PROJECT_ID),
    ]
    return {row["id"]: row for row in rows}


@pytest.fixture
def store(monkeypatch):
    commands = {}

    async def replay(conn, actor, request_id, operation, key):
        return commands.get((actor, request_id, operation, key))

    async def remember(conn, actor, request_id, operation, key, payload, result):
        commands[actor, request_id, operation, key] = {"payload": payload, "result": result}

    monkeypatch.setattr(routes.commands, "replay", replay)
    monkeypatch.setattr(routes.commands, "remember", remember)
    requests = initial_rows()
    revisions: list = []
    roles = {ROLE_ID: {"status": "DRAFTING", "reviewed_by_user_id": None}}

    @asynccontextmanager
    async def fake_connection(settings):
        yield object()

    async def list_for_project(conn, project_id):
        return [dict(r) for r in requests.values() if r["project_id"] == project_id]

    async def get_request(conn, request_id):
        row = requests.get(request_id)
        return dict(row) if row else None

    async def get_project_scope(conn, project_id):
        return PROJECTS.get(project_id)

    async def get_organization(conn, organization_id):
        return ORGANIZATIONS.get(organization_id)

    async def update_request(conn, request_id, version, values):
        assert requests[request_id]["version"] == version
        # The database sets updated_at; the route must echo the stored value.
        requests[request_id].update(values, version=version + 1, updated_at=UPDATED)
        return UPDATED

    async def mark_role_issued(conn, role_id, actor_user_id, issued_at):
        if roles[role_id]["status"] == "DRAFTING":
            roles[role_id] = {"status": "ISSUED", "reviewed_by_user_id": actor_user_id}

    async def insert_revision(conn, revision_id, request_id, version, action, actor, changes):
        revisions.append(
            {
                "request_id": request_id,
                "version": version,
                "action": action,
                "actor": actor,
                "changes": changes,
            }
        )

    monkeypatch.setattr(routes, "connection", fake_connection)
    for name, fake in {
        "list_for_project": list_for_project,
        "get_request": get_request,
        "lock_request": get_request,
        "get_project_scope": get_project_scope,
        "get_organization": get_organization,
        "update_request": update_request,
        "mark_role_issued": mark_role_issued,
        "insert_revision": insert_revision,
    }.items():
        monkeypatch.setattr(repository, name, fake)
    return {"requests": requests, "revisions": revisions, "roles": roles}


@pytest.fixture
def client(store):
    app = create_app(Settings(_env_file=None, environment="test"))
    with TestClient(app, headers=AS_SALES) as test_client:
        yield test_client


def detail_path(request_id: UUID) -> str:
    return f"/api/v1/tft/staffing-requests/{request_id}"


def test_list_returns_the_project_requests_in_creation_order(client):
    result = client.get(f"/api/v1/tft/projects/{PROJECT_ID}/staffing-requests")
    assert result.status_code == 200
    items = result.json()["data"]["items"]
    assert [item["id"] for item in items] == [
        str(DRAFT_ID),
        str(ISSUED_ID),
        str(UNDECIDED_ID),
        str(NO_DUE_ID),
    ]
    assert items[0]["organization_dept_code"] == "FIELD_SVC"
    assert items[0]["role_name"] == "marine construction"
    assert "next_cursor" not in result.json()["data"]


def test_list_of_unknown_or_out_of_scope_project_is_not_found(client):
    for path, headers in (
        (f"/api/v1/tft/projects/{uuid4()}/staffing-requests", AS_SALES),
        (f"/api/v1/tft/projects/{PROJECT_ID}/staffing-requests", AS_OTHER_SALES),
    ):
        result = client.get(path, headers=headers)
        assert result.status_code == 404
        assert result.json()["error"]["code"] == "TFT_PROJECT_NOT_FOUND"


def test_detail_carries_version_as_etag(client):
    result = client.get(detail_path(ISSUED_ID))
    assert result.status_code == 200
    assert result.headers["ETag"] == '"3"'
    data = result.json()["data"]
    assert data["version"] == 3 and data["status"] == "ISSUED"
    assert data["due_undecided"] is False


def test_unknown_request_returns_feature_error(client):
    for response in (
        client.get(detail_path(uuid4())),
        client.patch(detail_path(uuid4()), json={"message": "x"}),
        client.post(f"{detail_path(uuid4())}/issue"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "TFT_STAFFING_REQUEST_NOT_FOUND"


def test_request_of_an_out_of_scope_project_is_not_found(client, store):
    # A sales user with no assignment: the request exists but its project is out of scope.
    for response in (
        client.get(detail_path(DRAFT_ID), headers=AS_OTHER_SALES),
        client.patch(detail_path(DRAFT_ID), json={"message": "x"}, headers=AS_OTHER_SALES | V1),
        client.post(f"{detail_path(DRAFT_ID)}/issue", headers=AS_OTHER_SALES | V1),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "TFT_STAFFING_REQUEST_NOT_FOUND"
    assert store["revisions"] == []


def test_manager_of_the_project_reads_but_may_not_edit_or_issue(client, store):
    assert client.get(detail_path(DRAFT_ID), headers=AS_MANAGER).status_code == 200
    listed = client.get(f"/api/v1/tft/projects/{PROJECT_ID}/staffing-requests", headers=AS_MANAGER)
    assert listed.status_code == 200
    for response in (
        client.patch(detail_path(DRAFT_ID), json={"message": "x"}, headers=AS_MANAGER | V1),
        client.post(f"{detail_path(DRAFT_ID)}/issue", headers=AS_MANAGER | V1),
    ):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
    assert store["requests"][DRAFT_ID]["status"] == "DRAFT"
    assert store["revisions"] == []


def test_c_s_agent_has_no_tft_scope(client, store):
    for response in (
        client.get(f"/api/v1/tft/projects/{PROJECT_ID}/staffing-requests", headers=AS_CS_AGENT),
        client.get(detail_path(DRAFT_ID), headers=AS_CS_AGENT),
        client.patch(detail_path(DRAFT_ID), json={"message": "x"}, headers=AS_CS_AGENT | V1),
        client.post(f"{detail_path(DRAFT_ID)}/issue", headers=AS_CS_AGENT | V1),
    ):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
    assert store["revisions"] == []


def test_edit_bumps_version_and_records_from_and_to(client, store):
    result = client.patch(
        detail_path(DRAFT_ID),
        json={"organization_id": str(QUALITY_ORG), "participation": "SUPPORT", "message": None},
        headers=V1,
    )
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["version"] == 2
    assert result.headers["ETag"] == '"2"'
    assert data["organization_id"] == str(QUALITY_ORG)
    assert data["organization_name"] == "quality"
    assert data["organization_dept_code"] == "QA_CLAIM"
    assert data["participation"] == "SUPPORT"
    assert data["message"] is None
    assert data["status"] == "DRAFT"
    # updated_at is the stored value, so the response equals the next GET.
    assert data["updated_at"] == UPDATED.isoformat().replace("+00:00", "Z")
    assert client.get(detail_path(DRAFT_ID)).json()["data"]["updated_at"] == data["updated_at"]
    revision = store["revisions"][-1]
    assert (revision["action"], revision["version"], revision["actor"]) == ("EDIT", 2, SALES)
    assert revision["changes"]["organization_id"] == {
        "from": str(CONSTRUCTION_ORG),
        "to": str(QUALITY_ORG),
    }
    assert revision["changes"]["message"] == {"from": "please recommend", "to": None}


def test_edit_marks_the_due_date_undecided(client, store):
    result = client.patch(
        detail_path(DRAFT_ID),
        json={"due_at": None, "due_undecided": True},
        headers={"If-Match": "1"},
    )
    assert result.status_code == 200
    data = result.json()["data"]
    assert data["due_at"] is None and data["due_undecided"] is True
    assert store["revisions"][-1]["changes"]["due_undecided"] == {"from": False, "to": True}


def test_due_date_and_undecided_cannot_both_be_set(client, store):
    result = client.patch(detail_path(DRAFT_ID), json={"due_undecided": True}, headers=V1)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "VALIDATION_ERROR"
    assert store["requests"][DRAFT_ID]["version"] == 1


def test_unchanged_values_do_not_create_a_version(client, store):
    result = client.patch(detail_path(DRAFT_ID), json={"participation": "LEAD"}, headers=V1)
    assert result.status_code == 200
    assert result.json()["data"]["version"] == 1
    assert store["revisions"] == []


def test_edit_requires_the_version_header(client, store):
    result = client.patch(detail_path(DRAFT_ID), json={"message": "x"})
    assert result.status_code == 428
    assert result.json()["error"]["code"] == "VERSION_REQUIRED"
    assert store["requests"][DRAFT_ID]["message"] == "please recommend"


@pytest.mark.parametrize("stale", ['"2"', "0", '"abc"', 'W/"9"'])
def test_stale_or_malformed_version_is_rejected_without_writing(client, store, stale):
    result = client.patch(detail_path(DRAFT_ID), json={"message": "x"}, headers={"If-Match": stale})
    assert result.status_code == 412
    assert result.json()["error"]["code"] == "VERSION_CONFLICT"
    assert store["requests"][DRAFT_ID]["version"] == 1
    assert store["revisions"] == []


@pytest.mark.parametrize("value", ['"²"', "١", '"٣"'])
def test_non_ascii_digits_in_if_match_are_a_version_conflict_not_a_crash(value):
    # A real server decodes header bytes as latin-1, so such values can reach the route.
    # str.isdigit() accepts them while int() raises; the check must answer 412, not 500.
    # The test client cannot send non-ASCII headers, so the check is called directly.
    with pytest.raises(ApplicationError) as error:
        routes.check_version(value, 1)
    assert (error.value.status_code, error.value.code) == (412, "VERSION_CONFLICT")


def test_edit_of_an_issued_request_is_an_invalid_transition(client, store):
    result = client.patch(
        detail_path(ISSUED_ID), json={"message": "x"}, headers={"If-Match": '"3"'}
    )
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "INVALID_TRANSITION"
    assert store["requests"][ISSUED_ID]["message"] == "please recommend"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": "ISSUED"},
        {"required_role_id": str(uuid4())},
        {"organization_id": None},
        {"participation": "OWNER"},
        {"due_at": "2026-11-01T09:00:00"},
        {"version": 2},
    ],
)
def test_edit_rejects_immutable_missing_or_invalid_fields(client, body):
    result = client.patch(detail_path(DRAFT_ID), json=body, headers=V1)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "VALIDATION_ERROR"


def test_edit_rejects_an_organization_outside_the_project_company(client, store):
    for organization in (OTHER_COMPANY_ORG, uuid4()):
        result = client.patch(
            detail_path(DRAFT_ID), json={"organization_id": str(organization)}, headers=V1
        )
        assert result.status_code == 422
        assert result.json()["error"]["code"] == "VALIDATION_ERROR"
    assert store["requests"][DRAFT_ID]["organization_id"] == CONSTRUCTION_ORG


def test_issue_sets_issuer_time_status_and_revision(client, store):
    # The frontend sends Idempotency-Key on every state-making POST (api-contract); it is
    # replayed without creating another revision after a lost response.
    headers = {**V1, "Idempotency-Key": str(uuid4())}
    result = client.post(f"{detail_path(DRAFT_ID)}/issue", headers=headers)
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["status"] == "ISSUED"
    assert data["version"] == 2
    assert result.headers["ETag"] == '"2"'
    assert data["issued_by_user_id"] == str(SALES)
    assert data["issued_at"] is not None
    assert data["updated_at"] == UPDATED.isoformat().replace("+00:00", "Z")
    revision = store["revisions"][-1]
    assert (revision["action"], revision["version"], revision["actor"]) == ("ISSUE", 2, SALES)
    assert revision["changes"]["status"] == {"from": "DRAFT", "to": "ISSUED"}
    # The role the request belongs to leaves DRAFTING with the issuing sales user as reviewer.
    assert store["roles"][ROLE_ID] == {"status": "ISSUED", "reviewed_by_user_id": SALES}
    replay = client.post(f"{detail_path(DRAFT_ID)}/issue", headers=headers)
    assert replay.status_code == 200
    assert replay.json() == result.json()
    assert replay.headers["ETag"] == result.headers["ETag"]
    assert len(store["revisions"]) == 1
    conflict = client.post(f"{detail_path(DRAFT_ID)}/issue", headers=headers | {"If-Match": '"2"'})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    denied = client.post(f"{detail_path(DRAFT_ID)}/issue", headers=headers | AS_MANAGER)
    assert denied.status_code == 403
    hidden = client.post(f"{detail_path(DRAFT_ID)}/issue", headers=headers | AS_OTHER_SALES)
    assert hidden.status_code == 404


def test_issue_accepts_an_undecided_due_date(client):
    result = client.post(f"{detail_path(UNDECIDED_ID)}/issue", headers=V1)
    assert result.status_code == 200
    assert result.json()["data"]["due_undecided"] is True


def test_issue_without_due_date_or_undecided_mark_needs_evidence(client, store):
    result = client.post(f"{detail_path(NO_DUE_ID)}/issue", headers=V1)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "EVIDENCE_REQUIRED"
    assert store["requests"][NO_DUE_ID]["status"] == "DRAFT"


def test_issue_is_forbidden_while_the_project_has_no_sales_user(client, store):
    # SALES is the responsible user of this project (so it is visible) but not its sales user.
    result = client.post(f"{detail_path(ORPHAN_ID)}/issue", headers=V1)
    assert result.status_code == 403
    assert result.json()["error"]["code"] == "FORBIDDEN"
    assert store["requests"][ORPHAN_ID]["status"] == "DRAFT"


def test_sales_user_assigned_elsewhere_may_not_issue_even_when_the_project_is_visible(
    client, store
):
    # A manager-role user who is also the assigned sales user: visibility comes from
    # management, issuing still requires the sales role and the assignment.
    headers = principal_header(
        Principal(user_id=SALES, roles={"manager"}, managed_projects={PROJECT_ID})
    )
    result = client.post(f"{detail_path(DRAFT_ID)}/issue", headers=headers | V1)
    assert result.status_code == 403
    assert store["revisions"] == []


@pytest.mark.parametrize("headers", [{}, {"X-Team4-Principal": "{not json"}])
def test_requests_without_a_valid_principal_are_unauthenticated(store, headers):
    app = create_app(Settings(_env_file=None, environment="test"))
    with TestClient(app) as anonymous:
        for response in (
            anonymous.get(detail_path(DRAFT_ID), headers=headers),
            anonymous.post(f"{detail_path(DRAFT_ID)}/issue", headers=headers | V1),
            anonymous.patch(detail_path(DRAFT_ID), json={"message": "x"}, headers=headers | V1),
        ):
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert store["revisions"] == []


def test_issue_of_an_issued_request_is_an_invalid_transition(client):
    result = client.post(f"{detail_path(ISSUED_ID)}/issue", headers={"If-Match": '"3"'})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "INVALID_TRANSITION"


def test_issue_requires_a_matching_version(client, store):
    missing = client.post(f"{detail_path(DRAFT_ID)}/issue")
    assert missing.status_code == 428
    stale = client.post(f"{detail_path(DRAFT_ID)}/issue", headers={"If-Match": '"7"'})
    assert stale.status_code == 412
    assert store["requests"][DRAFT_ID]["status"] == "DRAFT"


def test_routes_are_closed_in_production_whatever_the_header_says(monkeypatch):
    async def must_not_query(*args):
        raise AssertionError("production must be rejected before any database access")

    for name in ("list_for_project", "get_request", "lock_request", "get_project_scope"):
        monkeypatch.setattr(repository, name, must_not_query)
    app = create_app(Settings(_env_file=None, environment="production"))
    with TestClient(app, headers=AS_SALES) as production:
        responses = [
            production.get(f"/api/v1/tft/projects/{PROJECT_ID}/staffing-requests"),
            production.get(detail_path(DRAFT_ID)),
            production.patch(detail_path(DRAFT_ID), json={"message": "x"}, headers=V1),
            production.post(f"{detail_path(DRAFT_ID)}/issue", headers=V1),
        ]
    for result in responses:
        assert result.status_code == 404
        assert result.json()["error"]["code"] == "NOT_FOUND"
