"""KAN-102 unit check and same-unit history on a real PostgreSQL with the C/S migrations.

Scenarios follow the customer history set (프로젝트1_고객이력_가상시나리오.xlsx): S13 is a
different unit of the same customer and product, S04 a warranty replacement.
"""

import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

MIGRATIONS = (
    "20261022_KAN-102_tickets.sql",
    "20261023_KAN-102_ticket_audit.sql",
    "20261027_KAN-102_installed_units.sql",
)
CS_AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
PRODUCT = "METER-LP3500-3P4W"


@pytest.fixture(scope="module")
def cs_database():
    """Applies the C/S migrations to the dedicated test DB and returns its admin DSN."""
    dsn = os.getenv("AUTH_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("전용 PostgreSQL 검사는 AUTH_TEST_DATABASE_URL이 필요합니다.")
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.info.dbname == "kan77_auth_test", "전용 테스트 DB만 사용할 수 있습니다."
        for role in ("cs_migrator", "cs_app"):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute(SQL("CREATE ROLE {} NOLOGIN").format(Identifier(role)))
        conn.execute("ALTER ROLE cs_app LOGIN PASSWORD 'test-only'")
        conn.execute("CREATE SCHEMA IF NOT EXISTS cs AUTHORIZATION cs_migrator")
        conn.execute("GRANT USAGE ON SCHEMA cs TO cs_app")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE cs_migrator IN SCHEMA cs "
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO cs_app"
        )
        for filename in MIGRATIONS:
            migration = Path("infra/migrations/cs", filename).read_text()
            conn.execute(
                "\n".join(line for line in migration.splitlines() if not line.startswith("\\"))
            )
    return dsn


@pytest.fixture(scope="module")
def client(cs_database):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    with TestClient(create_app(settings), headers=principal_header(CS_AGENT)) as test_client:
        yield test_client


@pytest.fixture
def admin(cs_database):
    with psycopg.connect(cs_database, autocommit=True) as conn:
        yield conn


def add_ticket(admin, customer: str, received: datetime, product: str = PRODUCT):
    ticket_id = uuid4()
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan102', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'PHONE', 'en', %s, %s, 'Meter does not answer Modbus.')",
        (ticket_id, f"T-{ticket_id}", received, received, customer, product),
    )
    return ticket_id


