"""KAN-110 customer mail on a ticket, on a real PostgreSQL with a fake platform.

Approval hands the message to the platform; the ticket moves only when the platform reports
ACCEPTED. An unsettled send blocks transfer and closing. A linked reply reopens the ticket.
Mail registered through the platform answers its sender by default.
"""

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.analysis.service import record_draft
from backend.api.cs.main import create_app
from backend.api.cs.workflow.mail import CustomerMailSettings, configure_customer_mail
from backend.shared.access import Principal, principal_header
from backend.shared.database import connection
from backend.shared.settings import Settings
from tests.cs.test_analysis_db import draft_options
from tests.cs.test_workflow_db import AGENT, TRF_STAFF, act, code, ok, state
from tests.cs.test_workflow_db import ticket as ticket

PLATFORM = "https://platform.example.invalid"
MAILBOX = uuid4()
CUSTOMER = "customer@example.invalid"
MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})


class FakePlatform:
    """The platform's internal send API: intents keyed by (business_ref, version)."""

    def __init__(self):
        self.intents = {}
        self.by_key = {}
        self.posts = 0
        self.fail_next = None  # an exception, or an HTTP status code

    def handler(self, request):
        assert request.headers["authorization"] == "Bearer cs-token"
        assert request.url.host == "platform.example.invalid"
        path = request.url.path
        if request.method == "POST" and path == "/internal/v1/platform/mail/send-intents":
            self.posts += 1
            body = json.loads(request.content)
            key = (body["business_ref"], body["business_version"])
            fail, self.fail_next = self.fail_next, None
            if isinstance(fail, httpx.ConnectError):
                # Never reached the platform: nothing is stored.
                raise fail
            if isinstance(fail, int) and fail >= 400:
                if fail < 500:
                    return httpx.Response(
                        fail, json={"error": {"code": "MAIL_SEND_CONFLICT", "message": "x"}}
                    )
                return httpx.Response(fail, json={})
            if key in self.by_key:
                existing = self.by_key[key]
                if existing["hash"] != body["approved_payload_sha256"]:
                    return httpx.Response(
                        409, json={"error": {"code": "MAIL_SEND_CONFLICT", "message": "x"}}
                    )
                intent_id = existing["id"]
            else:
                intent_id = uuid4()
                self.by_key[key] = {"id": intent_id, "hash": body["approved_payload_sha256"]}
                self.intents[intent_id] = {"status": "QUEUED", "body": body, "attempts": 0}
            if isinstance(fail, Exception):
                # The request got through, but the answer never came back.
                raise fail
            return httpx.Response(
                202,
                json={
                    "data": {
                        "intent_id": str(intent_id),
                        "status": self.intents[intent_id]["status"],
                        "task_registered": True,
                    }
                },
            )
        if request.method == "GET" and path.startswith("/internal/v1/platform/mail/send-intents/"):
            intent = self.intents.get(UUID(path.rsplit("/", 1)[1]))
            if intent is None:
                return httpx.Response(404, json={"error": {"code": "MAIL_SEND_INTENT_NOT_FOUND"}})
            accepted = intent["status"] == "ACCEPTED"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "intent_id": path.rsplit("/", 1)[1],
                        "business_ref": intent["body"]["business_ref"],
                        "business_version": intent["body"]["business_version"],
                        "status": intent["status"],
                        "error_code": intent.get("error_code"),
                        "attempts": intent["attempts"],
                        "created_at": "2026-10-29T00:00:00+00:00",
                        "submitted_at": None,
                        "accepted_at": "2026-10-29T00:01:00+00:00" if accepted else None,
                        "sent_copy_confirmed_at": None,
                        "events": [],
                    }
                },
            )
        raise AssertionError(f"{request.method} {path}")

    def settle(self, status, error_code=None):
        [intent] = [i for i in self.intents.values() if i["status"] == "QUEUED"]
        intent["status"], intent["attempts"] = status, 1
        intent["error_code"] = error_code


