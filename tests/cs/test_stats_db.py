"""KAN-111 aggregates on a real PostgreSQL: period, department, customer and owner filters.

Each test uses its own customer and owner IDs, so rows from other tests in the shared
database do not change the counts it checks.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"TS_LV"})
CS_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})
PATH = "/api/v1/cs/stats"
SEPT = {"start": "2026-09-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"}


@pytest.fixture(scope="module")
def client(cs_database):
    dsn = make_conninfo(cs_database, user="cs_app", password="test-only")
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(dsn))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T00:00:00+00:00")


def ticket(admin, customer, received, product="TRF-OIL-1000", origin="synthetic"):
    ticket_id = uuid4()
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan111', %s, %s, 'PROVIDED', %s, %s, 'EMAIL', "
        "'en', %s, %s, 'Body')",
        (ticket_id, f"T-{ticket_id}", origin, at(received), at(received), customer, product),
    )
    return ticket_id


def workflow(admin, ticket_id, state, owner=None, closed=None):
    admin.execute(
        "INSERT INTO cs.ticket_workflows (ticket_id, state, owner_user_id, closed_at, closed_by) "
        "VALUES (%s, %s, %s, %s, %s)",
        (ticket_id, state, owner, at(closed) if closed else None, owner if closed else None),
    )


@pytest.fixture
def customer(admin):
    """Four tickets of one customer: closed, in review, transferred, and an earlier one."""
    name, owner = f"CUST-{uuid4().hex[:8]}", uuid4()
    closed = ticket(admin, name, "2026-09-01")
    workflow(admin, closed, "CLOSED", owner, closed="2026-09-05")
    review = ticket(admin, name, "2026-09-10")
    workflow(admin, review, "IN_REVIEW", owner)
    moved = ticket(admin, name, "2026-09-20", product="TRF-DRY-630")
    workflow(admin, moved, "TRANSFERRED", owner)
    admin.execute(
        "INSERT INTO cs.ticket_transfers (id, ticket_id, round, dept_code, status, reason, "
        "transferred_by, transferred_at) VALUES (%s, %s, 1, 'TS_TRF', 'OPEN', 'x', %s, %s)",
        (uuid4(), moved, owner, at("2026-09-21")),
    )
    ticket(admin, name, "2026-08-01", origin="demo")
    return {"name": name, "owner": owner, "closed": closed, "review": review, "moved": moved}


def get(client, path, user, **query):
    response = client.get(f"{PATH}/{path}", params=query, headers=principal_header(user))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_overview_counts_the_period_for_one_customer(client, customer):
    data = get(client, "overview", MANAGER, customer=customer["name"], **SEPT)
    tickets = data["tickets"]
    assert (data["scope"], tickets["received"], tickets["closed"], tickets["open"]) == (
        "ALL",
        3,
        1,
        2,
    )
    assert {k: v for k, v in tickets["by_state"].items() if v} == {
        "CLOSED": 1,
        "IN_REVIEW": 1,
        "TRANSFERRED": 1,
    }
    assert tickets["by_origin"] == {"synthetic": 3, "demo": 0, "real": 0}
    # Aged at the period end (10/01): 21 and 11 days.
    assert tickets["open_age"] == {
        "under_1_day": 0,
        "from_1_to_3_days": 0,
        "from_3_to_7_days": 0,
        "over_7_days": 2,
    }
    # The data range is not limited by the period: nothing before the first record is invented.
    assert data["data_range"]["first_received_at"].startswith("2026-08-01")
    assert data["departments"] == [
        {
            "dept_code": "TS_TRF",
            "transferred": 1,
            "results_submitted": 0,
            "results_accepted": 0,
            "returned_or_recalled": 0,
        }
    ]
    assert data["top_customers"] == [
        {"customer_external_id": customer["name"], "received": 3, "open": 2}
    ]
    assert data["unconfirmed"] == ["SLA_DUE", "RULE_ASSIGNED_DEPT"]


def test_department_filter_keeps_transferred_tickets(client, customer):
    data = get(client, "overview", MANAGER, customer=customer["name"], dept="TS_TRF", **SEPT)
    assert (data["tickets"]["received"], data["dept_code"]) == (1, "TS_TRF")
    none = get(client, "overview", MANAGER, customer=customer["name"], dept="TS_LV", **SEPT)
    assert none["tickets"]["received"] == 0 and none["departments"] == []


def test_agents_see_only_their_own_tickets(client, customer):
    owner = Principal(user_id=customer["owner"], roles={"cs_agent"})
    data = get(client, "overview", owner, **SEPT)
    assert (data["scope"], data["tickets"]["received"], data["improvements"]) == ("OWN", 3, None)
    stranger = Principal(user_id=uuid4(), roles={"cs_agent"})
    assert get(client, "overview", stranger, **SEPT)["tickets"]["received"] == 0


def test_owner_load_for_managers(client, admin, customer):
    # An August ticket still open: outside the period, but part of the owner's current load.
    august = ticket(admin, customer["name"], "2026-08-15")
    workflow(admin, august, "IN_REVIEW", customer["owner"])
    items = get(client, "owners", MANAGER, **SEPT)["items"]
    [row] = [i for i in items if i["owner_user_id"] == str(customer["owner"])]
    assert (row["received"], row["closed"], row["open"]) == (3, 1, 3)
    # The overview counts only tickets received in the period.
    overview = get(client, "overview", MANAGER, customer=customer["name"], **SEPT)
    assert overview["tickets"]["open"] == 2
    # An owner with only older open tickets is still listed.
    idle = uuid4()
    old = ticket(admin, customer["name"], "2026-07-01")
    workflow(admin, old, "WAITING_CUSTOMER", idle)
    [row] = [
        i
        for i in get(client, "owners", MANAGER, **SEPT)["items"]
        if i["owner_user_id"] == str(idle)
    ]
    assert (row["received"], row["closed"], row["open"]) == (0, 0, 1)
    agent = Principal(user_id=customer["owner"], roles={"cs_agent"})
    response = client.get(f"{PATH}/owners", headers=principal_header(agent))
    assert response.status_code == 403


def test_customer_history_lists_tickets_units_and_requests(client, admin, customer):
    unit = uuid4()
    admin.execute(
        "INSERT INTO cs.installed_units (id, source, source_instance, data_origin, known_at, "
        "serial_no, customer_external_id, product_code) VALUES (%s, 'test', 'kan111', 'demo', "
        "now(), %s, %s, 'TRF-OIL-1000')",
        (unit, f"VSN-TRF-{unit.hex[:6]}", customer["name"]),
    )
    admin.execute(
        "INSERT INTO cs.ticket_unit_links (id, ticket_id, unit_id, data_origin, link_status, "
        "link_basis, decided_by, decided_at) VALUES (%s, %s, %s, 'demo', 'CONFIRMED', "
        "'SERIAL_PROVIDED', %s, now())",
        (uuid4(), customer["review"], unit, customer["owner"]),
    )
    request = uuid4()
    admin.execute(
        "INSERT INTO cs.improvement_requests (id, request_type, origin, data_origin, product_code, "
        "fault_id, problem_statement, proposed_depts) VALUES (%s, 'FAULT', 'MANUAL', 'demo', "
        "'TRF-OIL-1000', 'FLT-067', 'Leaks', '{QA_CLAIM}')",
        (request,),
    )
    admin.execute(
        "INSERT INTO cs.request_evidence (id, request_id, ticket_id) VALUES (%s, %s, %s)",
        (uuid4(), request, customer["closed"]),
    )
    data = get(client, f"customers/{customer['name']}", CS_MANAGER)
    assert data["received"] == 4
    assert data["by_product"] == {"TRF-OIL-1000": 3, "TRF-DRY-630": 1}
    assert [t["id"] for t in data["tickets"]][:1] == [str(customer["moved"])]
    assert [(u["id"], u["confirmed_tickets"]) for u in data["units"]] == [(str(unit), 1)]
    assert [(r["id"], r["evidence_tickets"]) for r in data["improvement_requests"]] == [
        (str(request), 1)
    ]
    september = get(client, f"customers/{customer['name']}", CS_MANAGER, **SEPT)
    assert september["received"] == 3
    # The improvement totals see the request through the customer's evidence.
    overview = get(client, "overview", MANAGER, customer=customer["name"])
    assert overview["improvements"]["by_status"] == {"DRAFT": 1}
    assert overview["improvements"]["open"] == 1


@pytest.mark.parametrize(
    "user",
    [
        Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_TRF"),
        Principal(user_id=uuid4(), roles={"sales"}),
    ],
)
def test_other_roles_see_no_aggregates(client, user):
    for path in ("overview", "owners", "customers/CUST-0001"):
        response = client.get(f"{PATH}/{path}", headers=principal_header(user))
        assert response.status_code == 403, path
    # A manager of another department sees aggregates but not a customer's records.
    response = client.get(f"{PATH}/customers/CUST-0001", headers=principal_header(MANAGER))
    assert response.status_code == 403


@pytest.mark.parametrize(
    "query",
    [
        {"start": "2026-10-01T00:00:00Z", "end": "2026-09-01T00:00:00Z"},
        {"start": "2026-09-01T00:00:00"},
        {"dept": "GCS_L1"},
        {"customer": ".CUST"},
        {"customer": "CUST 01"},
        {"customer": "C" * 65},
    ],
)
def test_invalid_filters_are_rejected(client, query):
    response = client.get(f"{PATH}/overview", params=query, headers=principal_header(MANAGER))
    assert response.status_code == 422


# KAN-178 customer ID convention: the same pattern the platform relay matches on paths.
@pytest.mark.parametrize("customer", [".CUST", "-CUST", "CUST%20A", "%EA%B3%A0%EA%B0%9D", "C" * 65])
def test_customer_history_rejects_ids_outside_the_convention(client, customer):
    response = client.get(f"{PATH}/customers/{customer}", headers=principal_header(CS_MANAGER))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CS_INVALID_CUSTOMER"
