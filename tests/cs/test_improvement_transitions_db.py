"""KAN-116 improvement request transitions on a real PostgreSQL (permissions.md 2.2, 2.2.1).

A department reply is a claim and an action is a record; only the C/S agent's follow-up and
completion close the request. The request status is summarized from the current round.
"""

from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.improvements.transitions import followup_allowed, rejection_closed, summarize
from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
QA_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="QA_CLAIM")
FW_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="RND_FW")
LV_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_LV")
CS_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})
PRODUCT = "TRF-OIL-1000"
PATH = "/api/v1/cs/improvement-requests"


@pytest.fixture(scope="module")
def client(cs_database):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as test_client:
        yield test_client


def add_case(admin, fault="FLT-067"):
    ticket_id, case_id = uuid4(), uuid4()
    at = datetime(2025, 10, 5, tzinfo=UTC)
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan116', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', 'en', %s, %s, 'Oil leakage at gasket.')",
        (ticket_id, f"T-{ticket_id}", at, at, f"CUST-{uuid4().hex[:6]}", PRODUCT),
    )
    admin.execute(
        "INSERT INTO cs.resolution_cases (id, ticket_id, source, source_instance, external_id, "
        "data_origin, derivation_kind, resolved_at, known_at, product_code, fault_id, symptom, "
        "root_cause, root_cause_type, action_taken, onsite_visit, resolved_by_dept) VALUES "
        "(%s, %s, 'test', 'kan116', %s, 'synthetic', 'PROVIDED', %s, %s, %s, %s, 'Oil leak', "
        "'Gasket damaged', 'WEAR_PART', 'Replace gasket', true, 'FIELD_SVC')",
        (case_id, ticket_id, f"RC-{case_id}", at, at, PRODUCT, fault),
    )
    return ticket_id, case_id


def ok(response) -> dict:
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