@pytest.fixture
def platform():
    return FakePlatform()


@pytest.fixture
def client(cs_database, platform):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    app = create_app(settings)
    configure_customer_mail(
        app,
        CustomerMailSettings(_env_file=None, enabled=True, platform_url=PLATFORM),
        token=lambda audience: "cs-token",
        transport=httpx.MockTransport(platform.handler),
    )
    with TestClient(app, headers=principal_header(AGENT)) as test_client:
        yield test_client


def send(client, ticket_id, version, kind="ANSWER", to=(CUSTOMER,), **changes):
    body = {
        "version": version,
        "kind": kind,
        "mailbox_id": str(MAILBOX),
        "subject": "Re: Oil leakage",
        "body": "We replaced the gasket.",
        **({"to": list(to)} if to is not None else {}),
        **changes,
    }
    return act(client, ticket_id, "send", **body)


def last_send(data):
    return data["customer_sends"][-1]


def test_send_routes_are_closed_without_customer_mail_settings(cs_database, ticket):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as client:
        ok(act(client, ticket, "start-review", version=0))
        assert send(client, ticket, 1).status_code == 404
        # Closing still works without the feature: there are no sends to wait for.
        assert code(act(client, ticket, "close", version=1)) == (409, "CS_CLOSE_CONDITION_UNMET")


def test_answer_moves_the_ticket_only_when_the_platform_accepted(client, admin, platform, ticket):
    assert code(send(client, ticket, 0)) == (409, "CS_INVALID_TRANSITION")
    ok(act(client, ticket, "start-review", version=0))
    assert code(send(client, ticket, 1, to=None)) == (422, "CS_RECIPIENT_REQUIRED")
    assert send(client, ticket, 1, user=TRF_STAFF).status_code == 404  # not transferred: no scope
    assert send(client, ticket, 1, user=MANAGER).status_code == 403

    data = ok(send(client, ticket, 1, cc=["lead@example.invalid"]))
    assert (data["state"], data["version"], data["customer_sent_at"]) == ("IN_REVIEW", 2, None)
    sent = last_send(data)
    assert (sent["request_status"], sent["send_status"], sent["recorded_at"]) == (
        "REGISTERED",
        "QUEUED",
        None,
    )
    assert sent["to_addresses"] == [CUSTOMER] and sent["workflow_version"] == 1
    body = platform.intents[UUID(sent["intent_id"])]["body"]
    assert (body["business_ref"], body["business_version"], body["approved_by"]) == (
        f"cs_ticket:{ticket}",
        1,
        str(AGENT.user_id),
    )
    assert body["to"] == [CUSTOMER] and body["cc"] == ["lead@example.invalid"]

    # One unsettled send at a time; it also blocks transfer and closing.
    assert code(send(client, ticket, 2)) == (409, "CS_CUSTOMER_SEND_PENDING")
    stale = act(client, ticket, "transfer", version=2, dept_code="TS_TRF", reason="x")
    assert code(stale) == (409, "CS_CUSTOMER_SEND_PENDING")
    assert code(act(client, ticket, "close", version=2)) == (409, "CS_CUSTOMER_SEND_PENDING")
    assert state(client, ticket)["state"] == "IN_REVIEW"

    platform.settle("ACCEPTED")
    data = ok(act(client, ticket, f"sends/{sent['id']}/refresh"))
    assert (data["state"], data["version"]) == ("ANSWERED", 3)
    assert data["customer_sent_at"] is not None
    refreshed = last_send(data)
    assert (refreshed["send_status"], refreshed["attempts"]) == ("ACCEPTED", 1)
    assert refreshed["recorded_at"] is not None and refreshed["accepted_at"] is not None
    # Refreshing again changes nothing.
    again = ok(act(client, ticket, f"sends/{sent['id']}/refresh"))
    assert (again["version"], again["state"]) == (3, "ANSWERED")

    data = ok(act(client, ticket, "close", version=3))
    assert data["state"] == "CLOSED"
    actions = admin.execute(
        "SELECT version, changes->'action'->>'to', changes->'message_id'->>'to' "
        "FROM cs.audit_events WHERE entity_id=%s AND entity_type='ticket_workflow' "
        "ORDER BY version",
        (ticket,),
    ).fetchall()
    assert [(v, a) for v, a, _ in actions] == [
        (1, "start_review"),
        (2, "approve_send"),
        (3, "send_answer"),
        (4, "close"),
    ]
    assert actions[2][2] == sent["intent_id"]
    assert platform.posts == 1


