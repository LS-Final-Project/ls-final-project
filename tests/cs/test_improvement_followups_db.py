"""KAN-117 re-request and recurrence rounds on a real PostgreSQL.

Signals are computed on read; a person opens the next round. Each test uses its own fault id
so the bundles do not mix.
"""

import itertools
from datetime import UTC, datetime
from uuid import uuid4

from backend.shared.access import principal_header
from tests.cs.test_improvement_transitions_db import (
    AGENT,
    PATH,
    PRODUCT,
    QA_STAFF,
    act,
    code,
    ok,
)
from tests.cs.test_improvement_transitions_db import client as client

FAULTS = itertools.count(301)


def new_fault() -> str:
    return f"FLT-{next(FAULTS)}"


def add_case(admin, fault, *, known=None, fw=None, customer=None):
    """A resolution record; known/received default to the database's now."""
    ticket_id, case_id = uuid4(), uuid4()
    at = known or admin.execute("SELECT now()").fetchone()[0]
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan117', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', 'en', %s, %s, 'Same fault again.')",
        (ticket_id, f"T-{ticket_id}", at, at, customer or f"CUST-{uuid4().hex[:6]}", PRODUCT),
    )
    admin.execute(
        "INSERT INTO cs.resolution_cases (id, ticket_id, source, source_instance, external_id, "
        "data_origin, derivation_kind, resolved_at, known_at, product_code, fault_id, symptom, "
        "root_cause, root_cause_type, action_taken, onsite_visit, resolved_by_dept, "
        "fw_version_at_issue) VALUES (%s, %s, 'test', 'kan117', %s, 'synthetic', 'PROVIDED', "
        "%s, %s, %s, %s, 'Fault', 'Cause', 'FIRMWARE', 'Update', false, 'RND_FW', %s)",
        (case_id, ticket_id, f"RC-{case_id}", at, at, PRODUCT, fault, fw),
    )
    return ticket_id, case_id


def sent_request(client, admin, fault, depts=("QA_CLAIM", "RND_FW")):
    old = datetime(2025, 10, 5, tzinfo=UTC)
    evidence = [add_case(admin, fault, known=old) for _ in range(3)]
    draft = ok(
        client.post(
            PATH,
            json={
                "request_type": "FAULT",
                "product_code": PRODUCT,
                "fault_id": fault,
                "problem_statement": "Same fault at three customers.",
                "proposed_depts": list(depts),
                "evidence": [{"ticket_id": str(t), "case_id": str(c)} for t, c in evidence],
            },
        )
    )["request"]
    reviewed = ok(
        act(client, draft["id"], "review", version=draft["version"], proposed_depts=list(depts))
    )
    return ok(act(client, draft["id"], "send", version=reviewed["request"]["version"]))


def signals(client, rid, user=AGENT, **params):
    return client.get(
        f"{PATH}/{rid}/followup-signals", params=params, headers=principal_header(user)
    )


def now_iso(admin):
    return admin.execute("SELECT now()").fetchone()[0].isoformat()


