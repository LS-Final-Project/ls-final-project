"""KAN-112 improvement requests on a real PostgreSQL: versions, evidence, scope, independence.

The gasket leak group (C/S proposal 4.5): FLT-067 on TRF-OIL-1000 across several customers.
"""

from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
OTHER_AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
CS_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})
QA_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"QA_CLAIM"})
QA_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="QA_CLAIM")
LV_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_LV")
PRODUCT = "TRF-OIL-1000"
PATH = "/api/v1/cs/improvement-requests"


@pytest.fixture(scope="module")
def runtime_dsn(cs_database):
    return make_conninfo(cs_database, user="cs_app", password="test-only")


@pytest.fixture(scope="module")
def client(runtime_dsn):
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(runtime_dsn))
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as test_client:
        yield test_client


def add_ticket(admin, product=PRODUCT, fault="FLT-067"):
    """A ticket and its known resolution case. Returns (ticket_id, case_id)."""
    ticket_id, case_id = uuid4(), uuid4()
    at = datetime(2025, 10, 5, tzinfo=UTC)
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan112', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', 'en', %s, %s, 'Oil leakage at gasket.')",
        (ticket_id, f"T-{ticket_id}", at, at, f"CUST-{uuid4().hex[:6]}", product),
    )
    admin.execute(
        "INSERT INTO cs.resolution_cases (id, ticket_id, source, source_instance, external_id, "
        "data_origin, derivation_kind, resolved_at, known_at, product_code, fault_id, symptom, "
        "root_cause, root_cause_type, action_taken, onsite_visit, resolved_by_dept) VALUES "
        "(%s, %s, 'test', 'kan112', %s, 'synthetic', 'PROVIDED', %s, %s, %s, %s, 'Oil leak', "
        "'Gasket damaged in transport', 'WEAR_PART', 'Replace gasket', true, 'FIELD_SVC')",
        (case_id, ticket_id, f"RC-{case_id}", at, at, product, fault),
    )
    return ticket_id, case_id


def fault_request(evidence, **changes) -> dict:
    return {
        "request_type": "FAULT",
        "product_code": PRODUCT,
        "fault_id": "FLT-067",
        "problem_statement": "Investigate gasket leaks reported by three customers in 40 days.",
        "proposed_depts": ["QA_CLAIM"],
        "evidence": [{"ticket_id": str(t), "case_id": str(c)} for t, c in evidence],
        **changes,
    }


def ok(response) -> dict:
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


