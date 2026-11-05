"""KAN-214 scope addition: improvement requests get a C/S owner by product family (real DB).

System candidates are assigned when saved, manual requests keep their writer, the reviewer
becomes owner only when there is none, and managers can preview/apply unassigned requests.
Agents are fresh per test and their skill rows are removed afterwards, so other test files keep
seeing an empty skill table (their candidates stay unassigned, as before).
"""

from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.engine.cs.assignment import IMPROVEMENT_RULES_VERSION
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
CS_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})
QA_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"QA_CLAIM"})
# LV_BREAKER and TRANSFORMER in product_master.csv.
BREAKER, TRANSFORMER = "MCCB-ABS-103c", "TRF-OIL-1000"
# Known long before anything other test files insert, so a collect at AS_OF sees only ours.
BASE = datetime(1991, 1, 1, tzinfo=UTC)
REQUESTS = "/api/v1/cs/improvement-requests"


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
def agents(admin):
    """Two breaker agents (any language) and one transformer agent, smallest UUID first."""
    ids = sorted((uuid4() for _ in range(3)), key=str)
    for user_id, families in zip(ids, (["LV_BREAKER"], ["LV_BREAKER"], ["TRANSFORMER"])):
        admin.execute(
            "INSERT INTO cs.agent_skills (user_id, languages, families, data_origin) "
            "VALUES (%s, '{th}', %s, 'synthetic')",
            (user_id, families),
        )
    yield ids
    admin.execute("DELETE FROM cs.agent_skills WHERE user_id = ANY(%s)", (ids,))


def eligible_group(admin, product, fault):
    """Three customers' resolution records of one fault: an ELIGIBLE candidate group."""
    for day in (0, 10, 20):
        ticket_id, case_id = uuid4(), uuid4()
        at = BASE + timedelta(days=day)
        admin.execute(
            "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
            "derivation_kind, received_at, known_at, channel, body_language, "
            "customer_external_id, product_code, body) VALUES (%s, 'test', 'kan214r', %s, "
            "'synthetic', 'PROVIDED', %s, %s, 'EMAIL', 'en', %s, %s, 'body')",
            (ticket_id, f"T-{ticket_id}", at, at, f"CUST-{day}", product),
        )
        admin.execute(
            "INSERT INTO cs.resolution_cases (id, ticket_id, source, source_instance, "
            "external_id, data_origin, derivation_kind, resolved_at, known_at, product_code, "
            "fault_id, symptom, root_cause, root_cause_type, action_taken, onsite_visit, "
            "resolved_by_dept) VALUES (%s, %s, 'test', 'kan214r', %s, 'synthetic', 'PROVIDED', "
            "%s, %s, %s, %s, 'symptom', 'cause', 'HARDWARE', 'action', false, 'QA_CLAIM')",
            (case_id, ticket_id, f"RC-{case_id}", at, at, product, fault),
        )


# Unique fault per group, so no test reuses another test's saved candidate.
FAULTS = count(500)


def fault():
    return f"FLT-{next(FAULTS)}"


