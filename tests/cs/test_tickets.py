"""KAN-102 ticket list and detail routes, with the database replaced by in-memory rows."""

import base64
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.cs.main import create_app
from backend.api.cs.tickets import repository, routes
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

BASE = datetime(2025, 10, 1, tzinfo=UTC)
CS_AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})


def ticket_row(index: int) -> dict:
    return {
        "id": UUID(int=index + 1),
        "external_id": f"CSV1-{index:06d}",
        "version": 1,
        "subject": None if index % 2 else f"subject {index}",
        "data_origin": "synthetic",
        "derivation_kind": "PROVIDED",
        "received_at": BASE + timedelta(hours=index),
        "known_at": BASE + timedelta(hours=index),
        "channel": "CHAT" if index % 2 else "EMAIL",
        "body_language": "en",
        "customer_external_id": "CUST-0001",
        "product_code": "TRF-OIL-1000",
        "body": "body",
        "fw_version_reported": None,
        "error_code_reported": None,
        "workflow_state": "RECEIVED",
    }


ROWS = [ticket_row(i) for i in range(5)]
# Departments each ticket was ever transferred to (cs.ticket_transfers).
TRANSFERS = {ROWS[3]["id"]: {"TS_TRF"}}
CASE = {
    "id": uuid4(),
    "external_id": "RC-0007",
    "data_origin": "synthetic",
    "derivation_kind": "PROVIDED",
    "resolved_at": BASE,
    "known_at": BASE,
    "product_code": "TRF-OIL-1000",
    "fault_id": "FLT-067",
    "fw_version_at_issue": None,
    "symptom": "Oil leakage at gasket",
    "root_cause": "Gasket damage during transport",
    "root_cause_type": "WEAR_PART",
    "action_taken": "Replace gasket and top up oil",
    "onsite_visit": True,
    "rma_no": None,
    "fw_fixed_version": None,
    "resolved_by_dept": "FIELD_SVC",
    "recurrence_prevention": None,
}


@pytest.fixture
def client(monkeypatch):
    @asynccontextmanager
    async def fake_connection(settings):
        yield object()

    async def fake_list(conn, limit, after, departments=None, *, filters=None, ascending=False):
        rows = sorted(ROWS, key=lambda r: (r["received_at"], r["id"]), reverse=True)
        if departments is not None:
            rows = [r for r in rows if TRANSFERS.get(r["id"], set()) & departments]
        if after:
            rows = [r for r in rows if (r["received_at"], r["id"]) < after]
        return rows[: limit + 1]

    async def fake_get(conn, ticket_id):
        return next((r for r in ROWS if r["id"] == ticket_id), None)

    async def fake_case(conn, ticket_id):
        return CASE if ticket_id == ROWS[0]["id"] else None

    monkeypatch.setattr(routes, "connection", fake_connection)
    monkeypatch.setattr(repository, "list_tickets", fake_list)
    monkeypatch.setattr(repository, "get_ticket", fake_get)

    async def fake_transferred_to(conn, ticket_id, departments):
        return bool(TRANSFERS.get(ticket_id, set()) & departments)

    monkeypatch.setattr(repository, "get_resolution_case", fake_case)
    monkeypatch.setattr(repository, "transferred_to", fake_transferred_to)
    app = create_app(Settings(_env_file=None, environment="test"))
    with TestClient(app, headers=principal_header(CS_AGENT)) as test_client:
        yield test_client


def test_list_pages_newest_first_without_gaps_or_duplicates(client):
    seen = []
    cursor = None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = client.get("/api/v1/cs/tickets", params=params).json()["data"]
        seen.extend(item["external_id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [f"CSV1-{i:06d}" for i in reversed(range(5))]


def test_chat_ticket_without_subject_is_returned_as_null(client):
    items = client.get("/api/v1/cs/tickets").json()["data"]["items"]
    chat = next(item for item in items if item["channel"] == "CHAT")
    assert chat["subject"] is None


def b64(value) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!",
        b64({"r": "2025-10-01"}),
        # Review finding: a numeric id used to raise AttributeError (500).
        b64({"r": "2025-10-01T00:00:00+00:00", "i": 5}),
        b64({"r": 1, "i": str(UUID(int=1))}),
        b64(["2025-10-01T00:00:00+00:00", str(UUID(int=1))]),
        b64({"r": "2025-10-01T00:00:00", "i": str(UUID(int=1))}),
        b64({"r": "2025-10-01T00:00:00+00:00", "i": str(UUID(int=1)), "x": 1}),
        b64({"r": "2025-10-01T00:00:00+00:00", "i": "not-a-uuid"}),
        # Review finding: normalising this offset to UTC overflows the datetime range.
        b64({"r": "0001-01-01T00:00:00+23:59", "i": str(UUID(int=1))}),
    ],
)
def test_invalid_or_naive_cursor_is_rejected(client, cursor):
    result = client.get("/api/v1/cs/tickets", params={"cursor": cursor})
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "CS_INVALID_CURSOR"


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_is_bounded(client, limit):
    assert client.get("/api/v1/cs/tickets", params={"limit": limit}).status_code == 422


