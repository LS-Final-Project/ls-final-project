"""KAN-114 조건 비교 생성·판정·재검토를 실제 PostgreSQL에서: 판본, 감사, 전이 위반, 잠금."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import count
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
CS_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})
QA_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="QA_CLAIM")
PRODUCT = "SYN-PRD-02"
PAST = datetime(2026, 10, 1, tzinfo=UTC)
# 테스트마다 다른 고장 묶음. 제공 자료(FLT-001~097)와 겹치지 않는다.
FAULTS = count(800)


@pytest.fixture(scope="module")
def client(cs_database):
    runtime = make_conninfo(cs_database, user="cs_app", password="test-only")
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(runtime))
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as test_client:
        yield test_client


def ticket(admin, product=PRODUCT, fault="FLT-950", known=PAST):
    """합성 문의와 그 해결 사례. (ticket_id, case_id)."""
    ticket_id, case_id = uuid4(), uuid4()
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan114w', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', 'ko', 'CUST-0001', %s, '합성 문의')",
        (ticket_id, f"T-{ticket_id}", PAST, PAST, product),
    )
    admin.execute(
        "INSERT INTO cs.resolution_cases (id, ticket_id, source, source_instance, external_id, "
        "data_origin, derivation_kind, resolved_at, known_at, product_code, fault_id, symptom, "
        "root_cause, root_cause_type, action_taken, onsite_visit, resolved_by_dept) VALUES "
        "(%s, %s, 'test', 'kan114w', %s, 'synthetic', 'PROVIDED', %s, %s, %s, %s, '합성', "
        "'합성', 'WEAR_PART', '합성', false, 'FIELD_SVC')",
        (case_id, ticket_id, f"RC-{case_id}", known, known, product, fault),
    )
    return ticket_id, case_id


def fault_request(admin, fault, request_type="FAULT"):
    request_id = uuid4()
    keys = {"fault_id": fault} if request_type == "FAULT" else {"doc_signal": "NOT_FOUND"}
    columns = ", ".join(["id", "request_type", "origin", "data_origin", "product_code", *keys])
    admin.execute(
        f"INSERT INTO cs.improvement_requests ({columns}, problem_statement) "
        f"VALUES (%s, %s, 'MANUAL', 'synthetic', %s, %s, '합성 비교')",
        (request_id, request_type, PRODUCT, *keys.values()),
    )
    return f"/api/v1/cs/improvement-requests/{request_id}/condition-comparison"


def side(value, *ids):
    return {"masked_value": value, "missing_reason": None, "evidence_ids": list(ids)}


def condition(judgement, *ids, reason=None, code="application", **changes):
    body = {
        "kind": "CONDITION",
        "code": code,
        "judgement": judgement,
        "finding": "DIFFERS",
        "baseline": side("정격 부하 운전", ids[0]),
        "target": side("정격 상한 근처 운전", ids[-1]),
        "masked_summary": "정격 상한 근처 운전 사례에서 재발",
        "summary_missing_reason": None,
        "evidence_ids": list(ids),
        "reason": reason,
    }
    return body | changes


def evidence(evidence_id, ticket_id, case_id=None):
    source = "resolution_case" if case_id else "ticket"
    return {
        "evidence_id": evidence_id,
        "ticket_id": str(ticket_id),
        "case_id": str(case_id) if case_id else None,
        "evidence_location": {"source": source, "field": "root_cause" if case_id else "body"},
    }


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def code(response):
    return response.status_code, response.json()["error"]["code"]


def judge(client, path, version, items, new_evidence=(), user=AGENT):
    body = {"version": version, "items": items, "evidence": list(new_evidence)}
    return client.post(path + "/judgements", json=body, headers=principal_header(user))


def test_create_judge_confirm_and_review_make_independent_versions(client, admin):
    ticket_id, case_id = ticket(admin)
    path = fault_request(admin, "FLT-950")
    created = ok(client.post(path), 201)
    assert (created["version"], created["is_latest"], created["request_version"]) == (1, True, 1)
    assert len(created["conditions"]) == 8 and created["summary"]["unconfirmed"] == 8
    assert code(client.post(path)) == (409, "CS_COMPARISON_EXISTS")

    suspected = ok(
        judge(client, path, 1, [condition("SUSPECTED", "ev-t")], [evidence("ev-t", ticket_id)])
    )
    confirmed = ok(
        judge(
            client,
            path,
            2,
            [condition("CONFIRMED", "ev-t", "ev-c", reason="해결 사례로 확인")],
            [evidence("ev-c", ticket_id, case_id)],
        )
    )
    reviewed = ok(
        judge(client, path, 3, [condition("SUSPECTED", "ev-t", reason="재발 사례 재검토")])
    )
    assert [d["version"] for d in (suspected, confirmed, reviewed)] == [2, 3, 4]
    assert confirmed["evidence"][1]["derivation_kind"] == "PROVIDED"
    assert reviewed["latest_version"] == 4 and reviewed["computed_condition_status"] == "UNKNOWN"
    for version, judgement in (
        (1, "UNCONFIRMED"),
        (2, "SUSPECTED"),
        (3, "CONFIRMED"),
        (4, "SUSPECTED"),
    ):
        data = ok(client.get(path, params={"version": version}))
        [application] = [c for c in data["conditions"] if c["item_code"] == "application"]
        assert application["judgement"] == judgement, version
        assert data["is_latest"] is (version == 4)
        assert application["decided_by_name"] is None

    comparison_id = created["comparison_id"]
    audit = admin.execute(
        "SELECT action, version, actor_user_id, changes FROM cs.audit_events "
        "WHERE entity_type = 'condition_comparison' AND entity_id = %s ORDER BY version",
        (comparison_id,),
    ).fetchall()
    assert [(a[0], a[1], a[2]) for a in audit] == [
        ("create", 1, AGENT.user_id),
        ("update", 2, AGENT.user_id),
        ("update", 3, AGENT.user_id),
        ("update", 4, AGENT.user_id),
    ]
    assert audit[3][3] == {
        "CONDITION:application": {
            "from": "CONFIRMED",
            "to": "SUSPECTED",
            "reason": "재발 사례 재검토",
        }
    }
    stale = judge(client, path, 3, [condition("SUSPECTED", "ev-t", reason="늦은 쓰기")])
    assert code(stale) == (409, "CS_COMPARISON_VERSION_CONFLICT")
    status = admin.execute(
        "SELECT condition_status FROM cs.improvement_requests r JOIN cs.condition_comparison_versions v "
        "ON v.improvement_request_id = r.id WHERE v.comparison_id = %s AND v.version = 4",
        (comparison_id,),
    ).fetchone()
    assert status == ("UNKNOWN",)


@pytest.fixture
def group(client, admin):
    """새 고장 묶음 하나와 그 문의·사례, 판본 1까지 만든 비교."""
    fault = f"FLT-{next(FAULTS)}"
    ticket_id, case_id = ticket(admin, fault=fault)
    path = fault_request(admin, fault)
    ok(client.post(path), 201)
    return {"path": path, "ticket": ticket_id, "case": case_id, "fault": fault}


def test_writes_that_break_the_table_are_rejected(client, admin, group):
    path, t, c = group["path"], group["ticket"], group["case"]
    other_ticket, _ = ticket(admin, product="SYN-PRD-03", fault=group["fault"])
    _, other_fault_case = ticket(admin, fault="FLT-999")
    later_ticket, later_case = ticket(
        admin, fault=group["fault"], known=datetime.now(UTC) + timedelta(days=30)
    )
    case_ev, ticket_ev = evidence("ev-c", t, c), evidence("ev-t", t)
    cases = [
        ([condition("CONFIRMED", "ev-c")], [case_ev], (422, "CS_REASON_REQUIRED")),
        ([condition("CONFIRMED", "ev-t", reason="x")], [ticket_ev], (422, "CS_EVIDENCE_REQUIRED")),
        (
            [
                condition(
                    "CONFIRMED",
                    "ev-c",
                    reason="x",
                    target={
                        "masked_value": None,
                        "missing_reason": "NOT_PROVIDED",
                        "evidence_ids": [],
                    },
                )
            ],
            [case_ev],
            (422, "CS_VALUE_NOT_PROVIDED"),
        ),
        ([condition("SUSPECTED", "ev-x")], [ticket_ev], (422, "CS_EVIDENCE_INVALID")),
        (
            [condition("SUSPECTED", "ev-o")],
            [evidence("ev-o", other_ticket)],
            (422, "CS_EVIDENCE_INVALID"),
        ),
        (
            [condition("SUSPECTED", "ev-f")],
            [evidence("ev-f", t, other_fault_case)],
            (422, "CS_EVIDENCE_INVALID"),
        ),
        (
            [condition("SUSPECTED", "ev-l")],
            [evidence("ev-l", later_ticket, later_case)],
            (422, "CS_EVIDENCE_REQUIRED"),
        ),
    ]
    for items, new_evidence, expected in cases:
        assert code(judge(client, path, 1, items, new_evidence)) == expected, expected
    # 아직 알려지지 않은 사례는 미확인(NOT_YET_KNOWN)의 근거로만 남는다.
    waiting = condition("UNCONFIRMED", "ev-l", finding=None, unconfirmed_reason="NOT_YET_KNOWN")
    data = ok(judge(client, path, 1, [waiting], [evidence("ev-l", later_ticket, later_case)]))
    assert data["version"] == 2
    reused = judge(client, path, 2, [condition("SUSPECTED", "ev-t")], [evidence("ev-l", t)])
    assert code(reused) == (422, "CS_EVIDENCE_INVALID")
    invalid = judge(client, path, 2, [condition("SUSPECTED", "ev-t", finding=None)], [ticket_ev])
    assert invalid.status_code == 422


def test_only_c_s_agents_write(client, admin, group):
    path = group["path"]
    request_id = path.split("/")[5]
    admin.execute(
        "INSERT INTO cs.request_departments (request_id, round, dept_code, status) "
        "VALUES (%s, 1, 'QA_CLAIM', 'SENT')",
        (request_id,),
    )
    item = [condition("SUSPECTED", "ev-t")]
    ticket_ev = [evidence("ev-t", group["ticket"])]
    for user in (CS_MANAGER, QA_STAFF):
        assert judge(client, path, 1, item, ticket_ev, user).status_code == 403
        assert client.post(path, headers=principal_header(user)).status_code == 403
    document = fault_request(admin, None, "DOCUMENT")
    assert code(client.post(document)) == (404, "CS_COMPARISON_NOT_FOUND")
    assert code(judge(client, document, 1, item, ticket_ev)) == (404, "CS_COMPARISON_NOT_FOUND")


def test_a_write_waits_for_the_lock_and_then_sees_the_newer_version(client, cs_database, group):
    """다른 트랜잭션이 비교 행을 잠근 동안 쓰기는 기다리고, 풀린 뒤 바뀐 판본이면 409다."""
    path = group["path"]
    body = [condition("SUSPECTED", "ev-t")], [evidence("ev-t", group["ticket"])]
    with psycopg.connect(cs_database) as holder:
        holder.execute(
            "SELECT 1 FROM cs.condition_comparisons WHERE fault_id = %s FOR UPDATE",
            (group["fault"],),
        )
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(judge, client, path, 1, *body)
            time.sleep(1)
            assert not pending.done()
            # 잠근 쪽이 먼저 판본을 올린 것처럼 최신 번호를 바꾸고 커밋한다.
            holder.execute(
                "UPDATE cs.condition_comparisons SET latest_version = 2 WHERE fault_id = %s",
                (group["fault"],),
            )
            holder.commit()
            response = pending.result(timeout=30)
    assert code(response) == (409, "CS_COMPARISON_VERSION_CONFLICT")


def test_two_writers_on_the_same_version_make_one_new_version(client, admin, group):
    path = group["path"]
    body = [condition("SUSPECTED", "ev-t")], [evidence("ev-t", group["ticket"])]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: judge(client, path, 1, *body), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    versions = admin.execute(
        "SELECT latest_version FROM cs.condition_comparisons WHERE fault_id = %s",
        (group["fault"],),
    ).fetchone()
    assert versions == (2,)