def code(response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def act(client, request_id, path, user=AGENT, **body):
    return client.post(f"{PATH}/{request_id}/{path}", json=body, headers=principal_header(user))


@pytest.fixture
def draft(client, admin):
    """A fault request in DRAFT with two proposed departments."""
    evidence = [add_case(admin) for _ in range(3)]
    data = ok(
        client.post(
            PATH,
            json={
                "request_type": "FAULT",
                "product_code": PRODUCT,
                "fault_id": "FLT-067",
                "problem_statement": "Investigate gasket leaks reported by three customers.",
                "proposed_depts": ["QA_CLAIM", "RND_FW"],
                "evidence": [{"ticket_id": str(t), "case_id": str(c)} for t, c in evidence],
            },
        )
    )
    return data["request"]


def depts(data) -> dict:
    return {d["dept_code"]: d["status"] for d in data["departments"]}


def tasks(data) -> list:
    return [(o["actor"], o["dept_code"], o["task"]) for o in data["next_owners"]]


def test_review_then_send_opens_the_first_round(client, draft):
    rid, v = draft["id"], draft["version"]
    # Sending before review, and a department acting before delivery, are state errors.
    assert code(act(client, rid, "send", version=v)) == (409, "CS_REQUEST_INVALID_TRANSITION")
    reviewed = ok(act(client, rid, "review", version=v, proposed_depts=["QA_CLAIM", "RND_FW"]))
    assert (reviewed["request"]["status"], reviewed["request"]["reviewed_by"]) == (
        "REVIEWED",
        str(AGENT.user_id),
    )
    assert tasks(reviewed) == [("CS_OWNER", None, "SEND")]
    # Stale version → 409.
    assert code(act(client, rid, "send", version=v)) == (409, "CS_REQUEST_VERSION_CONFLICT")
    sent = ok(act(client, rid, "send", version=reviewed["request"]["version"]))
    assert sent["request"]["status"] == "SENT" and sent["request"]["current_round"] == 1
    assert sent["request"]["sent_at"] is not None
    assert depts(sent) == {"QA_CLAIM": "SENT", "RND_FW": "SENT"}
    assert all(d["reply_due_at"] for d in sent["departments"])
    assert tasks(sent) == [
        ("DEPARTMENT", "QA_CLAIM", "ACKNOWLEDGE"),
        ("DEPARTMENT", "RND_FW", "ACKNOWLEDGE"),
    ]


def test_document_request_without_a_receiver_cannot_be_sent(client, admin):
    ticket_id, _ = add_case(admin)
    data = ok(
        client.post(
            PATH,
            json={
                "request_type": "DOCUMENT",
                "product_code": PRODUCT,
                "doc_signal": "BROKEN_LINK",
                "problem_statement": "Catalog link is broken.",
                "proposed_depts": [],
                "evidence": [{"ticket_id": str(ticket_id)}],
            },
        )
    )["request"]
    reviewed = ok(act(client, data["id"], "review", version=data["version"]))
    assert tasks(reviewed) == [("CS_OWNER", None, "CONFIRM_RECEIVER")]
    assert code(act(client, data["id"], "send", version=reviewed["request"]["version"])) == (
        422,
        "CS_DEPARTMENT_REQUIRED",
    )


def delivered(client, draft) -> dict:
    reviewed = ok(act(client, draft["id"], "review", version=draft["version"]))
    return ok(act(client, draft["id"], "send", version=reviewed["request"]["version"]))


def test_departments_progress_independently_and_the_status_is_summarized(client, admin, draft):
    rid = draft["id"]
    sent = delivered(client, draft)
    v = sent["request"]["version"]
    # Only the receiving department's staff act for it; a stranger department sees nothing.
    assert (
        act(client, rid, "departments/QA_CLAIM/acknowledge", user=LV_STAFF, version=v).status_code
        == 404
    )
    assert (
        act(client, rid, "departments/QA_CLAIM/acknowledge", user=FW_STAFF, version=v).status_code
        == 403
    )
    assert (
        act(client, rid, "departments/QA_CLAIM/acknowledge", user=AGENT, version=v).status_code
        == 403
    )
    qa = ok(act(client, rid, "departments/QA_CLAIM/acknowledge", user=QA_STAFF, version=v))
    assert qa["request"]["status"] == "ACKNOWLEDGED" and depts(qa)["RND_FW"] == "SENT"
    v = qa["request"]["version"]
    # A reply before acknowledging, or a rejection without a reason, is refused.
    assert code(
        act(client, rid, "departments/RND_FW/reply", user=FW_STAFF, version=v, reply_type="ADOPTED")
    ) == (
        409,
        "CS_REQUEST_INVALID_TRANSITION",
    )
    assert (
        act(
            client,
            rid,
            "departments/QA_CLAIM/reply",
            user=QA_STAFF,
            version=v,
            reply_type="REJECTED",
        ).status_code
        == 422
    )
    qa = ok(
        act(
            client,
            rid,
            "departments/QA_CLAIM/reply",
            user=QA_STAFF,
            version=v,
            reply_type="ADOPTED",
        )
    )
    v = qa["request"]["version"]
    assert qa["request"]["status"] == "ADOPTED"
    fw = ok(act(client, rid, "departments/RND_FW/acknowledge", user=FW_STAFF, version=v))
    fw = ok(
        act(
            client,
            rid,
            "departments/RND_FW/reply",
            user=FW_STAFF,
            version=fw["request"]["version"],
            reply_type="REJECTED",
            reason="Not a firmware matter",
        )
    )
    v = fw["request"]["version"]
    # One adopted, one rejected: ADOPTED (2.2.1 row 3); C/S must review the rejection.
    assert fw["request"]["status"] == "ADOPTED"
    assert ("CS_OWNER", "RND_FW", "REVIEW_REJECTION") in tasks(fw)
    assert ("DEPARTMENT", "QA_CLAIM", "RECORD_ACTION") in tasks(fw)
    replies = [(r["dept_code"], r["reply_type"], r["request_version"]) for r in fw["replies"]]
    assert ("RND_FW", "REJECTED", v - 1) in replies and all(rv >= 1 for _, _, rv in replies)
    # The adopted department records its action without waiting for the other.
    acted = ok(
        act(
            client,
            rid,
            "departments/QA_CLAIM/action",
            user=QA_STAFF,
            version=v,
            action="Changed gasket supplier lot inspection",
            action_scope="Lots from 2025-11",
            action_date="2025-11-20",
            action_evidence="QA report QA-2025-118",
        )
    )
    v = acted["request"]["version"]
    # 2.2.1 row 2: every non-rejected department acted → ACTION_TAKEN, even with an unreviewed
    # rejection; that rejection still blocks the follow-up plan below.
    assert (
        depts(acted)["QA_CLAIM"] == "ACTION_TAKEN" and acted["request"]["status"] == "ACTION_TAKEN"
    )
    assert len(acted["actions"]) == 1
    # Follow-up needs every department settled: the rejection is still unreviewed → 409.
    assert code(act(client, rid, "followup-plan", version=v, followup_plan="Watch 90 days")) == (
        409,
        "CS_REQUEST_INVALID_TRANSITION",
    )
    reviewed = ok(act(client, rid, "departments/RND_FW/review-rejection", version=v))
    v = reviewed["request"]["version"]
    assert reviewed["request"]["status"] == "ACTION_TAKEN"
    assert tasks(reviewed) == [("CS_OWNER", None, "PLAN_FOLLOWUP")]
    planned = ok(
        act(client, rid, "followup-plan", version=v, followup_plan="Watch new cases for 90 days")
    )
    v = planned["request"]["version"]
    assert planned["request"]["status"] == "FOLLOW_UP"
    # Department actions are frozen now, and completion is the agent's decision.
    assert code(act(client, rid, "departments/QA_CLAIM/acknowledge", user=QA_STAFF, version=v)) == (
        409,
        "CS_REQUEST_INVALID_TRANSITION",
    )
    assert (
        act(client, rid, "complete", user=CS_MANAGER, version=v, followup_result="x").status_code
        == 403
    )
    done = ok(act(client, rid, "complete", version=v, followup_result="No new case in 90 days"))
    assert done["request"]["status"] == "COMPLETED" and done["request"]["completed_by"] == str(
        AGENT.user_id
    )
    assert done["next_owners"] == []
    assert code(
        act(client, rid, "complete", version=done["request"]["version"], followup_result="again")
    ) == (
        409,
        "CS_REQUEST_COMPLETED",
    )
    audit = admin.execute(
        "SELECT action, changes->'action'->>'to' FROM cs.audit_events WHERE entity_id = %s "
        "ORDER BY version",
        (rid,),
    ).fetchall()
    assert {a[0] for a in audit[1:]} == {"transition"}
    assert [a[1] for a in audit][-3:] == ["review_rejection", "plan_followup", "complete"]


def test_resend_rules_supplement_new_evidence_and_reminders(client, admin, draft):
    rid = draft["id"]
    sent = delivered(client, draft)
    v = sent["request"]["version"]
    qa = ok(act(client, rid, "departments/QA_CLAIM/acknowledge", user=QA_STAFF, version=v))
    qa = ok(
        act(
            client,
            rid,
            "departments/QA_CLAIM/reply",
            user=QA_STAFF,
            version=qa["request"]["version"],
            reply_type="INFO_REQUESTED",
        )
    )
    v = qa["request"]["version"]
    assert qa["request"]["status"] == "INFO_REQUESTED"
    assert ("CS_OWNER", "QA_CLAIM", "SUPPLEMENT") in tasks(qa)
    # Supplement: the department goes back to SENT with a new version to answer.
    again = ok(
        act(client, rid, "departments/QA_CLAIM/resend", version=v, reason="Added lot numbers")
    )
    v = again["request"]["version"]
    qa_row = next(d for d in again["departments"] if d["dept_code"] == "QA_CLAIM")
    assert (qa_row["status"], qa_row["sent_version"]) == ("SENT", v)
    # Reminder before the deadline is refused; after it, allowed.
    assert code(act(client, rid, "departments/RND_FW/resend", version=v, reason="ping")) == (
        409,
        "CS_REQUEST_INVALID_TRANSITION",
    )
    admin.execute(
        "UPDATE cs.request_departments SET reply_due_at = now() - interval '1 day' "
        "WHERE request_id = %s AND dept_code = 'RND_FW'",
        (rid,),
    )
    reminded = ok(act(client, rid, "departments/RND_FW/resend", version=v, reason="Reply overdue"))
    v = reminded["request"]["version"]
    # Rejection → re-request needs evidence added after the rejection.
    fw = ok(act(client, rid, "departments/RND_FW/acknowledge", user=FW_STAFF, version=v))
    fw = ok(
        act(
            client,
            rid,
            "departments/RND_FW/reply",
            user=FW_STAFF,
            version=fw["request"]["version"],
            reply_type="REJECTED",
            reason="Not ours",
        )
    )
    v = fw["request"]["version"]
    assert code(
        act(client, rid, "departments/RND_FW/resend", version=v, reason="Please look again")
    ) == (
        422,
        "CS_EVIDENCE_REQUIRED",
    )
    ticket_id, case_id = add_case(admin)
    with_evidence = ok(
        client.post(
            f"{PATH}/{rid}/evidence",
            json={"version": v, "ticket_id": str(ticket_id), "case_id": str(case_id)},
        )
    )
    v = with_evidence["request"]["version"]
    re_requested = ok(
        act(client, rid, "departments/RND_FW/resend", version=v, reason="New country, new case")
    )
    assert depts(re_requested)["RND_FW"] == "SENT"


def test_all_rejected_and_reviewed_closes_the_round_until_a_new_one(client, admin, draft):
    rid = draft["id"]
    sent = delivered(client, draft)
    v = sent["request"]["version"]
    for staff, dept in ((QA_STAFF, "QA_CLAIM"), (FW_STAFF, "RND_FW")):
        d = ok(act(client, rid, f"departments/{dept}/acknowledge", user=staff, version=v))
        d = ok(
            act(
                client,
                rid,
                f"departments/{dept}/reply",
                user=staff,
                version=d["request"]["version"],
                reply_type="REJECTED",
                reason="Not ours",
            )
        )
        v = d["request"]["version"]
    assert d["request"]["status"] == "REJECTED"
    for dept in ("QA_CLAIM", "RND_FW"):
        d = ok(act(client, rid, f"departments/{dept}/review-rejection", version=v))
        v = d["request"]["version"]
    assert d["request"]["status"] == "REJECTED" and d["next_owners"] == []
    # Closed by rejection: no department-unit action; a new round (KAN-117) is the only way.
    assert code(act(client, rid, "departments/QA_CLAIM/resend", version=v, reason="x")) == (
        409,
        "CS_REQUEST_INVALID_TRANSITION",
    )


def test_service_cannot_rewrite_replies_or_actions(cs_database):
    runtime = make_conninfo(cs_database, user="cs_app", password="test-only")
    with psycopg.connect(runtime) as conn:
        for table in ("dept_replies", "request_actions"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"UPDATE cs.{table} SET dept_code = 'X' WHERE false")
            conn.rollback()


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["REJECTED", "REJECTED"], "REJECTED"),
        (["ACTION_TAKEN", "REJECTED"], "ACTION_TAKEN"),
        (["ACTION_TAKEN", "ACTION_TAKEN"], "ACTION_TAKEN"),
        (["ADOPTED", "INFO_REQUESTED", "REJECTED"], "ADOPTED"),
        (["INFO_REQUESTED", "ACKNOWLEDGED"], "INFO_REQUESTED"),
        (["ACKNOWLEDGED", "SENT"], "ACKNOWLEDGED"),
        (["SENT", "SENT"], "SENT"),
    ],
)
def test_summary_follows_the_table(states, expected):
    rows = [{"status": s, "rejection_reviewed_at": None} for s in states]
    assert summarize(rows) == expected


def test_followup_and_rejection_closure_rules():
    reviewed = {"status": "REJECTED", "rejection_reviewed_at": "x"}
    unreviewed = {"status": "REJECTED", "rejection_reviewed_at": None}
    acted = {"status": "ACTION_TAKEN", "rejection_reviewed_at": None}
    assert followup_allowed([acted, reviewed]) and not followup_allowed([acted, unreviewed])
    assert not followup_allowed([reviewed]) and not followup_allowed([])
    assert rejection_closed([reviewed, reviewed]) and not rejection_closed([reviewed, unreviewed])
