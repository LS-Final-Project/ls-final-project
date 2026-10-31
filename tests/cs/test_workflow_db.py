"""KAN-108 ticket workflow on a real PostgreSQL: review, transfer, department results, close.

Received is not reviewed and reviewed is not closed: a submitted department result must be
accepted by a C/S agent before it counts, and closing stays a separate C/S decision.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.departments import CS_DEPARTMENT, TRANSFER_DEPARTMENTS
from backend.shared.settings import Settings

IDENTITY_DEPARTMENTS = Path("infra/migrations/platform/20261023_KAN-78_manager_scopes.sql")
RESPONSIBILITY_MIGRATION = Path("infra/migrations/cs/20261031_KAN-193_return_responsibility.sql")
AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
TRF_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_TRF")
LV_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_LV")
CS_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})
TRF_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"TS_TRF"})


# Runs in the full checkout (auth-database job). The backend test image has no infra files.
@pytest.mark.skipif(not IDENTITY_DEPARTMENTS.exists(), reason="infra/migrations not in image")
def test_department_codes_match_identity_departments():
    codes = set(re.findall(r"\('([A-Z][A-Z0-9_]*)', '", IDENTITY_DEPARTMENTS.read_text()))
    assert codes == TRANSFER_DEPARTMENTS | {CS_DEPARTMENT}


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
def ticket(admin):
    ticket_id = uuid4()
    received = datetime(2026, 10, 1, tzinfo=UTC)
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan108', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', 'en', 'CUST-0374', 'TRF-OIL-1000', 'Oil leakage at gasket.')",
        (ticket_id, f"T-{ticket_id}", received, received),
    )
    return ticket_id


def act(client, ticket_id, action, user=AGENT, **body):
    path = f"/api/v1/cs/tickets/{ticket_id}/workflow/{action}"
    return client.post(path, json=body, headers=principal_header(user))


def state(client, ticket_id, user=AGENT) -> dict:
    response = client.get(
        f"/api/v1/cs/tickets/{ticket_id}/workflow", headers=principal_header(user)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def code(response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def transferred(client, ticket_id, dept="TS_TRF") -> dict:
    ok(act(client, ticket_id, "start-review", version=0))
    return ok(act(client, ticket_id, "transfer", version=1, dept_code=dept, reason="Gasket"))


def test_received_reviewed_and_closed_are_separate_steps(client, admin, ticket):
    assert state(client, ticket)["state"] == "RECEIVED"
    assert state(client, ticket)["version"] == 0
    data = transferred(client, ticket)
    assert (data["state"], data["version"], data["owner_user_id"]) == (
        "TRANSFERRED",
        2,
        str(AGENT.user_id),
    )
    assert [(t["round"], t["dept_code"], t["status"]) for t in data["transfers"]] == [
        (1, "TS_TRF", "OPEN")
    ]

    data = ok(act(client, ticket, "dept-result", TRF_STAFF, version=2, result="Gasket replaced"))
    assert data["state"] == "IN_REVIEW"
    [result] = data["dept_results"]
    assert result["review_status"] == "SUBMITTED"
    assert data["transfers"][0]["status"] == "RESULT_SUBMITTED"

    # A submitted result is not reviewed: no close, no new transfer until it is.
    assert code(act(client, ticket, "close", version=3)) == (409, "CS_DEPT_RESULT_UNREVIEWED")
    retransfer = act(client, ticket, "transfer", version=3, dept_code="TS_LV", reason="x")
    assert code(retransfer) == (409, "CS_DEPT_RESULT_UNREVIEWED")

    review = f"dept-results/{result['id']}/review"
    stale = act(client, ticket, review, version=3, result_version=2, decision="ACCEPT")
    assert code(stale) == (409, "CS_DEPT_RESULT_VERSION_CONFLICT")
    data = ok(act(client, ticket, review, version=3, result_version=1, decision="ACCEPT"))
    assert (data["state"], data["dept_results"][0]["review_status"]) == ("IN_REVIEW", "ACCEPTED")
    again = act(client, ticket, review, version=4, result_version=2, decision="ACCEPT")
    assert code(again) == (409, "CS_DEPT_RESULT_ALREADY_REVIEWED")

    data = ok(act(client, ticket, "close", version=4))
    assert (data["state"], data["closed_by"]) == ("CLOSED", str(AGENT.user_id))
    listed = client.get("/api/v1/cs/tickets", headers=principal_header(TRF_STAFF)).json()
    assert str(ticket) in {item["id"] for item in listed["data"]["items"]}
    assert {i["workflow_state"] for i in listed["data"]["items"] if i["id"] == str(ticket)} == {
        "CLOSED"
    }

    events = admin.execute(
        "SELECT entity_type, action, version, changes->'action'->>'to' FROM cs.audit_events "
        "WHERE entity_id IN (%s, %s) ORDER BY entity_type, version",
        (ticket, result["id"]),
    ).fetchall()
    assert events == [
        ("dept_result", "create", 1, None),
        ("dept_result", "review", 2, None),
        ("ticket_workflow", "transition", 1, "start_review"),
        ("ticket_workflow", "transition", 2, "transfer"),
        ("ticket_workflow", "transition", 3, "dept_result"),
        ("ticket_workflow", "transition", 4, "review_dept_result"),
        ("ticket_workflow", "transition", 5, "close"),
    ]
    reviewed = admin.execute(
        "SELECT changes FROM cs.audit_events WHERE entity_id = %s AND action = 'review'",
        (result["id"],),
    ).fetchone()[0]
    assert reviewed["reviewed_version"] == {"to": 1}


def test_rework_reopens_the_same_department(client, ticket):
    transferred(client, ticket)
    data = ok(act(client, ticket, "dept-result", TRF_STAFF, version=2, result="Checked"))
    review = f"dept-results/{data['dept_results'][0]['id']}/review"
    missing_note = act(client, ticket, review, version=3, result_version=1, decision="REWORK")
    assert missing_note.status_code == 422
    data = ok(
        act(client, ticket, review, version=3, result_version=1, decision="REWORK", note="Photos?")
    )
    assert data["state"] == "TRANSFERRED"
    assert [(t["round"], t["dept_code"], t["status"]) for t in data["transfers"]] == [
        (1, "TS_TRF", "RESULT_SUBMITTED"),
        (2, "TS_TRF", "OPEN"),
    ]
    data = ok(act(client, ticket, "dept-result", TRF_STAFF, version=4, result="Photos added"))
    assert [r["review_status"] for r in data["dept_results"]] == ["REWORK_REQUESTED", "SUBMITTED"]


def test_wrong_transfer_is_returned_or_recalled(client, ticket):
    transferred(client, ticket)
    assert act(client, ticket, "return", TRF_STAFF, version=2, reason="").status_code == 422
    data = ok(act(client, ticket, "return", TRF_STAFF, version=2, reason="Not a transformer"))
    assert data["state"] == "IN_REVIEW"
    assert data["transfers"][0]["end_reason"] == "Not a transformer"
    # A returned transfer is not a department result, so it does not allow closing.
    assert code(act(client, ticket, "close", version=3)) == (409, "CS_CLOSE_CONDITION_UNMET")

    ok(act(client, ticket, "transfer", version=3, dept_code="TS_LV", reason="LV breaker"))
    # TS_TRF still reads the ticket through its earlier transfer but cannot act on it.
    assert state(client, ticket, TRF_STAFF)["state"] == "TRANSFERRED"
    assert act(client, ticket, "dept-result", TRF_STAFF, version=4, result="x").status_code == 403
    data = ok(act(client, ticket, "recall", version=4, reason="Customer withdrew"))
    assert [t["status"] for t in data["transfers"]] == ["RETURNED_BY_DEPT", "RECALLED"]
    # The recalled department did not report anything either.
    assert code(act(client, ticket, "close", version=5)) == (409, "CS_CLOSE_CONDITION_UNMET")


def test_scope_role_state_and_version_are_checked(client, ticket):
    # Never transferred to TS_LV: the ticket does not exist for that department.
    assert act(client, ticket, "start-review", LV_STAFF, version=0).status_code == 404
    assert state(client, ticket, CS_MANAGER)["state"] == "RECEIVED"
    assert act(client, ticket, "start-review", CS_MANAGER, version=0).status_code == 403
    assert code(act(client, ticket, "close", version=0)) == (409, "CS_INVALID_TRANSITION")
    ok(act(client, ticket, "start-review", version=0))
    stale = act(client, ticket, "start-review", version=0)
    assert code(stale) == (409, "CS_INVALID_TRANSITION")
    stale = act(client, ticket, "transfer", version=0, dept_code="TS_TRF", reason="x")
    assert code(stale) == (409, "CS_WORKFLOW_VERSION_CONFLICT")
    bad_department = act(client, ticket, "transfer", version=1, dept_code="GCS_L1", reason="x")
    assert bad_department.status_code == 422
    ok(act(client, ticket, "transfer", version=1, dept_code="TS_TRF", reason="x"))
    # The transformer manager now reads the ticket but cannot act or reassign.
    assert state(client, ticket, TRF_MANAGER)["state"] == "TRANSFERRED"
    assert act(client, ticket, "dept-result", TRF_MANAGER, version=2, result="x").status_code == 403
    assert act(client, ticket, "dept-result", AGENT, version=2, result="x").status_code == 403
    assert act(client, ticket, "dept-result", LV_STAFF, version=2, result="x").status_code == 404


def test_only_a_c_s_manager_reassigns_the_owner(client, ticket):
    owner = {"version": 0, "owner_user_id": str(uuid4())}
    path = f"/api/v1/cs/tickets/{ticket}/workflow/owner"
    for user in (AGENT, TRF_MANAGER):
        response = client.put(path, json=owner, headers=principal_header(user))
        assert response.status_code in (403, 404)
    data = ok(client.put(path, json=owner, headers=principal_header(CS_MANAGER)))
    assert (data["state"], data["version"], data["owner_user_id"]) == (
        "RECEIVED",
        1,
        owner["owner_user_id"],
    )
    # The first reviewer does not take over an assigned ticket.
    data = ok(act(client, ticket, "start-review", version=1))
    assert data["owner_user_id"] == owner["owner_user_id"]
    stale = client.put(path, json=owner, headers=principal_header(CS_MANAGER))
    assert code(stale) == (409, "CS_WORKFLOW_VERSION_CONFLICT")


def test_service_cannot_remove_workflow_history(cs_database, client, ticket):
    transferred(client, ticket)
    runtime = make_conninfo(cs_database, user="cs_app", password="test-only")
    for table in ("cs.ticket_transfers", "cs.ticket_workflows"):
        with psycopg.connect(runtime) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"DELETE FROM {table} WHERE ticket_id = %s", (ticket,))
    # The database also keeps a single open transfer per ticket.
    with psycopg.connect(cs_database) as conn, pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO cs.ticket_transfers (id, ticket_id, round, dept_code, status, reason, "
            "transferred_by) VALUES (%s, %s, 9, 'TS_LV', 'OPEN', 'x', %s)",
            (uuid4(), ticket, uuid4()),
        )


def test_return_is_confirmed_by_a_person_after_review_starts(client, admin, ticket):
    note = "No fault found; customer ordered the wrong rating"
    # Not at intake: the customer's stated reason is on the ticket, confirmation needs review.
    early = act(
        client,
        ticket,
        "return-subtype",
        version=0,
        responsibility="NON_MANUFACTURING",
        response_note="x",
    )
    assert code(early) == (409, "CS_INVALID_TRANSITION")
    ok(act(client, ticket, "start-review", version=0))
    # Scope before role (permissions 1.3): a department outside the ticket sees nothing, and a
    # C/S manager who sees it still may not confirm.
    inspected = {"responsibility": "MANUFACTURING", "response_note": "Inspected"}
    staff = act(client, ticket, "return-subtype", user=TRF_STAFF, version=1, **inspected)
    assert staff.status_code == 404
    manager = act(client, ticket, "return-subtype", user=CS_MANAGER, version=1, **inspected)
    assert manager.status_code == 403
    # A response note is required; values outside the lists and a non-manufacturing reason
    # on another responsibility are refused. The old subtype field is not accepted.
    for invalid in (
        {"responsibility": "MANUFACTURING"},
        {"responsibility": "UNSURE", "response_note": "?"},
        {**inspected, "cause_type": "TRANSPORT"},
        {**inspected, "non_manufacturing_reason": "Damaged in transport"},
        {"return_subtype": "DEFECT", "reason": "Inspected"},
    ):
        assert act(client, ticket, "return-subtype", version=1, **invalid).status_code == 422
    first = ok(
        act(
            client,
            ticket,
            "return-subtype",
            version=1,
            responsibility="NON_MANUFACTURING",
            cause_type="SETTING",
            non_manufacturing_reason="Wrong rating ordered",
            response_note=note,
        )
    )
    assert first["state"] == "IN_REVIEW" and first["version"] == 2
    assert (first["return_responsibility"], first["return_cause_type"]) == (
        "NON_MANUFACTURING",
        "SETTING",
    )
    assert first["return_non_manufacturing_reason"] == "Wrong rating ordered"
    assert first["return_response_note"] == note
    # Routing keeps reading DEFECT/NON_DEFECT, derived from responsibility.
    assert first["return_subtype_confirmed"] == "NON_DEFECT"
    assert first["return_subtype_confirmed_by"] == str(AGENT.user_id)
    assert first["return_subtype_confirmed_at"]
    # Confirming again overwrites every field with a new version; the stale version is refused.
    assert code(act(client, ticket, "return-subtype", version=1, **inspected)) == (
        409,
        "CS_WORKFLOW_VERSION_CONFLICT",
    )
    second = ok(
        act(
            client,
            ticket,
            "return-subtype",
            version=2,
            responsibility="MANUFACTURING",
            cause_type="HARDWARE",
            response_note="Winding insulation failed on test",
        )
    )
    assert (second["return_subtype_confirmed"], second["version"]) == ("DEFECT", 3)
    assert second["return_non_manufacturing_reason"] is None
    # Inspection could not decide: recorded, but no subtype is confirmed for routing.
    third = ok(
        act(
            client,
            ticket,
            "return-subtype",
            version=3,
            responsibility="UNDETERMINED",
            response_note="Unit not yet returned; waiting for the carrier",
        )
    )
    assert (third["return_responsibility"], third["return_subtype_confirmed"]) == (
        "UNDETERMINED",
        None,
    )
    assert third["return_cause_type"] is None and third["return_subtype_confirmed_by"]
    audits = admin.execute(
        "SELECT changes FROM cs.audit_events WHERE entity_id = %s AND entity_type = 'ticket_workflow' "
        "AND changes->'action'->>'to' = 'confirm_return_subtype' ORDER BY version",
        (ticket,),
    ).fetchall()
    assert len(audits) == 3
    assert audits[0][0]["provenance_kind"]["to"] == "human_input"
    assert audits[1][0]["return_responsibility"] == {
        "from": "NON_MANUFACTURING",
        "to": "MANUFACTURING",
    }
    assert audits[2][0]["return_subtype_confirmed"] == {"from": "DEFECT", "to": None}
    # The database keeps the confirmation whole and the derived subtype consistent (DB CHECK).
    for broken in (
        "return_response_note = NULL",
        "return_responsibility = 'FOO'",
        "return_subtype_confirmed = 'DEFECT'",
        "return_non_manufacturing_reason = 'Transport'",
        "return_cause_type = 'TRANSPORT'",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(
                f"UPDATE cs.ticket_workflows SET {broken} WHERE ticket_id = %s", (ticket,)
            )


# Runs in the full checkout (auth-database job). The backend test image has no infra files.
@pytest.mark.skipif(not RESPONSIBILITY_MIGRATION.exists(), reason="infra/migrations not in image")
def test_a_subtype_confirmed_before_responsibility_is_carried_over(client, admin, ticket):
    """A row confirmed with the first KAN-193 columns (DEFECT/NON_DEFECT + reason) gets its
    responsibility and response note when the migration runs again; nothing else changes."""
    ok(act(client, ticket, "start-review", version=0))
    confirmed_by = uuid4()
    admin.execute(
        "ALTER TABLE cs.ticket_workflows DROP CONSTRAINT ticket_workflows_return_confirmation_check"
    )
    admin.execute(
        "UPDATE cs.ticket_workflows SET return_subtype_confirmed = 'DEFECT', "
        "return_subtype_confirmed_reason = 'Winding failed', return_subtype_confirmed_by = %s, "
        "return_subtype_confirmed_at = now() WHERE ticket_id = %s",
        (confirmed_by, ticket),
    )
    migration = RESPONSIBILITY_MIGRATION.read_text().splitlines()
    admin.execute("\n".join(line for line in migration if not line.startswith("\\")))
    row = admin.execute(
        "SELECT return_responsibility, return_cause_type, return_response_note, "
        "return_subtype_confirmed, return_subtype_confirmed_by FROM cs.ticket_workflows "
        "WHERE ticket_id = %s",
        (ticket,),
    ).fetchone()
    assert row == ("MANUFACTURING", None, "Winding failed", "DEFECT", confirmed_by)
    # The constraint is back after the re-run.
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "UPDATE cs.ticket_workflows SET return_subtype_confirmed = 'NON_DEFECT' "
            "WHERE ticket_id = %s",
            (ticket,),
        )