def test_detail_includes_known_resolution_case(client):
    body = client.get(f"/api/v1/cs/tickets/{ROWS[0]['id']}").json()["data"]
    assert body["external_id"] == "CSV1-000000"
    assert body["resolution_case"]["fault_id"] == "FLT-067"
    no_case = client.get(f"/api/v1/cs/tickets/{ROWS[1]['id']}").json()["data"]
    assert no_case["resolution_case"] is None


def test_unknown_ticket_returns_feature_error(client):
    result = client.get(f"/api/v1/cs/tickets/{uuid4()}")
    assert result.status_code == 404
    assert result.json()["error"]["code"] == "CS_TICKET_NOT_FOUND"


def test_routes_are_closed_in_production_until_access_checks_exist(monkeypatch):
    async def must_not_query(*args):
        raise AssertionError("production must be rejected before any database access")

    monkeypatch.setattr(repository, "list_tickets", must_not_query)
    monkeypatch.setattr(repository, "get_ticket", must_not_query)
    app = create_app(Settings(_env_file=None, environment="production"))
    with TestClient(app, headers=principal_header(CS_AGENT)) as production:
        for path in ("/api/v1/cs/tickets", f"/api/v1/cs/tickets/{ROWS[0]['id']}"):
            result = production.get(path)
            assert result.status_code == 404
            assert result.json()["error"]["code"] == "NOT_FOUND"


def as_user(**fields) -> dict:
    return principal_header(Principal(user_id=uuid4(), **fields))


C_S_MANAGER = {"roles": {"manager"}, "managed_departments": {"GCS_L1"}}
OTHER_MANAGER = {"roles": {"manager"}, "managed_departments": {"TS_TRF"}}
DEPT_STAFF = {"roles": {"dept_staff"}, "dept": "TS_TRF"}


def test_c_s_department_manager_sees_every_ticket(client):
    headers = as_user(**C_S_MANAGER)
    assert len(client.get("/api/v1/cs/tickets", headers=headers).json()["data"]["items"]) == 5
    assert client.get(f"/api/v1/cs/tickets/{ROWS[0]['id']}", headers=headers).status_code == 200


@pytest.mark.parametrize("user", [OTHER_MANAGER, DEPT_STAFF])
def test_transfer_department_users_see_only_tickets_transferred_to_them(client, user):
    headers = as_user(**user)
    body = client.get("/api/v1/cs/tickets", headers=headers).json()["data"]
    assert [item["id"] for item in body["items"]] == [str(ROWS[3]["id"])]
    assert client.get(f"/api/v1/cs/tickets/{ROWS[3]['id']}", headers=headers).status_code == 200
    detail = client.get(f"/api/v1/cs/tickets/{ROWS[0]['id']}", headers=headers)
    # Same answer as a missing ticket, so the ticket's existence is not revealed.
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "CS_TICKET_NOT_FOUND"


def test_department_without_transfers_sees_nothing(client):
    headers = as_user(roles={"dept_staff"}, dept="TS_LV")
    body = client.get("/api/v1/cs/tickets", headers=headers).json()["data"]
    assert body == {"items": [], "next_cursor": None}


def test_sales_has_no_c_s_scope(client):
    headers = as_user(roles={"sales"})
    assert client.get("/api/v1/cs/tickets", headers=headers).status_code == 403
    assert client.get(f"/api/v1/cs/tickets/{ROWS[0]['id']}", headers=headers).status_code == 404


def test_roles_combine(client):
    headers = as_user(roles={"sales", "cs_agent"})
    assert len(client.get("/api/v1/cs/tickets", headers=headers).json()["data"]["items"]) == 5


def test_filters_reach_the_repository_parsed(client, monkeypatch):
    """쉼표 상태 목록·오프셋 없는 시각(UTC로 읽음)·정렬이 저장소 호출로 그대로 간다."""
    seen = {}

    async def capture(conn, limit, after, departments=None, *, filters=None, ascending=False):
        seen.update(filters=filters, ascending=ascending)
        return []

    monkeypatch.setattr(repository, "list_tickets", capture)
    response = client.get(
        "/api/v1/cs/tickets",
        params={
            "state": "IN_REVIEW, TRANSFERRED",
            "received_from": "2026-10-01T00:00:00",
            "q": "leak",
            "sort": "received_at_asc",
        },
    )
    assert response.status_code == 200
    filters = seen["filters"]
    assert filters.states == {"IN_REVIEW", "TRANSFERRED"} and filters.q == "leak"
    assert filters.received_from == datetime(2026, 10, 1, tzinfo=UTC)
    assert seen["ascending"] is True


@pytest.mark.parametrize("state", ["", "IN_REVIEW,,CLOSED", "OPEN", "in_review"])
def test_unknown_states_are_rejected_not_ignored(client, state):
    response = client.get("/api/v1/cs/tickets", params={"state": state})
    assert (response.status_code, response.json()["error"]["code"]) == (400, "CS_INVALID_FILTER")


def test_a_cursor_cannot_change_direction(client):
    newest = client.get("/api/v1/cs/tickets", params={"limit": 2}).json()["data"]["next_cursor"]
    response = client.get(
        "/api/v1/cs/tickets", params={"cursor": newest, "sort": "received_at_asc"}
    )
    assert (response.status_code, response.json()["error"]["code"]) == (400, "CS_INVALID_CURSOR")