def code(response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def as_(user):
    return principal_header(user)


def test_a_fault_request_is_versioned_with_its_evidence(client, admin):
    evidence = [add_ticket(admin) for _ in range(3)]
    data = ok(client.post(PATH, json=fault_request(evidence)))
    request = data["request"]
    assert (request["status"], request["version"], request["origin"]) == ("DRAFT", 1, "MANUAL")
    assert request["cs_owner"] == str(AGENT.user_id)
    assert len(data["evidence"]) == 3
    assert data["next_owners"] == [
        {"actor": "CS_OWNER", "user_id": str(AGENT.user_id), "dept_code": None, "task": "REVIEW"}
    ]
    path = f"{PATH}/{request['id']}"

    stale = client.patch(path, json={"version": 2, "review_task": "x"})
    assert code(stale) == (409, "CS_REQUEST_VERSION_CONFLICT")
    same = ok(client.patch(path, json={"version": 1, "proposed_depts": ["QA_CLAIM"]}))
    assert same["request"]["version"] == 1
    changed = ok(client.patch(path, json={"version": 1, "proposed_depts": ["QA_CLAIM", "SCM"]}))
    assert changed["request"]["version"] == 2
    cleared = client.patch(path, json={"version": 2, "proposed_depts": []})
    assert code(cleared) == (422, "CS_DEPARTMENT_REQUIRED")

    # Evidence added together has no fixed order, so find the link by its ticket.
    first_link = next(e["id"] for e in data["evidence"] if e["ticket_id"] == str(evidence[0][0]))
    removed = ok(
        client.post(
            f"{path}/evidence/{first_link}/remove",
            json={"version": 2, "reason": "Different usage condition"},
        )
    )
    assert removed["request"]["version"] == 3
    assert [e["removed_at"] is None for e in removed["evidence"]] == [True, True, False]
    assert removed["evidence"][2]["ticket_id"] == str(evidence[0][0])
    again = client.post(f"{path}/evidence", json={"version": 3, **removed_link(evidence[1])})
    assert code(again) == (409, "CS_EVIDENCE_EXISTS")
    readded = ok(client.post(f"{path}/evidence", json={"version": 3, **removed_link(evidence[0])}))
    assert readded["request"]["version"] == 4

    versions = ok(client.get(f"{path}/versions"))["items"]
    assert [v["version"] for v in versions] == [1, 2, 3, 4]
    assert len(versions[0]["content"]["evidence"]) == 3
    assert versions[1]["content"]["proposed_depts"] == ["QA_CLAIM", "SCM"]
    assert len(versions[2]["content"]["evidence"]) == 2
    assert versions[0]["content"]["proposed_depts"] == ["QA_CLAIM"]
    audit = admin.execute(
        "SELECT action, version FROM cs.audit_events WHERE entity_type = 'improvement_request' "
        "AND entity_id = %s ORDER BY version",
        (request["id"],),
    ).fetchall()
    assert audit == [("create", 1), ("update", 2), ("update", 3), ("update", 4)]


def removed_link(pair) -> dict:
    return {"ticket_id": str(pair[0]), "case_id": str(pair[1])}


def test_evidence_must_match_the_group(client, admin):
    other_product = add_ticket(admin, product="TRF-DRY-630")
    other_fault = add_ticket(admin, fault="FLT-050")
    good = add_ticket(admin)
    cases = [
        (other_product, "another product"),
        (other_fault, "another fault"),
        ((good[0], other_fault[1]), "of another ticket"),
        ((good[0], uuid4()), "unknown"),
    ]
    for pair, message in cases:
        refused = client.post(PATH, json=fault_request([pair]))
        assert code(refused) == (422, "CS_EVIDENCE_INVALID"), message
        assert message in refused.json()["error"]["message"]
    no_case = client.post(
        PATH, json={**fault_request([]), "evidence": [{"ticket_id": str(good[0])}]}
    )
    assert code(no_case) == (422, "CS_EVIDENCE_INVALID")
    unknown = client.post(PATH, json=fault_request([], product_code="NO-SUCH-PRODUCT"))
    assert code(unknown) == (422, "CS_UNKNOWN_PRODUCT")
    # A rejected request leaves nothing behind.
    count = admin.execute(
        "SELECT count(*) FROM cs.request_evidence WHERE ticket_id = %s", (good[0],)
    ).fetchone()[0]
    assert count == 0


def test_a_document_request_may_wait_for_its_receiver(client, admin):
    ticket, case = add_ticket(admin)
    body = {
        "request_type": "DOCUMENT",
        "product_code": PRODUCT,
        "doc_signal": "NOT_FOUND",
        "doc_id": "DS-TRF-OIL-1000",
        "problem_statement": "Customers cannot find the vector group in the catalog.",
        "evidence": [{"ticket_id": str(ticket)}],
    }
    data = ok(client.post(PATH, json=body))
    assert data["request"]["receiver_unconfirmed"] is True
    with_case = {**body, "evidence": [{"ticket_id": str(ticket), "case_id": str(case)}]}
    assert code(client.post(PATH, json=with_case)) == (422, "CS_EVIDENCE_INVALID")
    # Once reviewed, the next task is confirming the receiver, not sending.
    admin.execute(
        "UPDATE cs.improvement_requests SET status = 'REVIEWED' WHERE id = %s",
        (data["request"]["id"],),
    )
    reviewed = ok(client.get(f"{PATH}/{data['request']['id']}"))
    assert [n["task"] for n in reviewed["next_owners"]] == ["CONFIRM_RECEIVER"]


def test_ticket_closing_and_request_completion_are_independent(client, admin):
    ticket, case = add_ticket(admin)
    data = ok(client.post(PATH, json=fault_request([(ticket, case)])))
    request_id = data["request"]["id"]
    # Close the ticket: the request does not move.
    admin.execute(
        "INSERT INTO cs.ticket_workflows (ticket_id, state, closed_at, closed_by) "
        "VALUES (%s, 'CLOSED', now(), %s)",
        (ticket, AGENT.user_id),
    )
    after = ok(client.get(f"{PATH}/{request_id}"))["request"]
    assert (after["status"], after["version"]) == ("DRAFT", 1)
    # A closed ticket can still be evidence for another request.
    ok(client.post(PATH, json=fault_request([(ticket, case)])))
    # Completing the request does not touch the ticket, and blocks further edits.
    admin.execute(
        "UPDATE cs.improvement_requests SET status = 'COMPLETED' WHERE id = %s", (request_id,)
    )
    state = admin.execute(
        "SELECT state FROM cs.ticket_workflows WHERE ticket_id = %s", (ticket,)
    ).fetchone()
    assert state == ("CLOSED",)
    edit = client.patch(f"{PATH}/{request_id}", json={"version": 1, "review_task": "x"})
    assert code(edit) == (409, "CS_REQUEST_COMPLETED")
    assert ok(client.get(f"{PATH}/{request_id}"))["next_owners"] == []


def test_department_scope_and_writers(client, admin):
    data = ok(client.post(PATH, json=fault_request([add_ticket(admin)])))
    request_id = data["request"]["id"]
    path = f"{PATH}/{request_id}"

    def listed(user):
        return {i["id"] for i in ok(client.get(PATH, headers=as_(user)))["items"]}

    # Not sent yet: the receiving department's staff cannot see it; its manager can.
    assert client.get(path, headers=as_(QA_STAFF)).status_code == 404
    assert request_id not in listed(QA_STAFF)
    assert request_id in listed(QA_MANAGER)
    assert client.get(path, headers=as_(LV_STAFF)).status_code == 404
    admin.execute(
        "INSERT INTO cs.request_departments (request_id, round, dept_code, status) "
        "VALUES (%s, 1, 'QA_CLAIM', 'SENT')",
        (request_id,),
    )
    admin.execute(
        "UPDATE cs.improvement_requests SET status = 'SENT', current_round = 1 WHERE id = %s",
        (request_id,),
    )
    sent = ok(client.get(path, headers=as_(QA_STAFF)))
    assert [(n["actor"], n["dept_code"], n["task"]) for n in sent["next_owners"]] == [
        ("DEPARTMENT", "QA_CLAIM", "ACKNOWLEDGE")
    ]
    assert (
        client.patch(
            path, json={"version": 1, "review_task": "x"}, headers=as_(QA_STAFF)
        ).status_code
        == 403
    )

    owner = {"version": 1, "owner_user_id": str(OTHER_AGENT.user_id)}
    for user in (AGENT, QA_MANAGER):
        assert client.put(f"{path}/owner", json=owner, headers=as_(user)).status_code == 403
    moved = ok(client.put(f"{path}/owner", json=owner, headers=as_(CS_MANAGER)))
    assert (moved["request"]["cs_owner"], moved["request"]["version"]) == (
        str(OTHER_AGENT.user_id),
        2,
    )


def test_case_comparison_shows_active_evidence_within_the_viewers_scope(client, admin):
    shared, shared_case = add_ticket(admin)
    private, private_case = add_ticket(admin)
    removed, removed_case = add_ticket(admin)
    body = fault_request([(shared, shared_case), (private, private_case), (removed, removed_case)])
    created = ok(client.post(PATH, json=body))["request"]
    request_id, version = created["id"], created["version"]
    evidence = ok(client.get(f"{PATH}/{request_id}"))["evidence"]
    gone = next(e["id"] for e in evidence if e["ticket_id"] == str(removed))
    out = client.post(
        f"{PATH}/{request_id}/evidence/{gone}/remove", json={"version": version, "reason": "other"}
    )
    assert out.status_code == 200, out.text
    url = f"{PATH}/{request_id}/case-comparison"

    data = ok(client.get(url))
    assert data["request_id"] == request_id and data["hidden_case_count"] == 0
    assert {row["ticket_id"] for row in data["items"]} == {str(shared), str(private)}
    row = next(r for r in data["items"] if r["ticket_id"] == str(shared))
    assert (row["case_id"], row["product_code"]) == (str(shared_case), PRODUCT)
    assert (row["symptom"], row["root_cause_type"], row["resolved_by_dept"]) == (
        "Oil leak",
        "WEAR_PART",
        "FIELD_SVC",
    )
    assert (row["root_cause"], row["action_taken"]) == (
        "Gasket damaged in transport",
        "Replace gasket",
    )
    assert row["customer_external_id"].startswith("CUST-") and row["known_at"]
    assert client.get(url, headers=as_(CS_MANAGER)).status_code == 200

    # 요청을 받은 부서도 이관받지 않은 문의의 해결 기록은 볼 수 없다.
    admin.execute(
        "INSERT INTO cs.request_departments (request_id, round, dept_code, status) "
        "VALUES (%s, 1, 'QA_CLAIM', 'SENT')",
        (request_id,),
    )
    admin.execute(
        "INSERT INTO cs.ticket_transfers (id, ticket_id, round, dept_code, status, reason, "
        "transferred_by) VALUES (%s, %s, 1, 'QA_CLAIM', 'OPEN', 'test', %s)",
        (uuid4(), shared, AGENT.user_id),
    )
    for user in (QA_STAFF, QA_MANAGER):
        scoped = ok(client.get(url, headers=as_(user)))
        assert [r["ticket_id"] for r in scoped["items"]] == [str(shared)]
        assert scoped["hidden_case_count"] == 1
        assert str(private) not in str(scoped) and str(private_case) not in str(scoped)
    assert code(client.get(url, headers=as_(LV_STAFF))) == (404, "CS_REQUEST_NOT_FOUND")
    assert code(client.get(f"{PATH}/{uuid4()}/case-comparison")) == (404, "CS_REQUEST_NOT_FOUND")


def test_case_comparison_keeps_a_ticket_without_a_resolution_record(client, admin):
    ticket, _ = add_ticket(admin)
    created = ok(
        client.post(
            PATH,
            json={
                "request_type": "DOCUMENT",
                "product_code": PRODUCT,
                "doc_signal": "NOT_FOUND",
                "problem_statement": "문서 확인",
                "evidence": [{"ticket_id": str(ticket)}],
            },
        )
    )["request"]
    [row] = ok(client.get(f"{PATH}/{created['id']}/case-comparison"))["items"]
    assert row["ticket_id"] == str(ticket)
    assert (row["case_id"], row["symptom"], row["known_at"]) == (None, None, None)


def test_the_list_filters_by_origin(client, admin):
    ticket, case = add_ticket(admin, fault="FLT-071")
    body = fault_request([(ticket, case)], fault_id="FLT-071")
    manual = ok(client.post(PATH, json=body))["request"]["id"]
    system = uuid4()
    admin.execute(
        "INSERT INTO cs.improvement_requests (id, request_type, origin, data_origin, "
        "product_code, fault_id, problem_statement) "
        "VALUES (%s, 'FAULT', 'SYSTEM_CANDIDATE', 'synthetic', %s, 'FLT-071', 'x')",
        (system, f"KAN202-{system.hex[:8]}"),
    )
    listed = ok(client.get(PATH, params={"origin": "SYSTEM_CANDIDATE"}))["items"]
    assert str(system) in {i["id"] for i in listed} and manual not in {i["id"] for i in listed}
    assert {i["origin"] for i in listed} == {"SYSTEM_CANDIDATE"}
    assert manual in {i["id"] for i in ok(client.get(PATH, params={"origin": "MANUAL"}))["items"]}
    assert client.get(PATH, params={"origin": "OTHER"}).status_code == 422


def test_service_cannot_rewrite_history(runtime_dsn, client, admin):
    data = ok(client.post(PATH, json=fault_request([add_ticket(admin)])))
    request_id = data["request"]["id"]
    for statement in (
        "DELETE FROM cs.improvement_requests WHERE id = %s",
        "UPDATE cs.improvement_request_versions SET content = '{}' WHERE request_id = %s",
        "DELETE FROM cs.request_evidence WHERE request_id = %s",
    ):
        with (
            psycopg.connect(runtime_dsn) as conn,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            conn.execute(statement, (request_id,))


def test_the_list_filters_by_status_type_and_department(client, admin):
    """상태(쉼표로 여러 개)·유형·부서로 좁힌다. 부서는 보낸 적 있거나 제안된 부서다(KAN-131 화면 요청)."""
    fault = ok(client.post(PATH, json=fault_request([add_ticket(admin)])))["request"]["id"]
    ticket, _ = add_ticket(admin)
    document = ok(
        client.post(
            PATH,
            json={
                "request_type": "DOCUMENT",
                "product_code": PRODUCT,
                "doc_signal": "NOT_FOUND",
                "doc_id": "DS-TRF-OIL-1000",
                "problem_statement": "Customers cannot find the vector group in the catalog.",
                "evidence": [{"ticket_id": str(ticket)}],
            },
        )
    )["request"]["id"]
    admin.execute(
        "UPDATE cs.improvement_requests SET status = 'REVIEWED' WHERE id = %s", (document,)
    )

    def listed(**params):
        items = ok(client.get(PATH, params=params))["items"]
        return items, {item["id"] for item in items}

    items, ids = listed(status="REVIEWED")
    assert document in ids and fault not in ids
    assert {item["status"] for item in items} == {"REVIEWED"}
    _, ids = listed(status="DRAFT,REVIEWED")
    assert {fault, document} <= ids
    items, ids = listed(request_type="FAULT")
    assert fault in ids and document not in ids
    assert {item["request_type"] for item in items} == {"FAULT"}
    _, ids = listed(dept="QA_CLAIM")
    assert fault in ids and document not in ids
    _, ids = listed(dept="QA_CLAIM", status="REVIEWED")
    assert fault not in ids and document not in ids
    assert code(client.get(PATH, params={"status": "DRAFT,"})) == (400, "CS_INVALID_FILTER")
    assert code(client.get(PATH, params={"status": "OPEN"})) == (400, "CS_INVALID_FILTER")
    assert client.get(PATH, params={"dept": "GCS_L1"}).status_code == 422
    # 부서 사용자는 필터를 줘도 자기 부서로 보낸 요청만 본다. 제안만 된 초안은 보이지 않는다.
    staff_ids = {
        item["id"]
        for item in ok(client.get(PATH, params={"dept": "QA_CLAIM"}, headers=as_(QA_STAFF)))[
            "items"
        ]
    }
    assert fault not in staff_ids