def test_information_request_waits_and_a_linked_reply_reopens(
    client, cs_database, platform, ticket
):
    ok(act(client, ticket, "start-review", version=0))
    sent = last_send(ok(send(client, ticket, 1, kind="INFO_REQUEST")))
    platform.settle("ACCEPTED")
    data = ok(act(client, ticket, f"sends/{sent['id']}/refresh"))
    assert data["state"] == "WAITING_CUSTOMER"
    assert code(act(client, ticket, "close", version=3)) == (409, "CS_INVALID_TRANSITION")

    reply = {
        "message_id": str(uuid4()),
        "method": "HEADER",
        "received_at": datetime(2026, 10, 29, 10, tzinfo=UTC).isoformat(),
    }
    response = client.post(f"/internal/v1/cs/tickets/{ticket}/customer-reply", json=reply)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "ticket_id": str(ticket),
        "message_id": reply["message_id"],
        "state": "IN_REVIEW",
        "reopened": True,
        "recorded": True,
    }
    data = state(client, ticket)
    assert (data["state"], data["version"]) == ("IN_REVIEW", 4)
    [recorded] = data["customer_replies"]
    assert (recorded["message_id"], recorded["method"], recorded["reopened"]) == (
        reply["message_id"],
        "HEADER",
        True,
    )
    # The same mail reported again changes nothing.
    again = client.post(f"/internal/v1/cs/tickets/{ticket}/customer-reply", json=reply)
    assert again.json()["data"]["recorded"] is False and again.json()["data"]["reopened"] is False
    assert state(client, ticket)["version"] == 4
    # A reply while in review is recorded, but the state stays.
    other = {**reply, "message_id": str(uuid4()), "method": "THREAD"}
    assert client.post(f"/internal/v1/cs/tickets/{ticket}/customer-reply", json=other).json()[
        "data"
    ] == {
        "ticket_id": str(ticket),
        "message_id": other["message_id"],
        "state": "IN_REVIEW",
        "reopened": False,
        "recorded": True,
    }
    missing = client.post(f"/internal/v1/cs/tickets/{uuid4()}/customer-reply", json=reply)
    assert missing.status_code == 404

    # Closed by a sent message, then reopened by a later reply: closing data is cleared.
    data = ok(act(client, ticket, "close", version=4))
    assert data["state"] == "CLOSED"
    late = {**reply, "message_id": str(uuid4())}
    client.post(f"/internal/v1/cs/tickets/{ticket}/customer-reply", json=late)
    data = state(client, ticket)
    assert (data["state"], data["closed_at"], data["closed_by"]) == ("IN_REVIEW", None, None)
    assert len(data["customer_replies"]) == 3
    with psycopg.connect(
        make_conninfo(cs_database, user="cs_app", password="test-only")
    ) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute("DELETE FROM cs.ticket_customer_replies WHERE ticket_id=%s", (ticket,))
        runtime.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute("DELETE FROM cs.ticket_customer_sends WHERE ticket_id=%s", (ticket,))