def collect(client):
    response = client.post(
        "/api/v1/cs/improvement-candidates/collect",
        json={"as_of": (BASE + timedelta(days=30)).isoformat()},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def stored(admin, product, fault_id):
    return admin.execute(
        "SELECT id, cs_owner, version FROM cs.improvement_requests "
        "WHERE product_code = %s AND fault_id = %s AND origin = 'SYSTEM_CANDIDATE'",
        (product, fault_id),
    ).fetchone()


def decisions(admin, request_id):
    return admin.execute(
        "SELECT user_id, reason_code, matched_family, trigger, decided_by "
        "FROM cs.improvement_assignments WHERE request_id = %s ORDER BY created_at",
        (request_id,),
    ).fetchall()


def test_saved_candidates_are_assigned_by_family_and_spread(client, admin, agents):
    first, second, third = fault(), fault(), fault()
    for f in (first, second):
        eligible_group(admin, BREAKER, f)
    eligible_group(admin, TRANSFORMER, third)
    collect(client)
    owners = [stored(admin, BREAKER, f)[1] for f in (first, second)]
    # Two breaker candidates in one run go to the two breaker agents, one each.
    assert sorted(owners, key=str) == agents[:2]
    request_id, owner, version = stored(admin, TRANSFORMER, third)
    assert (owner, version) == (agents[2], 1)
    assert decisions(admin, request_id) == [(agents[2], "FAMILY", "TRANSFORMER", "CANDIDATE", None)]
    created = admin.execute(
        "SELECT changes FROM cs.audit_events WHERE entity_type = 'improvement_request' "
        "AND entity_id = %s",
        (request_id,),
    ).fetchone()[0]
    assert created["cs_owner"] == {"to": str(agents[2])}
    assert created["assignment_reason"] == {"to": "FAMILY"}
    snapshot = admin.execute(
        "SELECT content->>'cs_owner' FROM cs.improvement_request_versions WHERE request_id = %s",
        (request_id,),
    ).fetchone()
    assert snapshot == (str(agents[2]),)
    # The owner sees it with the list filter; others' filters do not.
    mine = client.get(REQUESTS, params={"owner": str(agents[2])}).json()["data"]["items"]
    assert [i["id"] for i in mine] == [str(request_id)]
    other = client.get(REQUESTS, params={"owner": str(agents[0])}).json()["data"]["items"]
    assert str(request_id) not in {i["id"] for i in other}

    # The assigned owner stays when another agent records the review.
    reviewed = client.post(f"{REQUESTS}/{request_id}/review", json={"version": 1})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["data"]["request"]["cs_owner"] == str(agents[2])


def test_no_match_is_recorded_and_the_reviewer_becomes_owner(client, admin, agents):
    f = fault()
    eligible_group(admin, "PLC-XGI-CPUU", f)  # PLC: no agent handles it
    collect(client)
    request_id, owner, _ = stored(admin, "PLC-XGI-CPUU", f)
    assert owner is None
    assert decisions(admin, request_id) == [(None, "NO_MATCH", None, "CANDIDATE", None)]
    # KAN-234: the manager list shows it with the family nobody handles.
    [item] = unmatched(client, request_id)
    assert (item["product_code"], item["family"], item["reason_code"], item["trigger"]) == (
        "PLC-XGI-CPUU",
        "PLC",
        "NO_MATCH",
        "CANDIDATE",
    )
    reviewed = client.post(f"{REQUESTS}/{request_id}/review", json={"version": 1})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["data"]["request"]["cs_owner"] == str(AGENT.user_id)
    assert unmatched(client, request_id) == []


def unmatched(client, request_id):
    response = client.get(
        "/api/v1/cs/assignments/improvements/unmatched",
        params={"limit": 200},
        headers=principal_header(CS_MANAGER),
    )
    assert response.status_code == 200, response.text
    return [i for i in response.json()["data"]["items"] if i["request_id"] == str(request_id)]


def test_manual_request_keeps_its_writer(client, admin, agents):
    f = fault()
    eligible_group(admin, TRANSFORMER, f)
    cases = admin.execute(
        "SELECT ticket_id, id FROM cs.resolution_cases WHERE product_code = %s AND fault_id = %s",
        (TRANSFORMER, f),
    ).fetchall()
    response = client.post(
        REQUESTS,
        json={
            "request_type": "FAULT",
            "product_code": TRANSFORMER,
            "fault_id": f,
            "problem_statement": "Written by hand.",
            "proposed_depts": ["QA_CLAIM"],
            "evidence": [{"ticket_id": str(t), "case_id": str(c)} for t, c in cases],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["request"]["cs_owner"] == str(AGENT.user_id)


def test_manager_previews_and_applies_unassigned_requests(client, admin, agents):
    f = fault()
    eligible_group(admin, BREAKER, f)
    # Saved while nobody handled breakers, so it has no owner.
    admin.execute("UPDATE cs.agent_skills SET active = false WHERE user_id = ANY(%s)", (agents,))
    collect(client)
    request_id, owner, version = stored(admin, BREAKER, f)
    assert owner is None
    admin.execute("UPDATE cs.agent_skills SET active = true WHERE user_id = ANY(%s)", (agents,))

    path = "/api/v1/cs/assignments/improvements"
    for user in (AGENT, QA_MANAGER):
        assert (
            client.post(f"{path}/preview", json={}, headers=principal_header(user)).status_code
            == 403
        )
    preview = client.post(
        f"{path}/preview", json={"limit": 200}, headers=principal_header(CS_MANAGER)
    )
    assert preview.status_code == 200, preview.text
    [item] = [i for i in preview.json()["data"]["items"] if i["request_id"] == str(request_id)]
    assert (item["user_id"], item["reason_code"], item["family"]) == (
        str(agents[0]),
        "FAMILY",
        "LV_BREAKER",
    )
    assert stored(admin, BREAKER, f)[1] is None  # preview writes nothing

    body = {
        "rule_version": IMPROVEMENT_RULES_VERSION,
        "items": [{"request_id": str(request_id), "user_id": item["user_id"]}],
    }
    applied = client.post(f"{path}/apply", json=body, headers=principal_header(CS_MANAGER))
    assert applied.status_code == 200, applied.text
    assert applied.json()["data"]["items"][0]["outcome"] == "ASSIGNED"
    assert stored(admin, BREAKER, f)[1:] == (agents[0], version + 1)
    assert decisions(admin, request_id)[-1] == (
        agents[0],
        "FAMILY",
        "LV_BREAKER",
        "BATCH",
        CS_MANAGER.user_id,
    )
    again = client.post(f"{path}/apply", json=body, headers=principal_header(CS_MANAGER))
    assert again.json()["data"]["items"][0]["outcome"] == "ALREADY_ASSIGNED"
    assert stored(admin, BREAKER, f)[2] == version + 1
    denied = client.post(f"{path}/apply", json=body, headers=principal_header(AGENT))
    assert denied.status_code == 403
    missing = client.post(
        f"{path}/apply",
        json={**body, "items": [{"request_id": str(uuid4()), "user_id": None}]},
        headers=principal_header(CS_MANAGER),
    )
    assert missing.json()["data"]["items"][0]["outcome"] == "NOT_FOUND"


def test_agent_table_shows_open_request_load(client, admin, agents):
    f = fault()
    eligible_group(admin, TRANSFORMER, f)
    collect(client)
    rows = {
        UUID(i["user_id"]): i
        for i in client.get("/api/v1/cs/assignments/agents").json()["data"]["items"]
    }
    assert rows[agents[2]]["open_request_count"] == 1
    assert rows[agents[0]]["open_request_count"] == 0