def test_three_new_records_after_send_open_a_re_request_round(client, admin):
    fault = new_fault()
    sent = sent_request(client, admin, fault)
    rid = sent["request"]["id"]
    assert [r["kind"] for r in sent["rounds"]] == ["FIRST"]
    before = ok(signals(client, rid))
    assert (before["phase"], before["decision"], before["counts"]["new_since_sent"]) == (
        "BEFORE_ACTION",
        "NONE",
        0,
    )
    assert {r["class"] for r in before["records"]} == {"IN_EARLIER_ROUND"}
    # No candidate yet: opening a round is refused.
    assert code(
        act(
            client,
            rid,
            "rounds",
            version=sent["request"]["version"],
            kind="REQUEST_AGAIN",
            as_of=now_iso(admin),
        )
    ) == (409, "CS_FOLLOWUP_NOT_CANDIDATE")
    new = [add_case(admin, fault) for _ in range(3)]
    signal = ok(signals(client, rid))
    assert (signal["decision"], signal["counts"]["candidate_index"], signal["next_action"]) == (
        "REQUEST_AGAIN",
        1,
        "OPEN_ROUND_REQUEST_AGAIN",
    )
    denied = act(
        client,
        rid,
        "rounds",
        QA_STAFF,
        version=sent["request"]["version"],
        kind="REQUEST_AGAIN",
        as_of=signal["as_of"],
    )
    assert denied.status_code == 403
    # Recurrence needs an applied action; there is none before action.
    assert code(
        act(
            client,
            rid,
            "rounds",
            version=sent["request"]["version"],
            kind="RECURRENCE",
            as_of=signal["as_of"],
        )
    ) == (409, "CS_FOLLOWUP_NOT_CANDIDATE")
    opened = ok(
        act(
            client,
            rid,
            "rounds",
            version=sent["request"]["version"],
            kind="REQUEST_AGAIN",
            as_of=signal["as_of"],
        )
    )
    assert opened["request"]["current_round"] == 2 and opened["request"]["status"] == "SENT"
    assert {d["dept_code"]: d["status"] for d in opened["departments"]} == {
        "QA_CLAIM": "SENT",
        "RND_FW": "SENT",
    }
    assert [(r["kind"], r["candidate_index"]) for r in opened["rounds"]] == [
        ("FIRST", None),
        ("REQUEST_AGAIN", 1),
    ]
    active = {e["ticket_id"] for e in opened["evidence"] if e["removed_at"] is None}
    assert {str(t) for t, _ in new} <= active
    # The count starts again from the new round; the same records are not reused.
    after = ok(signals(client, rid))
    assert after["counts"]["new_since_sent"] == 0 and after["baseline"]["round"] == 2
    events = admin.execute(
        "SELECT changes->'kind'->>'to', changes->'round'->>'to' FROM cs.audit_events "
        "WHERE entity_type='improvement_request' AND entity_id=%s "
        "AND changes->'action'->>'to'='open_round'",
        (rid,),
    ).fetchall()
    assert events == [("REQUEST_AGAIN", "2")]