def test_an_unknown_hand_over_blocks_closing_until_a_retry_finds_the_intent(
    client, platform, ticket
):
    ok(act(client, ticket, "start-review", version=0))
    platform.fail_next = httpx.ReadTimeout("no answer")
    sent = last_send(ok(send(client, ticket, 1)))
    assert (sent["request_status"], sent["request_error"], sent["intent_id"]) == (
        "REQUEST_UNKNOWN",
        "PLATFORM_NO_ANSWER",
        None,
    )
    assert code(act(client, ticket, "close", version=2)) == (409, "CS_CUSTOMER_SEND_PENDING")
    assert code(send(client, ticket, 2)) == (409, "CS_CUSTOMER_SEND_PENDING")
    assert code(act(client, ticket, f"sends/{sent['id']}/refresh")) == (
        409,
        "CS_CUSTOMER_SEND_NOT_REGISTERED",
    )
    # The first call did reach the platform. The retry carries the same version and content,
    # so the platform returns that intent instead of creating a second one.
    data = ok(act(client, ticket, f"sends/{sent['id']}/retry", version=2))
    retried = last_send(data)
    assert (retried["request_status"], retried["send_status"], data["version"]) == (
        "REGISTERED",
        "QUEUED",
        2,
    )
    assert len(platform.intents) == 1 and platform.posts == 2
    assert code(act(client, ticket, f"sends/{sent['id']}/retry", version=2)) == (
        409,
        "CS_CUSTOMER_SEND_NOT_RETRYABLE",
    )

    # A platform 5xx is also unknown; a connection failure means nothing was sent.
    platform.settle("ACCEPTED")
    ok(act(client, ticket, f"sends/{sent['id']}/refresh"))
    assert state(client, ticket)["state"] == "ANSWERED"


def test_rejected_and_failed_sends_leave_the_ticket_in_review(client, platform, ticket):
    ok(act(client, ticket, "start-review", version=0))
    platform.fail_next = 409
    sent = last_send(ok(send(client, ticket, 1)))
    assert (sent["request_status"], sent["request_error"]) == ("REJECTED", "MAIL_SEND_CONFLICT")
    # Rejected is final: the agent may approve a corrected message (new version).
    platform.fail_next = 503
    unknown = last_send(ok(send(client, ticket, 2)))
    assert unknown["request_status"] == "REQUEST_UNKNOWN"
    assert code(send(client, ticket, 3)) == (409, "CS_CUSTOMER_SEND_PENDING")
    # Not retryable while unknown? It is: the retry reaches the platform this time.
    registered = last_send(ok(act(client, ticket, f"sends/{unknown['id']}/retry", version=3)))
    assert registered["request_status"] == "REGISTERED"
    platform.settle("FAILED", "GRAPH_BAD_REQUEST")
    data = ok(act(client, ticket, f"sends/{unknown['id']}/refresh"))
    failed = last_send(data)
    assert (data["state"], failed["send_status"], failed["send_error_code"]) == (
        "IN_REVIEW",
        "FAILED",
        "GRAPH_BAD_REQUEST",
    )
    assert failed["recorded_at"] is None and data["customer_sent_at"] is None
    # A failed send does not count as a sent message for closing.
    assert code(act(client, ticket, "close", version=3)) == (409, "CS_CLOSE_CONDITION_UNMET")
    # ...but it no longer blocks a new send or a transfer.
    transferred = ok(act(client, ticket, "transfer", version=3, dept_code="TS_TRF", reason="x"))
    assert transferred["state"] == "TRANSFERRED"


def test_unreachable_platform_is_a_failed_request_that_can_be_retried(client, platform, ticket):
    ok(act(client, ticket, "start-review", version=0))
    platform.fail_next = httpx.ConnectError("refused")
    sent = last_send(ok(send(client, ticket, 1)))
    assert (sent["request_status"], sent["request_error"]) == (
        "REQUEST_FAILED",
        "PLATFORM_UNREACHABLE",
    )
    # Nothing reached the platform, so a new approval is allowed as well as a retry.
    assert len(platform.intents) == 0
    retried = last_send(ok(act(client, ticket, f"sends/{sent['id']}/retry", version=2)))
    assert retried["request_status"] == "REGISTERED" and len(platform.intents) == 1


