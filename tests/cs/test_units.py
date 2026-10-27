"""KAN-102 unit routes: role and input checks that run before any database access."""

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.cs.main import create_app
from backend.api.cs.units import routes
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

TICKET = uuid4()
UNIT = uuid4()
LINK = {"link_status": "CONFIRMED", "link_basis": "SERIAL_PROVIDED", "version": None}


@pytest.fixture
def client(monkeypatch):
    @asynccontextmanager
    async def no_database(settings):
        raise AssertionError("The request must be refused before the database is used.")
        yield

    monkeypatch.setattr(routes, "connection", no_database)
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as test_client:
        yield test_client


def as_user(**fields) -> dict:
    return principal_header(Principal(user_id=uuid4(), **fields))


CS_AGENT = as_user(roles={"cs_agent"})
PATHS = (
    ("GET", f"/api/v1/cs/tickets/{TICKET}/units"),
    ("GET", f"/api/v1/cs/tickets/{TICKET}/unit-history"),
    ("PUT", f"/api/v1/cs/tickets/{TICKET}/units/{UNIT}"),
)


@pytest.mark.parametrize(
    "user",
    [{"roles": {"dept_staff"}, "dept": "TS_TRF"}, {"roles": {"sales"}}, {"roles": {"manager"}}],
)
@pytest.mark.parametrize(("method", "path"), PATHS)
def test_users_outside_the_ticket_scope_get_not_found(client, user, method, path):
    response = client.request(method, path, json=LINK, headers=as_user(**user))
    assert response.status_code == 404


def test_c_s_manager_cannot_link_units(client):
    manager = as_user(roles={"manager"}, managed_departments={"GCS_L1"})
    method, path = PATHS[2]
    assert client.request(method, path, json=LINK, headers=manager).status_code == 403


@pytest.mark.parametrize(("method", "path"), PATHS)
def test_missing_user_is_unauthorized(client, method, path):
    assert client.request(method, path, json=LINK).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {**LINK, "repeat_type": "DIFFERENT_UNIT"},
        {**LINK, "link_status": "LINKED"},
        {**LINK, "link_basis": "GUESS"},
        {**LINK, "version": 0},
        {**LINK, "decided_by": str(uuid4())},
        {"link_basis": "SERIAL_PROVIDED"},
    ],
)
def test_invalid_link_writes_are_rejected(client, body):
    method, path = PATHS[2]
    assert client.request(method, path, json=body, headers=CS_AGENT).status_code == 422


@pytest.mark.parametrize(("method", "path"), PATHS)
def test_unit_routes_are_closed_in_production(method, path):
    app = create_app(Settings(_env_file=None, environment="production"))
    with TestClient(app) as production:
        assert production.request(method, path, json=LINK, headers=CS_AGENT).status_code == 404