def test_a_recurrence_on_an_applied_unit_reopens_a_completed_request_keeping_its_record(
    client, admin
):
    fault = new_fault()
    sent = sent_request(client, admin, fault, depts=("QA_CLAIM",))
    rid = sent["request"]["id"]
    v = sent["request"]["version"]
    v = ok(act(client, rid, "departments/QA_CLAIM/acknowledge", QA_STAFF, version=v))["request"][
        "version"
    ]
    v = ok(
        act(client, rid, "departments/QA_CLAIM/reply", QA_STAFF, version=v, reply_type="ADOPTED")
    )["request"]["version"]
    action = {
        "version": v,
        "action": "Release firmware 1.72",
        "action_scope": "Units on 1.72 or later",
        "action_date": "2025-12-01",
        "action_evidence": "Release note",
    }
    # A structured scope needs a value of its kind.
    assert (
        act(
            client,
            rid,
            "departments/QA_CLAIM/action",
            QA_STAFF,
            **action,
            scope_kind="FIRMWARE_VERSION",
        ).status_code
        == 422
    )
    assert (
        act(
            client,
            rid,
            "departments/QA_CLAIM/action",
            QA_STAFF,
            **action,
            scope_kind="FIRMWARE_VERSION",
            scope_value="latest",
        ).status_code
        == 422
    )
    acted = ok(
        act(
            client,
            rid,
            "departments/QA_CLAIM/action",
            QA_STAFF,
            **action,
            scope_kind="FIRMWARE_VERSION",
            scope_value="1.72",
        )
    )
    assert (acted["actions"][0]["scope_kind"], acted["actions"][0]["scope_value"]) == (
        "FIRMWARE_VERSION",
        "1.72",
    )
    v = acted["request"]["version"]
    v = ok(act(client, rid, "followup-plan", version=v, followup_plan="Watch 90 days"))["request"][
        "version"
    ]
    done = ok(act(client, rid, "complete", version=v, followup_result="No new case in 90 days"))
    assert done["request"]["status"] == "COMPLETED"
    v = done["request"]["version"]
    # A unit still on the old firmware is not a recurrence: counted apart, customer to notify.
    add_case(admin, fault, fw="1.60", customer="CUST-OLD")
    old_only = ok(signals(client, rid))
    assert (old_only["phase"], old_only["decision"], old_only["update_notice_customers"]) == (
        "AFTER_ACTION",
        "NONE",
        ["CUST-OLD"],
    )
    assert code(
        act(client, rid, "rounds", version=v, kind="RECURRENCE", as_of=old_only["as_of"])
    ) == (
        409,
        "CS_FOLLOWUP_NOT_CANDIDATE",
    )
    assert code(
        act(client, rid, "rounds", version=v, kind="REQUEST_AGAIN", as_of=old_only["as_of"])
    ) == (
        409,
        "CS_REQUEST_INVALID_TRANSITION",
    )
    ticket, _ = add_case(admin, fault, fw="1.72")
    signal = ok(signals(client, rid))
    assert (
        signal["decision"],
        signal["counts"]["recurrence"],
        signal["counts"]["pre_action_unit"],
    ) == (
        "RECURRENCE",
        1,
        1,
    )
    reopened = ok(act(client, rid, "rounds", version=v, kind="RECURRENCE", as_of=signal["as_of"]))
    request = reopened["request"]
    assert (request["status"], request["current_round"], request["completed_at"]) == (
        "SENT",
        2,
        None,
    )
    assert {d["dept_code"]: d["status"] for d in reopened["departments"]} == {"QA_CLAIM": "SENT"}
    previous = reopened["rounds"][-1]["previous_completion"]
    assert (previous["round"], previous["followup_result"]) == (1, "No new case in 90 days")
    assert previous["completed_at"] is not None
    assert str(ticket) in {e["ticket_id"] for e in reopened["evidence"]}


def test_signal_inputs_are_checked(client, admin):
    fault = new_fault()
    sent = sent_request(client, admin, fault)
    rid = sent["request"]["id"]
    future = signals(client, rid, as_of="2999-01-01T00:00:00Z")
    assert code(future) == (422, "CS_INVALID_AS_OF")
    past = ok(signals(client, rid, as_of="2025-10-06T00:00:00+00:00"))
    # Before the request was sent there was no round yet.
    assert past["phase"] == "NOT_SENT"
    assert signals(client, uuid4()).status_code == 404
    rows = admin.execute(
        "SELECT count(*) FROM cs.request_rounds WHERE request_id=%s", (rid,)
    ).fetchone()
    assert rows == (1,)


def test_department_users_do_not_see_other_customers_and_old_requests_get_round_one(client, admin):
    fault = new_fault()
    sent = sent_request(client, admin, fault)
    rid = sent["request"]["id"]
    for _ in range(3):
        add_case(admin, fault)
    agent_view = ok(signals(client, rid))
    assert all(r["customer_id"] for r in agent_view["records"])
    staff_view = ok(signals(client, rid, QA_STAFF))
    assert staff_view["decision"] == "REQUEST_AGAIN"
    assert {r["customer_id"] for r in staff_view["records"]} == {None}
    assert staff_view["update_notice_customers"] == []
    # A request sent before KAN-117 has no round rows (simulated by the admin connection).
    admin.execute("DELETE FROM cs.request_rounds WHERE request_id=%s", (rid,))
    opened = ok(
        act(
            client,
            rid,
            "rounds",
            version=sent["request"]["version"],
            kind="REQUEST_AGAIN",
            as_of=agent_view["as_of"],
        )
    )
    first, second = opened["rounds"]
    assert (first["kind"], first["round"], second["kind"]) == ("FIRST", 1, "REQUEST_AGAIN")
    assert first["opened_at"] == sent["request"]["sent_at"]
    assert first["opened_by"] == str(AGENT.user_id)