def test_new_approval_replaces_failed_request_for_retry(client, platform, ticket):
    ok(act(client, ticket, "start-review", version=0))
    platform.fail_next = httpx.ConnectError("refused")
    old = last_send(ok(send(client, ticket, 1)))
    ok(send(client, ticket, 2, body="Corrected answer"))
    posts = platform.posts
    assert code(act(client, ticket, f"sends/{old['id']}/retry", version=3)) == (
        409,
        "CS_CUSTOMER_SEND_NOT_RETRYABLE",
    )
    assert platform.posts == posts and len(platform.intents) == 1


@pytest.mark.parametrize("unknown", [False, True])
def test_disabling_mail_does_not_bypass_pending_send_guard(client, platform, ticket, unknown):
    ok(act(client, ticket, "start-review", version=0))
    if unknown:
        platform.fail_next = httpx.ReadTimeout("no answer")
    ok(send(client, ticket, 1))
    configure_customer_mail(client.app, CustomerMailSettings(_env_file=None, enabled=False))
    response = act(client, ticket, "close", version=2)
    assert response.status_code == (409 if unknown else 404)
    if unknown:
        assert code(response)[1] == "CS_CUSTOMER_SEND_PENDING"
    assert state(client, ticket)["state"] == "IN_REVIEW"


