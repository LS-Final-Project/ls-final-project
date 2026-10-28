"""KAN-102 ticket creation, correction, version conflicts and audit, with an in-memory store."""

from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.cs.main import create_app
from backend.api.cs.tickets import repository, routes
from backend.api.cs.units import repository as units
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

CS_AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})

NEW = {
    "channel": "EMAIL",
    "body_language": "en",
    "customer_external_id": "CUST-0374",
    "product_code": "TRF-OIL-1000",
    "subject": "Oil leakage",
    "body": "Oil leakage at gasket after delivery.",
}


@pytest.fixture
def store(monkeypatch):
    tickets: dict = {}
    audit: list = []

    @asynccontextmanager
    async def fake_connection(settings):
        yield object()

    async def insert_ticket(conn, values):
        tickets[values["id"]] = {**values, "version": 1, "workflow_state": "RECEIVED"}

    async def lock_ticket(conn, ticket_id):
        row = tickets.get(ticket_id)
        return dict(row) if row else None

    async def get_ticket(conn, ticket_id):
        return await lock_ticket(conn, ticket_id)

    async def update_ticket(conn, ticket_id, version, values):
        assert tickets[ticket_id]["version"] == version
        tickets[ticket_id].update(values, version=version + 1)

    async def insert_audit(conn, event_id, ticket_id, action, version, actor_user_id, changes):
        audit.append(
            {
                "id": event_id,
                "ticket_id": ticket_id,
                "action": action,
                "version": version,
                "actor_user_id": actor_user_id,
                "changes": changes,
                "occurred_at": "2026-10-23T00:00:00Z",
            }
        )

    async def list_audit(conn, ticket_id):
        return [event for event in audit if event["ticket_id"] == ticket_id]

    async def no_case(conn, ticket_id):
        return None

    async def no_confirmed_unit(conn, ticket_id):
        return None

    monkeypatch.setattr(routes, "connection", fake_connection)

    async def not_transferred(conn, ticket_id, departments):
        return False

    monkeypatch.setattr(units, "confirmed_link", no_confirmed_unit)
    monkeypatch.setattr(repository, "transferred_to", not_transferred)
    for name, fake in {
        "insert_ticket": insert_ticket,
        "lock_ticket": lock_ticket,
        "get_ticket": get_ticket,
        "update_ticket": update_ticket,
        "insert_audit": insert_audit,
        "list_audit": list_audit,
        "get_resolution_case": no_case,
    }.items():
        monkeypatch.setattr(repository, name, fake)
    return tickets, audit


@pytest.fixture
def client(store):
    app = create_app(Settings(_env_file=None, environment="test"))
    with TestClient(app, headers=principal_header(CS_AGENT)) as test_client:
        yield test_client


def create(client) -> dict:
    result = client.post("/api/v1/cs/tickets", json=NEW)
    assert result.status_code == 201
    return result.json()["data"]


def test_create_sets_server_owned_fields_and_audits_without_the_body(client, store):
    data = create(client)
    assert data["version"] == 1
    assert data["external_id"] is None
    assert (data["data_origin"], data["derivation_kind"]) == ("demo", "SYSTEM")
    _, audit = store
    assert [(e["action"], e["version"]) for e in audit] == [("create", 1)]
    assert "body" not in audit[0]["changes"]
    assert audit[0]["changes"]["product_code"] == {"to": "TRF-OIL-1000"}


@pytest.mark.parametrize(
    "extra",
    [{"data_origin": "real"}, {"external_id": "CSV1-1"}, {"version": 3}, {"known_at": "2020"}],
)
def test_create_rejects_server_owned_fields(client, extra):
    assert client.post("/api/v1/cs/tickets", json={**NEW, **extra}).status_code == 422


# KAN-178 customer ID convention: ASCII alphanumeric first, then alphanumerics, ".", "_", "-".
@pytest.mark.parametrize(
    "customer",
    ["", ".CUST", "-CUST", "_CUST", "CUST 01", "고객-01", "CUST/01", "CUST-01\n", "C" * 65],
)
def test_customer_ids_outside_the_convention_are_rejected(client, customer):
    created = client.post("/api/v1/cs/tickets", json={**NEW, "customer_external_id": customer})
    assert created.status_code == 422
    ticket = create(client)
    body = {"version": 1, "customer_external_id": customer}
    assert client.patch(f"/api/v1/cs/tickets/{ticket['id']}", json=body).status_code == 422


@pytest.mark.parametrize("customer", ["A", "C" * 64, "cust.01_a-B"])
def test_customer_ids_within_the_convention_are_accepted(client, customer):
    created = client.post("/api/v1/cs/tickets", json={**NEW, "customer_external_id": customer})
    assert created.status_code == 201
    assert created.json()["data"]["customer_external_id"] == customer


