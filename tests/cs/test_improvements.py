"""KAN-112 next owners per permissions.md 2.2.1, and checks that need no database."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.cs.improvements.next_owner import next_owners
from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

OWNER = uuid4()


def request(status: str, depts=("QA_CLAIM",)) -> dict:
    return {"status": status, "cs_owner": OWNER, "proposed_depts": list(depts)}


def dept(code: str, status: str, reviewed: bool = False) -> dict:
    return {"dept_code": code, "status": status, "rejection_reviewed_at": "x" if reviewed else None}


def tasks(owners) -> list[tuple[str, str | None, str]]:
    return [(o.actor, o.dept_code, o.task) for o in owners]


@pytest.mark.parametrize(
    ("status", "depts", "expected"),
    [
        ("DRAFT", ("QA_CLAIM",), [("CS_OWNER", None, "REVIEW")]),
        ("REVIEWED", ("QA_CLAIM",), [("CS_OWNER", None, "SEND")]),
        ("REVIEWED", (), [("CS_OWNER", None, "CONFIRM_RECEIVER")]),
        ("FOLLOW_UP", ("QA_CLAIM",), [("CS_OWNER", None, "RECORD_FOLLOWUP")]),
        ("COMPLETED", ("QA_CLAIM",), []),
    ],
)
def test_tasks_before_and_after_delivery(status, depts, expected):
    assert tasks(next_owners(request(status, depts), [])) == expected


def test_mixed_department_replies_list_every_pending_task():
    # permissions.md 2.2.1 example: quality adopted, design rejected, logistics asks for data.
    rows = [dept("TS_TRF", "ADOPTED"), dept("QA_CLAIM", "REJECTED"), dept("SCM", "INFO_REQUESTED")]
    owners = next_owners(request("ADOPTED"), rows)
    assert tasks(owners) == [
        ("CS_OWNER", "QA_CLAIM", "REVIEW_REJECTION"),
        ("CS_OWNER", "SCM", "SUPPLEMENT"),
        ("DEPARTMENT", "TS_TRF", "RECORD_ACTION"),
    ]
    assert {o.user_id for o in owners if o.actor == "CS_OWNER"} == {OWNER}


def test_follow_up_opens_only_when_every_department_settled():
    unreviewed = [dept("TS_TRF", "ACTION_TAKEN"), dept("QA_CLAIM", "REJECTED")]
    assert tasks(next_owners(request("ACTION_TAKEN"), unreviewed)) == [
        ("CS_OWNER", "QA_CLAIM", "REVIEW_REJECTION")
    ]
    settled = [dept("TS_TRF", "ACTION_TAKEN"), dept("QA_CLAIM", "REJECTED", reviewed=True)]
    assert tasks(next_owners(request("ACTION_TAKEN"), settled)) == [
        ("CS_OWNER", None, "PLAN_FOLLOWUP")
    ]
    # All rejected and reviewed: no action, so no follow-up.
    rejected = [dept("QA_CLAIM", "REJECTED", reviewed=True)]
    assert tasks(next_owners(request("REJECTED"), rejected)) == []


def test_waiting_departments_are_department_tasks():
    rows = [dept("QA_CLAIM", "SENT"), dept("SCM", "ACKNOWLEDGED")]
    assert tasks(next_owners(request("SENT"), rows)) == [
        ("DEPARTMENT", "QA_CLAIM", "ACKNOWLEDGE"),
        ("DEPARTMENT", "SCM", "REPLY"),
    ]


AGENT = principal_header(Principal(user_id=uuid4(), roles={"cs_agent"}))
BASE = {
    "request_type": "FAULT",
    "product_code": "TRF-OIL-1000",
    "fault_id": "FLT-067",
    "problem_statement": "Investigate gasket leaks.",
    "proposed_depts": ["QA_CLAIM"],
}


@pytest.mark.parametrize(
    "body",
    [
        {**BASE, "proposed_depts": []},
        {**BASE, "fault_id": None},
        {**BASE, "doc_signal": "NOT_FOUND"},
        {**BASE, "fault_id": "FLT-67"},
        {**BASE, "proposed_depts": ["GCS_L1"]},
        {**BASE, "proposed_depts": ["QA_CLAIM", "QA_CLAIM"]},
        {**BASE, "request_type": "DOCUMENT"},
        {**BASE, "problem_statement": ""},
        {**BASE, "status": "COMPLETED"},
        {**BASE, "evidence": [{"ticket_id": "00000000-0000-4000-8000-000000000001"}] * 2},
    ],
)
def test_invalid_requests_are_rejected_before_any_lookup(body):
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as client:
        assert (
            client.post("/api/v1/cs/improvement-requests", json=body, headers=AGENT).status_code
            == 422
        )


@pytest.mark.parametrize("roles", [{"sales"}, {"dept_staff"}])
def test_only_c_s_agents_write(roles):
    user = Principal(
        user_id=uuid4(), roles=roles, dept="QA_CLAIM" if "dept_staff" in roles else None
    )
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as client:
        response = client.post(
            "/api/v1/cs/improvement-requests", json=BASE, headers=principal_header(user)
        )
        assert response.status_code == 403


def test_closed_in_production():
    with TestClient(create_app(Settings(_env_file=None, environment="production"))) as client:
        assert client.get("/api/v1/cs/improvement-requests", headers=AGENT).status_code == 404
