"""KAN-214 rule-based assignment on a real PostgreSQL: intake, manager preview/apply, grants.

Rule conformance only (contracts/cs/assignment.md). Every test removes its skill rows and
tickets, because other test files create tickets and expect them to stay unassigned.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.assignments import repository as assignment_repository
from backend.api.cs.main import create_app
from backend.engine.cs.assignment import ASSIGNMENT_RULES_VERSION
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
CS_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})
TRF_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"TS_TRF"})
# Smaller UUIDs win ties, so the order of these three is part of the expectations.
THAI = UUID("00000000-0000-4000-8000-0000000214a1")
THAI_TOO = UUID("00000000-0000-4000-8000-0000000214a2")
ENGLISH = UUID("00000000-0000-4000-8000-0000000214a3")
# Older than anything other test files insert, so a preview lists these first.
BASE = datetime(1990, 1, 1, tzinfo=UTC)
TABLES = ("ticket_assignments", "audit_events", "ticket_workflows")


@pytest.fixture(scope="module")
def client(cs_database):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as test_client:
        yield test_client


@pytest.fixture
def skills(admin):
    # TRF-OIL-1000 is TRANSFORMER, MCCB-ABS-103c is LV_BREAKER (product_master.csv).
    rows = [
        (THAI, ["th", "en"], ["LV_BREAKER"]),
        (THAI_TOO, ["th"], ["LV_BREAKER", "TRANSFORMER"]),
        (ENGLISH, ["en"], ["TRANSFORMER"]),
    ]
    for user_id, languages, families in rows:
        admin.execute(
            "INSERT INTO cs.agent_skills (user_id, languages, families, data_origin) "
            "VALUES (%s, %s, %s, 'synthetic')",
            (user_id, languages, families),
        )
    created: list[UUID] = []
    yield created
    for table in TABLES:
        column = "entity_id" if table == "audit_events" else "ticket_id"
        admin.execute(f"DELETE FROM cs.{table} WHERE {column} = ANY(%s)", (created,))
    admin.execute("DELETE FROM cs.tickets WHERE id = ANY(%s)", (created,))
    admin.execute("DELETE FROM cs.agent_skills WHERE user_id = ANY(%s)", ([r[0] for r in rows],))


def stored_ticket(admin, created, language, product, minutes):
    ticket_id = uuid4()
    received = BASE + timedelta(minutes=minutes)
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan214', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', %s, 'CUST-0374', %s, 'Breaker trips.')",
        (ticket_id, f"T-{ticket_id}", received, received, language, product),
    )
    created.append(ticket_id)
    return ticket_id


def owner(admin, ticket_id):
    row = admin.execute(
        "SELECT owner_user_id, version FROM cs.ticket_workflows WHERE ticket_id = %s", (ticket_id,)
    ).fetchone()
    return row


def preview(client, user=CS_MANAGER, limit=200):
    return client.post(
        "/api/v1/cs/assignments/preview", json={"limit": limit}, headers=principal_header(user)
    )


def apply(client, items, user=CS_MANAGER, rule_version=ASSIGNMENT_RULES_VERSION):
    return client.post(
        "/api/v1/cs/assignments/apply",
        json={"rule_version": rule_version, "items": items},
        headers=principal_header(user),
    )


def mine(response, created):
    assert response.status_code == 200, response.text
    return [i for i in response.json()["data"]["items"] if UUID(i["ticket_id"]) in created]


def test_manual_intake_assigns_by_language_and_family(client, admin, skills):
    response = client.post(
        "/api/v1/cs/tickets",
        json={
            "channel": "EMAIL",
            "body_language": "th-TH",
            "customer_external_id": "CUST-0374",
            "product_code": "TRF-OIL-1000",
            "body": "Transformer oil leak.",
        },
    )
    assert response.status_code == 201, response.text
    ticket_id = UUID(response.json()["data"]["id"])
    skills.append(ticket_id)
    # Only THAI_TOO reads Thai and handles transformers.
    assert owner(admin, ticket_id) == (THAI_TOO, 1)
    workflow = client.get(f"/api/v1/cs/tickets/{ticket_id}/workflow").json()["data"]
    assert (workflow["state"], workflow["owner_user_id"]) == ("RECEIVED", str(THAI_TOO))
    record = workflow["assignment"]
    assert {k: record[k] for k in ("reason_code", "matched_language", "matched_family")} == {
        "reason_code": "LANGUAGE_AND_FAMILY",
        "matched_language": "th",
        "matched_family": "TRANSFORMER",
    }
    assert (record["trigger"], record["decided_by"], record["candidate_count"]) == (
        "INTAKE",
        None,
        1,
    )
    assert record["rule_version"] == ASSIGNMENT_RULES_VERSION
    event = admin.execute(
        "SELECT actor_user_id, changes->'action'->>'to', changes->'owner_user_id'->>'to', "
        "changes->'reason_code'->>'to' FROM cs.audit_events "
        "WHERE entity_type = 'ticket_workflow' AND entity_id = %s",
        (ticket_id,),
    ).fetchall()
    assert event == [(None, "auto_assign", str(THAI_TOO), "LANGUAGE_AND_FAMILY")]
    # A manager reassignment replaces the owner; the old rule reason is no longer shown.
    moved = client.put(
        f"/api/v1/cs/tickets/{ticket_id}/workflow/owner",
        json={"version": 1, "owner_user_id": str(ENGLISH)},
        headers=principal_header(CS_MANAGER),
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["data"]["assignment"] is None


def test_mail_intake_without_a_match_stays_unassigned_and_records_why(client, admin, skills):
    response = client.post(
        "/internal/v1/cs/tickets/from-mail",
        json={
            "message_id": str(uuid4()),
            "mailbox_id": str(uuid4()),
            "external_id": f"AAMk-{uuid4()}",
            "sender_address": "plant@customer.invalid",
            "received_at": BASE.isoformat(),
            "body": "Stoki się nie uruchamia.",
            "body_language": "pl",
            "customer_external_id": "CUST-0374",
            "product_code": "PLC-XGI-CPUU",
        },
    )
    assert response.status_code == 201, response.text
    ticket_id = UUID(response.json()["data"]["id"])
    skills.append(ticket_id)
    admin.execute("DELETE FROM cs.ticket_mail_origins WHERE ticket_id = %s", (ticket_id,))
    # Nobody reads Polish, nobody handles PLC: nothing is written, version stays 0, and the
    # manager preview shows the reason.
    assert owner(admin, ticket_id) is None
    workflow = client.get(f"/api/v1/cs/tickets/{ticket_id}/workflow").json()["data"]
    assert (workflow["version"], workflow["assignment"]) == (0, None)
    assert admin.execute(
        "SELECT count(*) FROM cs.ticket_assignments WHERE ticket_id = %s", (ticket_id,)
    ).fetchone() == (0,)
    [proposal] = mine(preview(client), [ticket_id])
    assert (proposal["user_id"], proposal["reason_code"]) == (None, "NO_MATCH")


def test_assignment_failure_never_fails_intake(client, admin, skills, monkeypatch):
    async def broken(conn):
        raise RuntimeError("skills unavailable")

    monkeypatch.setattr(assignment_repository, "list_agents", broken)
    response = client.post(
        "/api/v1/cs/tickets",
        json={
            "channel": "PHONE",
            "body_language": "th",
            "customer_external_id": "CUST-0374",
            "product_code": "MCCB-ABS-103c",
            "body": "Breaker trips.",
        },
    )
    assert response.status_code == 201, response.text
    ticket_id = UUID(response.json()["data"]["id"])
    skills.append(ticket_id)
    assert owner(admin, ticket_id) is None
    assert admin.execute(
        "SELECT count(*) FROM cs.ticket_assignments WHERE ticket_id = %s", (ticket_id,)
    ).fetchone() == (0,)


def test_preview_and_apply_need_a_cs_manager(client, skills):
    for user in (AGENT, TRF_MANAGER):
        assert preview(client, user).status_code == 403
        assert apply(client, [{"ticket_id": str(uuid4()), "user_id": None}], user).status_code == (
            403
        )
    agents = client.get("/api/v1/cs/assignments/agents", headers=principal_header(TRF_MANAGER))
    assert agents.status_code == 403


def test_agent_table_lists_skills_and_current_load(client, admin, skills):
    loaded = stored_ticket(admin, skills, "th", "MCCB-ABS-103c", 0)
    admin.execute(
        "INSERT INTO cs.ticket_workflows (ticket_id, state, owner_user_id) "
        "VALUES (%s, 'IN_REVIEW', %s)",
        (loaded, THAI),
    )
    for user in (AGENT, CS_MANAGER):
        response = client.get("/api/v1/cs/assignments/agents", headers=principal_header(user))
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        rows = {UUID(i["user_id"]): i for i in data["items"]}
        assert data["rule_version"] == ASSIGNMENT_RULES_VERSION
        assert rows[THAI]["languages"] == ["th", "en"]
        assert (rows[THAI]["open_count"], rows[THAI_TOO]["open_count"]) == (1, 0)
        assert rows[THAI]["derivation_kind"] == "ASSUMED"


def test_preview_writes_nothing_and_apply_spreads_the_batch(client, admin, skills):
    breakers = [stored_ticket(admin, skills, "th", "MCCB-ABS-103c", n) for n in range(4)]
    english = stored_ticket(admin, skills, "en", "TRF-OIL-1000", 10)
    polish = stored_ticket(admin, skills, "pl", "PLC-XGI-CPUU", 11)
    items = mine(preview(client), skills)
    assert [UUID(i["ticket_id"]) for i in items] == [*breakers, english, polish]
    # Breakers: THAI and THAI_TOO both qualify and alternate (tie → smaller user_id).
    assert [(i["user_id"], i["reason_code"]) for i in items] == [
        (str(THAI), "LANGUAGE_AND_FAMILY"),
        (str(THAI_TOO), "LANGUAGE_AND_FAMILY"),
        (str(THAI), "LANGUAGE_AND_FAMILY"),
        (str(THAI_TOO), "LANGUAGE_AND_FAMILY"),
        (str(ENGLISH), "LANGUAGE_AND_FAMILY"),
        (None, "NO_MATCH"),
    ]
    assert [i["chosen_open_count"] for i in items[:4]] == [0, 0, 1, 1]
    assert all(owner(admin, t) is None for t in skills)

    body = [{"ticket_id": i["ticket_id"], "user_id": i["user_id"]} for i in items]
    result = apply(client, body)
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert (data["assigned"], data["skipped"]) == (5, 1)
    assert [i["outcome"] for i in data["items"]] == ["ASSIGNED"] * 5 + ["NO_MATCH"]
    assert [owner(admin, t) for t in breakers] == [
        (THAI, 1),
        (THAI_TOO, 1),
        (THAI, 1),
        (THAI_TOO, 1),
    ]
    assert owner(admin, polish) is None
    stored = admin.execute(
        "SELECT trigger, decided_by, reason_code FROM cs.ticket_assignments WHERE ticket_id = %s",
        (english,),
    ).fetchall()
    assert stored == [("BATCH", CS_MANAGER.user_id, "LANGUAGE_AND_FAMILY")]
    actor = admin.execute(
        "SELECT actor_user_id, changes->'trigger'->>'to' FROM cs.audit_events "
        "WHERE entity_type = 'ticket_workflow' AND entity_id = %s",
        (english,),
    ).fetchall()
    assert actor == [(CS_MANAGER.user_id, "BATCH")]

    # Idempotent: the same apply again writes nothing.
    again = apply(client, body).json()["data"]
    assert (again["assigned"], [i["outcome"] for i in again["items"]][:5]) == (
        0,
        ["ALREADY_ASSIGNED"] * 5,
    )
    assert admin.execute(
        "SELECT count(*) FROM cs.ticket_assignments WHERE ticket_id = ANY(%s)", (skills,)
    ).fetchone() == (5,)
    assert [UUID(i["ticket_id"]) for i in mine(preview(client), skills)] == [polish]


def test_apply_rechecks_each_ticket_before_writing(client, admin, skills):
    first = stored_ticket(admin, skills, "th", "MCCB-ABS-103c", 0)
    stored_ticket(admin, skills, "en", "TRF-OIL-1000", 1)
    third = stored_ticket(admin, skills, "en", "TRF-OIL-1000", 2)
    items = mine(preview(client), skills)
    # Someone assigned the first ticket by hand after the preview.
    moved = client.put(
        f"/api/v1/cs/tickets/{first}/workflow/owner",
        json={"version": 0, "owner_user_id": str(ENGLISH)},
        headers=principal_header(CS_MANAGER),
    )
    assert moved.status_code == 200, moved.text
    body = [{"ticket_id": i["ticket_id"], "user_id": i["user_id"]} for i in items]
    body[2]["user_id"] = str(THAI)  # not what the rules give for this ticket
    data = apply(client, body).json()["data"]
    assert [(i["outcome"], i["user_id"]) for i in data["items"]] == [
        ("ALREADY_ASSIGNED", str(ENGLISH)),
        ("ASSIGNED", str(ENGLISH)),
        ("PREVIEW_CHANGED", str(ENGLISH)),
    ]
    assert owner(admin, first) == (ENGLISH, 1)
    assert owner(admin, third) is None
    missing = apply(client, [{"ticket_id": str(uuid4()), "user_id": None}]).json()["data"]
    assert missing["items"][0]["outcome"] == "NOT_FOUND"
    stale = apply(client, body, rule_version="cs-assign-v0")
    assert (stale.status_code, stale.json()["error"]["code"]) == (
        409,
        "CS_ASSIGNMENT_RULES_CHANGED",
    )


def test_service_role_reads_skills_but_cannot_write_them(cs_database):
    with psycopg.connect(
        make_conninfo(cs_database, user="cs_app", password="test-only"), autocommit=True
    ) as conn:
        conn.execute("SELECT count(*) FROM cs.agent_skills").fetchone()
        for statement in (
            "INSERT INTO cs.agent_skills (user_id, languages, families, data_origin) "
            "VALUES (gen_random_uuid(), '{en}', '{UPS}', 'demo')",
            "UPDATE cs.agent_skills SET active = false",
            "DELETE FROM cs.ticket_assignments",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_skill_rows_need_languages_and_families(admin):
    for languages, families in (([], ["UPS"]), (["en"], []), (["EN"], ["UPS"]), (["en"], ["ups"])):
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(
                "INSERT INTO cs.agent_skills (user_id, languages, families, data_origin) "
                "VALUES (%s, %s, %s, 'demo')",
                (uuid4(), languages, families),
            )
