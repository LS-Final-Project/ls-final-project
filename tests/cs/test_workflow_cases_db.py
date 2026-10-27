"""KAN-108 second part on a real PostgreSQL: department view limits, resolution cases and
the send/reply functions the mail paths (KAN-74, KAN-75) call."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.api.cs.workflow import service
from backend.shared.access import Principal, principal_header
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
TRF_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_TRF")
LV_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_LV")
CASE = {
    "fault_id": "FLT-067",
    "symptom": "Oil leakage at gasket",
    "root_cause": "Gasket damaged in transport",
    "root_cause_type": "WEAR_PART",
    "action_taken": "Replace gasket and top up oil",
    "onsite_visit": True,
    "resolved_by_dept": "FIELD_SVC",
}


@pytest.fixture(scope="module")
def runtime_dsn(cs_database):
    return make_conninfo(cs_database, user="cs_app", password="test-only")


@pytest.fixture(scope="module")
def client(runtime_dsn):
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(runtime_dsn))
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as test_client:
        yield test_client


@pytest.fixture
def ticket(admin):
    ticket_id = uuid4()
    received = datetime(2026, 10, 2, tzinfo=UTC)
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan108', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', 'en', 'CUST-0374', 'TRF-OIL-1000', 'Oil leakage at gasket.')",
        (ticket_id, f"T-{ticket_id}", received, received),
    )
    return ticket_id


def act(client, ticket_id, action, user=AGENT, **body):
    response = client.post(
        f"/api/v1/cs/tickets/{ticket_id}/workflow/{action}",
        json=body,
        headers=principal_header(user),
    )
    return response


def ok(response) -> dict:
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


def code(response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def closed_with_result(client, ticket_id) -> tuple[dict, str]:
    """IN_REVIEW → TS_TRF → accepted result → CLOSED. Returns the workflow and result ID."""
    ok(act(client, ticket_id, "start-review", version=0))
    ok(act(client, ticket_id, "transfer", version=1, dept_code="TS_TRF", reason="Gasket"))
    data = ok(act(client, ticket_id, "dept-result", TRF_STAFF, version=2, result="Replaced"))
    result_id = data["dept_results"][0]["id"]
    review = f"dept-results/{result_id}/review"
    ok(act(client, ticket_id, review, version=3, result_version=1, decision="ACCEPT"))
    return ok(act(client, ticket_id, "close", version=4)), result_id


def register(client, ticket_id, user=AGENT, **body):
    return client.post(
        f"/api/v1/cs/tickets/{ticket_id}/resolution-case",
        json={**CASE, **body},
        headers=principal_header(user),
    )


def test_departments_see_only_their_own_transfers_and_results(client, ticket):
    ok(act(client, ticket, "start-review", version=0))
    ok(act(client, ticket, "transfer", version=1, dept_code="TS_TRF", reason="Gasket"))
    ok(act(client, ticket, "return", TRF_STAFF, version=2, reason="Not ours"))
    ok(act(client, ticket, "transfer", version=3, dept_code="TS_LV", reason="Breaker"))
    ok(act(client, ticket, "dept-result", LV_STAFF, version=4, result="Breaker reset"))

    def seen(user):
        path = f"/api/v1/cs/tickets/{ticket}/workflow"
        data = ok(client.get(path, headers=principal_header(user)))
        return [t["dept_code"] for t in data["transfers"]], [
            r["dept_code"] for r in data["dept_results"]
        ]

    assert seen(AGENT) == (["TS_TRF", "TS_LV"], ["TS_LV"])
    assert seen(LV_STAFF) == (["TS_LV"], ["TS_LV"])
    # TS_TRF still reads the ticket, but not what TS_LV reported.
    assert seen(TRF_STAFF) == (["TS_TRF"], [])
    trf_manager = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"TS_TRF"})
    assert seen(trf_manager) == (["TS_TRF"], [])


def test_case_is_registered_once_after_closing_from_an_accepted_result(client, admin, ticket):
    assert code(register(client, ticket, version=0)) == (409, "CS_INVALID_TRANSITION")
    workflow, result_id = closed_with_result(client, ticket)
    assert register(client, ticket, TRF_STAFF, version=5).status_code == 403
    assert code(register(client, ticket, version=4)) == (409, "CS_WORKFLOW_VERSION_CONFLICT")
    assert register(client, ticket, version=5, fault_id="FLT-67").status_code == 422
    assert register(client, ticket, version=5, rma_no="").status_code == 422
    unknown = register(client, ticket, version=5, dept_result_id=str(uuid4()))
    assert code(unknown) == (422, "CS_DEPT_RESULT_NOT_ACCEPTED")

    case = ok(register(client, ticket, version=5, dept_result_id=result_id))
    assert (case["derivation_kind"], case["dept_result_id"]) == ("SYSTEM", result_id)
    assert datetime.fromisoformat(case["resolved_at"]) == datetime.fromisoformat(
        workflow["closed_at"]
    )
    assert case["product_code"] == "TRF-OIL-1000"
    # known_at is the database time, so the case is evidence right away.
    stored = admin.execute(
        "SELECT known_at <= now() FROM cs.resolution_cases WHERE id = %s", (case["id"],)
    ).fetchone()
    assert stored == (True,)
    assert datetime.fromisoformat(case["known_at"]) >= datetime.fromisoformat(case["resolved_at"])
    detail = ok(client.get(f"/api/v1/cs/tickets/{ticket}"))
    assert detail["resolution_case"]["id"] == case["id"]
    assert code(register(client, ticket, version=5)) == (409, "CS_RESOLUTION_CASE_EXISTS")
    # Registering a case does not change the workflow.
    assert ok(client.get(f"/api/v1/cs/tickets/{ticket}/workflow"))["version"] == 5
    audit = admin.execute(
        "SELECT action, version, changes->'workflow_version'->>'to' FROM cs.audit_events "
        "WHERE entity_type = 'resolution_case' AND entity_id = %s",
        (case["id"],),
    ).fetchall()
    assert audit == [("create", 1, "5")]


def test_rework_requested_result_cannot_back_a_case(client, ticket):
    ok(act(client, ticket, "start-review", version=0))
    ok(act(client, ticket, "transfer", version=1, dept_code="TS_TRF", reason="Gasket"))
    data = ok(act(client, ticket, "dept-result", TRF_STAFF, version=2, result="First try"))
    first = data["dept_results"][0]["id"]
    review = f"dept-results/{first}/review"
    ok(act(client, ticket, review, version=3, result_version=1, decision="REWORK", note="Photos"))
    data = ok(act(client, ticket, "dept-result", TRF_STAFF, version=4, result="With photos"))
    second = data["dept_results"][1]["id"]
    review = f"dept-results/{second}/review"
    ok(act(client, ticket, review, version=5, result_version=1, decision="ACCEPT"))
    ok(act(client, ticket, "close", version=6))
    refused = register(client, ticket, version=7, dept_result_id=first)
    assert code(refused) == (422, "CS_DEPT_RESULT_NOT_ACCEPTED")
    assert (
        ok(register(client, ticket, version=7, dept_result_id=second))["dept_result_id"] == second
    )


def run(runtime_dsn, function, *args):
    async def call():
        async with await psycopg.AsyncConnection.connect(runtime_dsn, row_factory=dict_row) as conn:
            return await function(conn, *args)

    return asyncio.run(call())


def test_send_and_customer_reply_move_the_state(client, admin, runtime_dsn, ticket):
    ok(act(client, ticket, "start-review", version=0))
    message = uuid4()
    with pytest.raises(ApplicationError) as stale:
        run(runtime_dsn, service.record_customer_send, ticket, "ANSWER", 0, message, AGENT.user_id)
    assert stale.value.code == "CS_WORKFLOW_VERSION_CONFLICT"
    sent = run(
        runtime_dsn, service.record_customer_send, ticket, "INFO_REQUEST", 1, message, AGENT.user_id
    )
    assert (sent["state"], sent["version"]) == ("WAITING_CUSTOMER", 2)
    assert sent["customer_sent_at"] is not None
    # Sending again needs review first.
    with pytest.raises(ApplicationError) as again:
        run(runtime_dsn, service.record_customer_send, ticket, "ANSWER", 2, message, AGENT.user_id)
    assert again.value.code == "CS_INVALID_TRANSITION"

    reply = uuid4()
    reopened = run(runtime_dsn, service.reopen_for_customer_reply, ticket, reply, None)
    assert reopened["state"] == "IN_REVIEW"
    run(runtime_dsn, service.record_customer_send, ticket, "ANSWER", 3, uuid4(), AGENT.user_id)
    ok(act(client, ticket, "close", version=4))
    reopened = run(runtime_dsn, service.reopen_for_customer_reply, ticket, uuid4(), None)
    assert (reopened["state"], reopened["closed_at"], reopened["closed_by"]) == (
        "IN_REVIEW",
        None,
        None,
    )
    # A reply during review changes nothing.
    unchanged = run(runtime_dsn, service.reopen_for_customer_reply, ticket, uuid4(), None)
    assert unchanged["version"] == reopened["version"]
    # The earlier customer message still allows closing from review.
    assert ok(act(client, ticket, "close", version=reopened["version"]))["state"] == "CLOSED"

    events = admin.execute(
        "SELECT changes->'action'->>'to', actor_user_id, changes->'message_id'->>'to' "
        "FROM cs.audit_events WHERE entity_type = 'ticket_workflow' AND entity_id = %s "
        "AND changes->'action'->>'to' IN ('send_info_request', 'customer_reply') ORDER BY version",
        (ticket,),
    ).fetchall()
    assert events[0] == ("send_info_request", AGENT.user_id, str(message))
    assert events[1] == ("customer_reply", None, str(reply))


def test_unreviewed_department_result_blocks_sending(client, runtime_dsn, ticket):
    ok(act(client, ticket, "start-review", version=0))
    ok(act(client, ticket, "transfer", version=1, dept_code="TS_TRF", reason="Gasket"))
    ok(act(client, ticket, "dept-result", TRF_STAFF, version=2, result="Replaced"))
    with pytest.raises(ApplicationError) as blocked:
        run(runtime_dsn, service.record_customer_send, ticket, "ANSWER", 3, uuid4(), AGENT.user_id)
    assert blocked.value.code == "CS_DEPT_RESULT_UNREVIEWED"


def test_service_cannot_remove_cases(runtime_dsn, client, ticket):
    closed_with_result(client, ticket)
    ok(register(client, ticket, version=5))
    with psycopg.connect(runtime_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM cs.resolution_cases WHERE ticket_id = %s", (ticket,))