def add_unit(admin, customer: str, serial: str, product: str = PRODUCT, **fields):
    unit_id = uuid4()
    values = {
        "id": unit_id,
        "source": "test",
        "source_instance": "kan102",
        "data_origin": "demo",
        "known_at": datetime(2026, 1, 1, tzinfo=UTC),
        "serial_no": f"{serial}-{unit_id.hex[:8]}",
        "customer_external_id": customer,
        "product_code": product,
        **fields,
    }
    columns = ", ".join(values)
    admin.execute(
        f"INSERT INTO cs.installed_units ({columns}) VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )
    return unit_id


def add_case(admin, ticket_id, known: datetime):
    admin.execute(
        "INSERT INTO cs.resolution_cases (id, ticket_id, source, source_instance, data_origin, "
        "derivation_kind, resolved_at, known_at, product_code, fault_id, symptom, root_cause, "
        "root_cause_type, action_taken, onsite_visit, resolved_by_dept) VALUES (%s, %s, 'test', "
        "'kan102', 'synthetic', 'PROVIDED', %s, %s, %s, 'FLT-001', 'No trip', 'Worn part', "
        "'WEAR_PART', 'Replace breaker', false, 'QA_CLAIM')",
        (uuid4(), ticket_id, known, known, PRODUCT),
    )


def link(client, ticket_id, unit_id, status="CONFIRMED", version=None, **fields):
    body = {"link_status": status, "link_basis": "SERIAL_PROVIDED", "version": version, **fields}
    return client.put(f"/api/v1/cs/tickets/{ticket_id}/units/{unit_id}", json=body)


def history(client, ticket_id) -> dict:
    response = client.get(f"/api/v1/cs/tickets/{ticket_id}/unit-history")
    assert response.status_code == 200
    return response.json()["data"]


def test_other_unit_history_is_not_copied(client, admin):
    """S13: the earlier meter (installed 2024-05) is another unit; its history stays out."""
    customer = f"CUST-{uuid4().hex[:8]}"
    old_unit = add_unit(admin, customer, "VSN-MTR-1301", commissioning_date=date(2024, 5, 1))
    new_unit = add_unit(admin, customer, "VSN-MTR-1302")
    earlier = add_ticket(admin, customer, datetime(2026, 1, 8, tzinfo=UTC))
    current = add_ticket(admin, customer, datetime(2026, 9, 11, tzinfo=UTC))
    assert link(client, earlier, old_unit).status_code == 200

    # Same customer and product, but no confirmed unit: nothing is carried over.
    assert history(client, current) == {"status": "NO_CONFIRMED_UNIT", "unit": None, "items": []}
    listed = client.get(f"/api/v1/cs/tickets/{current}/units").json()["data"]["items"]
    assert {item["unit"]["id"] for item in listed} == {str(old_unit), str(new_unit)}
    assert all(item["matches_ticket"] and item["link"] is None for item in listed)

    assert (
        link(client, current, old_unit, "REJECTED", repeat_type="DIFFERENT_UNIT").status_code == 200
    )
    assert link(client, current, new_unit).status_code == 200
    data = history(client, current)
    assert data["status"] == "CONFIRMED"
    assert data["unit"]["id"] == str(new_unit)
    assert data["unit"]["commissioning_date"] is None
    assert data["items"] == []


def test_replacement_history_is_marked_and_future_records_stay_out(client, admin):
    """S04: the replacement shows the replaced unit's tickets, and only what was known then."""
    customer = f"CUST-{uuid4().hex[:8]}"
    replacement = add_unit(admin, customer, "VSN-ELCB-0402")
    original = add_unit(
        admin, customer, "VSN-ELCB-0401", unit_status="REPLACED", replaced_by_unit_id=replacement
    )
    first = add_ticket(admin, customer, datetime(2025, 12, 18, tzinfo=UTC))
    add_case(admin, first, datetime(2025, 12, 20, tzinfo=UTC))
    second = add_ticket(admin, customer, datetime(2026, 3, 1, tzinfo=UTC))
    # Resolved only after the current ticket arrived: not usable for it.
    add_case(admin, second, datetime(2026, 9, 1, tzinfo=UTC))
    current = add_ticket(admin, customer, datetime(2026, 8, 25, tzinfo=UTC))
    later = add_ticket(admin, customer, datetime(2026, 10, 1, tzinfo=UTC))
    for ticket, unit in ((first, original), (second, replacement), (current, replacement)):
        assert link(client, ticket, unit).status_code == 200
    assert link(client, later, replacement).status_code == 200

    data = history(client, current)
    assert [(i["id"], i["relation"]) for i in data["items"]] == [
        (str(second), "SAME_UNIT"),
        (str(first), "REPLACED_UNIT"),
    ]
    assert data["items"][0]["resolution_case"] is None
    assert data["items"][1]["resolution_case"]["fault_id"] == "FLT-001"
    # The replaced unit's values are not presented as the current unit's.
    assert data["unit"]["id"] == str(replacement)


def test_candidates_carry_no_history(client, admin):
    customer = f"CUST-{uuid4().hex[:8]}"
    unit = add_unit(admin, customer, "VSN-HMI-0301")
    earlier = add_ticket(admin, customer, datetime(2026, 1, 19, tzinfo=UTC))
    current = add_ticket(admin, customer, datetime(2026, 6, 11, tzinfo=UTC))
    assert link(client, earlier, unit, "CANDIDATE").status_code == 200
    assert link(client, current, unit).status_code == 200
    assert history(client, current)["items"] == []


def test_unit_must_match_customer_and_product(client, admin):
    customer = f"CUST-{uuid4().hex[:8]}"
    ticket = add_ticket(admin, customer, datetime(2026, 5, 1, tzinfo=UTC))
    other_customer = add_unit(admin, f"CUST-{uuid4().hex[:8]}", "VSN-MTR-9001")
    other_product = add_unit(admin, customer, "VSN-RLY-9002", product="RELAY-GMR-22")
    for unit in (other_customer, other_product):
        for status in ("CANDIDATE", "CONFIRMED"):
            refused = link(client, ticket, unit, status)
            assert refused.status_code == 422
            assert refused.json()["error"]["code"] == "CS_UNIT_MISMATCH"
    assert link(client, ticket, other_customer, "REJECTED").status_code == 200
    assert link(client, ticket, uuid4()).json()["error"]["code"] == "CS_UNIT_NOT_FOUND"
    # Units of other customers are not listed unless linked to this ticket.
    listed = client.get(f"/api/v1/cs/tickets/{ticket}/units").json()["data"]["items"]
    assert [(i["unit"]["id"], i["matches_ticket"]) for i in listed] == [
        (str(other_customer), False)
    ]


def test_one_confirmed_unit_and_versioned_changes(client, admin):
    customer = f"CUST-{uuid4().hex[:8]}"
    first = add_unit(admin, customer, "VSN-S100-0901")
    second = add_unit(admin, customer, "VSN-S100-0902")
    ticket = add_ticket(admin, customer, datetime(2025, 12, 17, tzinfo=UTC))
    created = link(client, ticket, first, "CANDIDATE", link_basis="AGENT_REVIEW").json()["data"]
    assert (created["link"]["version"], created["link"]["decided_by"]) == (1, None)

    assert link(client, ticket, first, "CANDIDATE").json()["error"]["code"] == (
        "CS_UNIT_LINK_VERSION_CONFLICT"
    )
    confirmed = link(client, ticket, first, "CONFIRMED", version=1).json()["data"]["link"]
    assert confirmed["version"] == 2
    assert confirmed["decided_by"] == str(CS_AGENT.user_id)
    # Sending the same decision again changes nothing.
    again = link(client, ticket, first, "CONFIRMED", version=2).json()["data"]["link"]
    assert (again["version"], again["decided_at"]) == (2, confirmed["decided_at"])

    blocked = link(client, ticket, second)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "CS_TICKET_UNIT_ALREADY_CONFIRMED"
    assert link(client, ticket, first, "REJECTED", version=2).status_code == 200
    assert link(client, ticket, second).status_code == 200

    rows = admin.execute(
        "SELECT a.action, a.version, a.actor_user_id FROM cs.audit_events a "
        "JOIN cs.ticket_unit_links l ON l.id = a.entity_id "
        "WHERE a.entity_type = 'ticket_unit_link' AND l.ticket_id = %s AND l.unit_id = %s "
        "ORDER BY a.version",
        (ticket, first),
    ).fetchall()
    assert rows == [
        ("create", 1, CS_AGENT.user_id),
        ("update", 2, CS_AGENT.user_id),
        ("update", 3, CS_AGENT.user_id),
    ]


def test_confirmed_unit_blocks_customer_or_product_correction(client, admin):
    customer = f"CUST-{uuid4().hex[:8]}"
    unit = add_unit(admin, customer, "VSN-UPS-0201")
    ticket = add_ticket(admin, customer, datetime(2026, 9, 21, tzinfo=UTC))
    assert link(client, ticket, unit).status_code == 200
    path = f"/api/v1/cs/tickets/{ticket}"
    for change in ({"customer_external_id": "CUST-OTHER"}, {"product_code": "UPS-OTHER"}):
        refused = client.patch(path, json={"version": 1, **change})
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "CS_TICKET_UNIT_LINKED"
    assert client.patch(path, json={"version": 1, "subject": "Bypass"}).status_code == 200
    assert link(client, ticket, unit, "REJECTED", version=1).status_code == 200
    corrected = client.patch(path, json={"version": 2, "product_code": "UPS-OTHER"})
    assert corrected.status_code == 200


def test_service_cannot_write_units_or_remove_links(cs_database, admin):
    customer = f"CUST-{uuid4().hex[:8]}"
    unit = add_unit(admin, customer, "VSN-PV-0101")
    runtime = make_conninfo(cs_database, user="cs_app", password="test-only")
    for statement in (
        "UPDATE cs.installed_units SET serial_no = 'X' WHERE id = %s",
        "DELETE FROM cs.ticket_unit_links WHERE unit_id = %s",
    ):
        with psycopg.connect(runtime) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement, (unit,))
    with psycopg.connect(cs_database) as conn, pytest.raises(psycopg.errors.UniqueViolation):
        ticket = add_ticket(conn, customer, datetime(2026, 1, 1, tzinfo=UTC))
        for other in (unit, add_unit(conn, customer, "VSN-PV-0102")):
            conn.execute(
                "INSERT INTO cs.ticket_unit_links (id, ticket_id, unit_id, data_origin, "
                "link_status, link_basis, decided_by, decided_at) "
                "VALUES (%s, %s, %s, 'demo', 'CONFIRMED', 'SERIAL_PROVIDED', %s, now())",
                (uuid4(), ticket, other, uuid4()),
            )
