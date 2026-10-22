"""KAN-102 ticket list and detail routes, with the database replaced by in-memory rows."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.cs.main import create_app
from backend.api.cs.tickets import repository, routes
from backend.shared.settings import Settings

BASE = datetime(2025, 10, 1, tzinfo=UTC)


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
    }


ROWS = [ticket_row(i) for i in range(5)]
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

    async def fake_list(conn, limit, after):
        rows = sorted(ROWS, key=lambda r: (r["received_at"], r["id"]), reverse=True)
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
    monkeypatch.setattr(repository, "get_resolution_case", fake_case)
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as test_client:
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


@pytest.mark.parametrize("cursor", ["not-base64!", "eyJyIjogIjIwMjUtMTAtMDEifQ=="])
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