def test_mail_registered_ticket_answers_its_sender_and_hides_mail_from_departments(
    client, admin, cs_database, platform
):
    message_id, mailbox_id = uuid4(), uuid4()
    mail = {
        "message_id": str(message_id),
        "mailbox_id": str(mailbox_id),
        "external_id": "AAMk-synthetic-graph-id",
        "sender_address": "plant@customer.invalid",
        "received_at": datetime(2026, 10, 29, 9, tzinfo=UTC).isoformat(),
        "subject": "Oil leakage at gasket",
        "body": "Oil leaks from the gasket of TRF-OIL-1000.",
        "body_language": "en",
        "customer_external_id": "CUST-0374",
        "product_code": "TRF-OIL-1000",
    }
    response = client.post("/internal/v1/cs/tickets/from-mail", json=mail)
    assert response.status_code == 201, response.text
    created = response.json()["data"]
    assert (created["channel"], created["external_id"]) == ("EMAIL", "AAMk-synthetic-graph-id")
    # 메일에는 등급이 없다. 없는 값을 채우지 않는다(규칙만 표준 기준으로 계산).
    assert admin.execute(
        "SELECT customer_tier FROM cs.tickets WHERE id=%s", (UUID(created["id"]),)
    ).fetchone() == (None,)
    assert created["received_at"].startswith("2026-10-29T09:00:00")
    ticket_id = UUID(created["id"])
    try:
        assert admin.execute(
            "SELECT source, source_instance, derivation_kind FROM cs.tickets WHERE id=%s",
            (ticket_id,),
        ).fetchone() == ("microsoft_graph", str(mailbox_id), "SYSTEM")
        again = client.post("/internal/v1/cs/tickets/from-mail", json=mail)
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "CS_MAIL_ALREADY_REGISTERED"
        assert again.json()["error"]["details"] == [
            {"field": "ticket_id", "reason": str(ticket_id)}
        ]
        forbidden = client.post(
            "/internal/v1/cs/tickets/from-mail", json=mail, headers=principal_header(MANAGER)
        )
        assert forbidden.status_code == 403

        data = state(client, ticket_id)
        assert data["mail_origin"]["sender_address"] == "plant@customer.invalid"
        assert data["mail_origin"]["message_id"] == str(message_id)
        ok(act(client, ticket_id, "start-review", version=0))
        sent = last_send(ok(send(client, ticket_id, 1, to=None)))
        assert sent["to_addresses"] == ["plant@customer.invalid"]
        platform.settle("ACCEPTED")
        ok(act(client, ticket_id, f"sends/{sent['id']}/refresh"))
        # Department users see the workflow but not the customer mail.
        with psycopg.connect(cs_database, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO cs.ticket_transfers (id, ticket_id, round, dept_code, status, reason, "
                "transferred_by, ended_by, ended_at, end_reason) "
                "VALUES (%s, %s, 1, 'TS_TRF', 'RECALLED', 'x', %s, %s, now(), 'x')",
                (uuid4(), ticket_id, AGENT.user_id, AGENT.user_id),
            )
        dept_view = state(client, ticket_id, TRF_STAFF)
        assert (dept_view["mail_origin"], dept_view["customer_sends"]) == (None, [])
        assert state(client, ticket_id)["customer_sends"] != []
    finally:
        for table in (
            "ticket_customer_sends",
            "ticket_mail_origins",
            "ticket_customer_replies",
            "ticket_transfers",
            "ticket_workflows",
            "audit_events",
        ):
            column = "entity_id" if table == "audit_events" else "ticket_id"
            admin.execute(f"DELETE FROM cs.{table} WHERE {column}=%s", (ticket_id,))
        admin.execute("DELETE FROM cs.tickets WHERE id=%s", (ticket_id,))


def test_a_concurrent_registration_of_the_same_mail_is_a_conflict_not_an_error(client, admin):
    """The other request inserted the ticket between our check and our insert."""
    mailbox_id, other = uuid4(), uuid4()
    graph_id = f"AAMk-race-{uuid4()}"
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'microsoft_graph', %s, %s, 'demo', 'SYSTEM', now(), "
        "now(), 'EMAIL', 'en', 'CUST-0374', 'TRF-OIL-1000', 'x')",
        (other, str(mailbox_id), graph_id),
    )
    try:
        response = client.post(
            "/internal/v1/cs/tickets/from-mail",
            json={
                "message_id": str(uuid4()),
                "mailbox_id": str(mailbox_id),
                "external_id": graph_id,
                "sender_address": "plant@customer.invalid",
                "received_at": datetime(2026, 10, 29, 9, tzinfo=UTC).isoformat(),
                "body": "Oil leaks.",
                "body_language": "en",
                "customer_external_id": "CUST-0374",
                "product_code": "TRF-OIL-1000",
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["details"] == [{"field": "ticket_id", "reason": str(other)}]
        assert admin.execute(
            "SELECT count(*) FROM cs.tickets WHERE external_id=%s", (graph_id,)
        ).fetchone() == (1,)
    finally:
        admin.execute("DELETE FROM cs.tickets WHERE id=%s", (other,))


def stored_draft(cs_database, ticket_id, outcome="VALID", asks=()):
    """A draft run saved through record_draft (KAN-107), as the job runner will. `asks` are the
    information codes the INFO_FIRST option asks for."""
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )

    async def run():
        async with connection(settings) as conn:
            return await record_draft(
                conn,
                ticket_id,
                {
                    "outcome": outcome,
                    "draft": with_questions(draft_options(), asks) if outcome == "VALID" else None,
                    "input_version": "cs-draft-input-v1",
                    "error_code": None if outcome == "VALID" else "CS_DRAFT_INVALID",
                },
            )

    return asyncio.run(run())


def with_questions(draft, codes):
    for option in draft["options"]:
        if option["label"] == "INFO_FIRST":
            option["info_requests"] = [
                {"info_code": c, "question": "Please send it."} for c in codes
            ]
    return draft


def test_an_information_request_keeps_the_codes_it_asked_for(
    client, admin, cs_database, platform, ticket
):
    """KAN-107: the codes a sent message asked for are what later drafts treat as asked."""
    ok(act(client, ticket, "start-review", version=0))
    draft = stored_draft(cs_database, ticket, asks=("serial_no", "photos"))
    picked = {"draft_id": str(draft["id"]), "draft_option": "INFO_FIRST"}
    for invalid in (["not_a_code"], ["serial_no", "serial_no"], ["Serial-No"]):
        response = send(client, ticket, 1, kind="INFO_REQUEST", requested_info_codes=invalid)
        assert response.status_code == 422
    assert code(
        send(client, ticket, 1, kind="INFO_REQUEST", requested_info_codes=["x_unknown"])
    ) == (
        422,
        "CS_INFO_CODE_UNKNOWN",
    )
    # Not given with a draft: the chosen option's questions, sorted.
    sent = last_send(ok(send(client, ticket, 1, kind="INFO_REQUEST", **picked)))
    assert sent["requested_info_codes"] == ["photos", "serial_no"]
    stored = admin.execute(
        "SELECT requested_info_codes FROM cs.ticket_customer_sends WHERE id=%s", (sent["id"],)
    ).fetchone()
    assert stored == (["photos", "serial_no"],)
    approval = admin.execute(
        "SELECT changes->'requested_info_codes' FROM cs.audit_events WHERE entity_id=%s "
        "AND changes->'action'->>'to'='approve_send'",
        (ticket,),
    ).fetchone()
    assert approval == ({"to": ["photos", "serial_no"]},)


def test_an_edited_message_can_say_it_asks_nothing(client, admin, cs_database, platform, ticket):
    ok(act(client, ticket, "start-review", version=0))
    draft = stored_draft(cs_database, ticket, asks=("serial_no",))
    data = ok(
        send(
            client,
            ticket,
            1,
            draft_id=str(draft["id"]),
            draft_option="INFO_FIRST",
            requested_info_codes=[],
        )
    )
    assert last_send(data)["requested_info_codes"] == []


def test_a_reviewed_draft_becomes_the_sent_message_and_a_draft_alone_never_closes(
    client, admin, cs_database, platform, ticket
):
    """KAN-110 draft review: draft → agent review → approved send → ACCEPTED → close."""
    ok(act(client, ticket, "start-review", version=0))
    draft = stored_draft(cs_database, ticket)
    failed = stored_draft(cs_database, ticket, outcome="SCHEMA_VIOLATION")
    # A draft is not a sent message: closing is still refused.
    assert code(act(client, ticket, "close", version=1)) == (409, "CS_CLOSE_CONDITION_UNMET")

    assert code(send(client, ticket, 1, draft_id=str(failed["id"]), draft_option="CONCISE")) == (
        422,
        "CS_DRAFT_INVALID",
    )
    assert code(send(client, ticket, 1, draft_id=str(uuid4()), draft_option="CONCISE")) == (
        422,
        "CS_DRAFT_INVALID",
    )
    assert send(client, ticket, 1, draft_id=str(draft["id"])).status_code == 422
    # The agent edits the chosen option before approving; the body is what was approved.
    edited = "We replaced the gasket. Please check the oil level after 24 hours."
    data = ok(
        send(client, ticket, 1, draft_id=str(draft["id"]), draft_option="DETAILED", body=edited)
    )
    sent = last_send(data)
    assert (sent["draft_id"], sent["draft_option"], sent["body"]) == (
        str(draft["id"]),
        "DETAILED",
        edited,
    )
    assert platform.intents[UUID(sent["intent_id"])]["body"]["body"] == edited
    # Approval is not sending: still in review until the platform accepts.
    assert data["state"] == "IN_REVIEW"
    assert code(act(client, ticket, "close", version=2)) == (409, "CS_CUSTOMER_SEND_PENDING")
    platform.settle("ACCEPTED")
    assert ok(act(client, ticket, f"sends/{sent['id']}/refresh"))["state"] == "ANSWERED"
    assert ok(act(client, ticket, "close", version=3))["state"] == "CLOSED"
    approval = admin.execute(
        "SELECT changes FROM cs.audit_events WHERE entity_type='ticket_workflow' AND entity_id=%s "
        "AND changes->'action'->>'to'='approve_send'",
        (ticket,),
    ).fetchone()[0]
    assert approval["draft_id"] == {"to": str(draft["id"])}
    assert approval["draft_option"] == {"to": "DETAILED"}
    # The stored draft is unchanged by the edit.
    assert admin.execute(
        "SELECT draft->'options'->1->'sentences'->0->>'text' FROM cs.ticket_drafts WHERE id=%s",
        (draft["id"],),
    ).fetchone() == ("We replaced the gasket.",)