def test_update_bumps_version_and_records_from_and_to(client, store):
    ticket = create(client)
    result = client.patch(
        f"/api/v1/cs/tickets/{ticket['id']}", json={"version": 1, "product_code": "TRF-OIL-2000"}
    )
    assert result.status_code == 200
    assert result.json()["data"]["version"] == 2
    assert result.json()["data"]["product_code"] == "TRF-OIL-2000"
    history = client.get(f"/api/v1/cs/tickets/{ticket['id']}/audit").json()["data"]["items"]
    assert [(e["action"], e["version"]) for e in history] == [("create", 1), ("update", 2)]
    assert history[1]["changes"] == {"product_code": {"from": "TRF-OIL-1000", "to": "TRF-OIL-2000"}}


def test_stale_version_is_rejected_without_writing(client, store):
    ticket = create(client)
    path = f"/api/v1/cs/tickets/{ticket['id']}"
    assert client.patch(path, json={"version": 1, "subject": "first"}).status_code == 200
    stale = client.patch(path, json={"version": 1, "subject": "second"})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CS_TICKET_VERSION_CONFLICT"
    tickets, audit = store
    assert tickets[UUID(ticket["id"])]["subject"] == "first"
    assert len(audit) == 2


def test_unchanged_values_do_not_create_a_version(client, store):
    ticket = create(client)
    result = client.patch(
        f"/api/v1/cs/tickets/{ticket['id']}", json={"version": 1, "subject": NEW["subject"]}
    )
    assert result.json()["data"]["version"] == 1
    assert len(store[1]) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"version": 1},
        {"version": 1, "body": "rewritten"},
        {"version": 1, "channel": "CHAT"},
        {"version": 1, "received_at": "2020-01-01T00:00:00Z"},
        {"version": 1, "product_code": None},
        {"subject": "no version"},
        {"version": 0, "subject": "x"},
    ],
)
def test_update_rejects_immutable_missing_or_invalid_fields(client, body):
    ticket = create(client)
    assert client.patch(f"/api/v1/cs/tickets/{ticket['id']}", json=body).status_code == 422


def test_subject_can_be_cleared(client):
    ticket = create(client)
    result = client.patch(
        f"/api/v1/cs/tickets/{ticket['id']}", json={"version": 1, "subject": None}
    )
    assert result.json()["data"]["subject"] is None


def test_update_and_audit_of_unknown_ticket_are_not_found(client):
    unknown = uuid4()
    patched = client.patch(f"/api/v1/cs/tickets/{unknown}", json={"version": 1, "subject": "x"})
    assert patched.json()["error"]["code"] == "CS_TICKET_NOT_FOUND"
    assert client.get(f"/api/v1/cs/tickets/{unknown}/audit").status_code == 404


def test_writes_are_closed_in_production(store):
    app = create_app(Settings(_env_file=None, environment="production"))
    with TestClient(app, headers=principal_header(CS_AGENT)) as production:
        assert production.post("/api/v1/cs/tickets", json=NEW).status_code == 404
        assert store == ({}, [])


def as_user(**fields) -> dict:
    return principal_header(Principal(user_id=uuid4(), **fields))


def test_audit_records_who_made_each_change(client, store):
    ticket = create(client)
    client.patch(f"/api/v1/cs/tickets/{ticket['id']}", json={"version": 1, "subject": "x"})
    _, audit = store
    assert [e["actor_user_id"] for e in audit] == [CS_AGENT.user_id, CS_AGENT.user_id]
    history = client.get(f"/api/v1/cs/tickets/{ticket['id']}/audit").json()["data"]["items"]
    assert {e["actor_user_id"] for e in history} == {str(CS_AGENT.user_id)}


def test_c_s_manager_sees_but_cannot_change_tickets(client, store):
    ticket = create(client)
    manager = as_user(roles={"manager"}, managed_departments={"GCS_L1"})
    path = f"/api/v1/cs/tickets/{ticket['id']}"
    assert client.get(f"{path}/audit", headers=manager).status_code == 200
    assert client.post("/api/v1/cs/tickets", json=NEW, headers=manager).status_code == 403
    patched = client.patch(path, json={"version": 1, "subject": "x"}, headers=manager)
    assert patched.status_code == 403
    assert len(store[0]) == 1 and len(store[1]) == 1


@pytest.mark.parametrize(
    "user",
    [{"roles": {"dept_staff"}, "dept": "TS_TRF"}, {"roles": {"sales"}}],
)
def test_users_outside_the_ticket_scope_get_not_found(client, store, user):
    ticket = create(client)
    headers = as_user(**user)
    path = f"/api/v1/cs/tickets/{ticket['id']}"
    assert (
        client.patch(path, json={"version": 1, "subject": "x"}, headers=headers).status_code == 404
    )
    assert client.get(f"{path}/audit", headers=headers).status_code == 404
    assert client.post("/api/v1/cs/tickets", json=NEW, headers=headers).status_code == 403
