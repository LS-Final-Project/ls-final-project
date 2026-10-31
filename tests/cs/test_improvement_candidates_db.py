"""KAN-202 개선 후보 저장과 조회. 실제 마이그레이션과 cs_app 권한으로 돈다.

다른 검사의 기록이 같은 DB에 있으므로 각 검사는 자기만의 제품 코드를 쓴다.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.comparisons import examples
from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
CS_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})
QA_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"QA_CLAIM"})
QA_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="QA_CLAIM")
SALES = Principal(user_id=uuid4(), roles={"sales"})
PATH = "/api/v1/cs/improvement-candidates"
BASE = datetime(2026, 1, 10, tzinfo=UTC)
FAULT = "FLT-901"
SAME = examples.SAME
UNCONFIRMED = examples.UNCONFIRMED


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
def product():
    return f"KAN202-{uuid4().hex[:8]}"


def add_case(admin, product, days, customer, **extra):
    """문의와 해결 기록 하나. BASE에서 days일 뒤에 알려진다."""
    ticket_id, case_id = uuid4(), uuid4()
    at = BASE + timedelta(days=days)
    origin = extra.get("origin", "synthetic")
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan202', %s, %s, 'PROVIDED', %s, %s, 'EMAIL', "
        "'en', %s, %s, 'body')",
        (ticket_id, f"T-{ticket_id}", origin, at, at, customer, product),
    )
    admin.execute(
        "INSERT INTO cs.resolution_cases (id, ticket_id, source, source_instance, external_id, "
        "data_origin, derivation_kind, resolved_at, known_at, product_code, fault_id, "
        "fw_version_at_issue, symptom, root_cause, root_cause_type, action_taken, onsite_visit, "
        "resolved_by_dept) VALUES (%s, %s, 'test', 'kan202', %s, %s, 'PROVIDED', %s, %s, %s, %s, "
        "%s, 'symptom', 'cause', %s, 'action', false, %s)",
        (
            case_id,
            ticket_id,
            f"RC-{case_id}",
            origin,
            at,
            at,
            product,
            extra.get("fault", FAULT),
            extra.get("fw"),
            extra.get("cause", "HARDWARE"),
            extra.get("dept", "QA_CLAIM"),
        ),
    )
    return ticket_id, case_id


def three_customers(admin, product):
    return [add_case(admin, product, day, f"CUST-{day}") for day in (0, 10, 20)]


def as_of(days):
    return (BASE + timedelta(days=days)).isoformat()


def collect(client, days=30, user=AGENT):
    response = client.post(f"{PATH}/collect", json={"as_of": as_of(days)}, headers=header(user))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def header(user):
    return principal_header(user)


def group(client, product, days=30, fault=FAULT, **params):
    response = client.get(PATH, params={"as_of": as_of(days), **params})
    assert response.status_code == 200, response.text
    return [i for i in response.json()["data"]["items"] if i["product_code"] == product]


def requests_of(admin, product):
    return admin.execute(
        "SELECT id, origin, status, version, cs_owner, data_origin, derivation_kind, "
        "proposed_depts, candidate_reason_code, candidate_reached_at, candidate_rules_version, "
        "candidate_as_of, problem_statement, missing_info, not_claimed "
        "FROM cs.improvement_requests WHERE product_code = %s",
        (product,),
    ).fetchall()


def rows(admin, table, request_id):
    return admin.execute(
        f"SELECT count(*) FROM {table} WHERE request_id = %s", (request_id,)
    ).fetchone()[0]


def test_an_eligible_group_is_saved_as_a_draft_with_its_reason_and_evidence(client, admin, product):
    three_customers(admin, product)
    data = collect(client)
    assert data["created"] >= 1 and data["document_candidates"] == "NOT_CONNECTED"
    [row] = requests_of(admin, product)
    assert row[1:7] == ("SYSTEM_CANDIDATE", "DRAFT", 1, None, "synthetic", "SYSTEM")
    assert row[7] == ["QA_CLAIM"]
    assert row[8] == "REPEAT_90D_3_CUSTOMERS"
    assert row[9] == BASE + timedelta(days=20)
    assert row[10] == "improvement-candidates-v1"
    assert row[11] == BASE + timedelta(days=30)
    assert "3/3" in row[12] and row[13] == "사용 조건 미확인." and "단정하지" in row[14]
    assert rows(admin, "cs.request_evidence", row[0]) == 3
    assert rows(admin, "cs.improvement_request_versions", row[0]) == 1
    [item] = group(client, product)
    assert item["request_id"] == str(row[0]) and item["request_status"] == "DRAFT"
    detail = client.get(f"/api/v1/cs/improvement-requests/{row[0]}").json()["data"]["request"]
    assert detail["candidate_reason_code"] == "REPEAT_90D_3_CUSTOMERS"
    assert detail["candidate_rules_version"] == "improvement-candidates-v1"


def test_running_again_adds_nothing_and_every_run_is_audited(client, admin, product):
    three_customers(admin, product)
    first = collect(client)
    [row] = requests_of(admin, product)
    second = collect(client, 31)
    assert second["created"] == 0 and second["already_requested"] >= 1
    assert product not in str(second["request_ids"])
    assert second["run_id"] != first["run_id"]
    [again] = requests_of(admin, product)
    assert again[3] == row[3] == 1
    assert rows(admin, "cs.request_evidence", row[0]) == 3
    for run in (first, second):
        audit = admin.execute(
            "SELECT action, actor_user_id, changes FROM cs.audit_events "
            "WHERE entity_type = 'improvement_candidate_run' AND entity_id = %s",
            (run["run_id"],),
        ).fetchall()
        assert len(audit) == 1 and audit[0][0] == "create" and audit[0][1] == AGENT.user_id
        assert audit[0][2]["created"] == run["created"]
        assert audit[0][2]["rules_version"] == "improvement-candidates-v1"
    created = admin.execute(
        "SELECT action, actor_user_id, changes FROM cs.audit_events "
        "WHERE entity_type = 'improvement_request' AND entity_id = %s",
        (row[0],),
    ).fetchall()
    assert [(c[0], c[1]) for c in created] == [("create", AGENT.user_id)]
    assert created[0][2]["run_id"] == {"to": first["run_id"]}


def test_only_one_saved_candidate_can_exist_for_a_group(client, admin, product):
    three_customers(admin, product)
    collect(client)
    with pytest.raises(Exception, match="improvement_requests_candidate_group"):
        admin.execute(
            "INSERT INTO cs.improvement_requests (id, request_type, origin, data_origin, "
            "product_code, fault_id, problem_statement) "
            "VALUES (%s, 'FAULT', 'SYSTEM_CANDIDATE', 'synthetic', %s, %s, 'x')",
            (uuid4(), product, FAULT),
        )


def test_a_group_with_a_written_request_is_not_raised_again(client, admin, product):
    three_customers(admin, product)
    admin.execute(
        "INSERT INTO cs.improvement_requests (id, request_type, origin, data_origin, "
        "product_code, fault_id, problem_statement) "
        "VALUES (%s, 'FAULT', 'MANUAL', 'synthetic', %s, %s, 'written by hand')",
        (uuid4(), product, FAULT),
    )
    collect(client)
    assert [r[1] for r in requests_of(admin, product)] == ["MANUAL"]
    assert group(client, product)[0]["request_status"] == "DRAFT"


def test_records_known_after_as_of_change_nothing(client, admin, product):
    three_customers(admin, product)
    add_case(admin, product, 25, "CUST-LATER")
    [before] = group(client, product, days=15)
    assert before["outcome"] == "NOT_ELIGIBLE" and before["display_state"] == "NOT_ELIGIBLE"
    assert before["cases_total"] == 2 and "2/3" in before["reason"]
    assert before["reached_at"] is None
    collect(client, 15)
    assert requests_of(admin, product) == []
    [at_21] = group(client, product, days=21)
    assert at_21["cases_total"] == 3 and len(at_21["evidence"]) == 3
    collect(client, 21)
    [row] = requests_of(admin, product)
    assert rows(admin, "cs.request_evidence", row[0]) == 3


@pytest.mark.parametrize("call", ["read", "collect"])
def test_a_future_as_of_is_refused(client, call):
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    response = (
        client.get(PATH, params={"as_of": later})
        if call == "read"
        else client.post(f"{PATH}/collect", json={"as_of": later})
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CS_CANDIDATE_AS_OF_FUTURE"


@pytest.mark.parametrize("user", [QA_MANAGER, QA_STAFF, SALES])
def test_only_c_s_may_read_or_collect(client, user):
    assert client.get(PATH, headers=header(user)).status_code == 403
    assert client.post(f"{PATH}/collect", json={}, headers=header(user)).status_code == 403


def test_a_c_s_manager_may_collect_and_is_the_recorded_actor(client, admin, product):
    three_customers(admin, product)
    data = collect(client, user=CS_MANAGER)
    assert client.get(PATH, headers=header(CS_MANAGER)).status_code == 200
    assert (
        admin.execute(
            "SELECT actor_user_id FROM cs.audit_events WHERE entity_id = %s", (data["run_id"],)
        ).fetchone()[0]
        == CS_MANAGER.user_id
    )


def test_a_candidate_without_confirmed_conditions_does_not_read_as_confirmed(
    client, admin, product
):
    three_customers(admin, product)
    [item] = group(client, product)
    assert (item["outcome"], item["condition_status"]) == ("ELIGIBLE", "UNKNOWN")
    assert item["display_state"] == "CONDITION_UNCONFIRMED"


def compare(admin, product, days, *items, origin="synthetic"):
    """사용 조건 비교 판본 하나. BASE에서 days일 뒤에 알려진다."""
    comparison = uuid4()
    admin.execute(
        "INSERT INTO cs.condition_comparisons (id, product_code, fault_id, latest_version) "
        "VALUES (%s, %s, %s, 1)",
        (comparison, product, FAULT),
    )
    admin.execute(
        "INSERT INTO cs.condition_comparison_versions (comparison_id, version, data_origin, "
        "known_at) VALUES (%s, 1, %s, %s)",
        (comparison, origin, BASE + timedelta(days=days)),
    )
    for item in items:
        row = {"comparison_id": comparison, "version": 1, **item}
        admin.execute(
            f"INSERT INTO cs.condition_comparison_items ({', '.join(row)}) "
            f"VALUES ({', '.join(['%s'] * len(row))})",
            tuple(row.values()),
        )


def test_confirmed_conditions_make_a_confirmed_candidate(client, admin, product):
    three_customers(admin, product)
    compare(admin, product, 25, SAME)
    [item] = group(client, product)
    assert item["condition_status"] == "CONFIRMED"
    assert item["display_state"] == "CONFIRMED_CANDIDATE"
    collect(client)
    [row] = requests_of(admin, product)
    assert row[13] is None
    assert (
        admin.execute(
            "SELECT condition_status FROM cs.improvement_requests WHERE id = %s", (row[0],)
        ).fetchone()[0]
        == "CONFIRMED"
    )


def test_conditions_confirmed_after_as_of_are_not_used(client, admin, product):
    three_customers(admin, product)
    compare(admin, product, 25, SAME)
    [item] = group(client, product, days=24)
    assert item["condition_status"] == "UNKNOWN"


def test_conditions_confirmed_on_other_data_do_not_confirm_this_group(client, admin, product):
    three_customers(admin, product)
    compare(admin, product, 25, SAME, origin="demo")
    [item] = group(client, product)
    assert item["condition_status"] == "UNKNOWN"


def test_one_unconfirmed_condition_keeps_the_group_unconfirmed(client, admin, product):
    three_customers(admin, product)
    compare(admin, product, 25, SAME, UNCONFIRMED)
    [item] = group(client, product)
    assert item["display_state"] == "CONDITION_UNCONFIRMED"


def test_insufficient_evidence_does_not_read_as_a_result_and_is_not_saved(client, admin, product):
    add_case(admin, product, 0, "CUST-1", cause="FIRMWARE", fw=None)
    [item] = group(client, product, outcome="INSUFFICIENT_EVIDENCE")
    assert item["reason_code"] == "FW_VERSION_UNKNOWN"
    assert item["display_state"] == "INSUFFICIENT_EVIDENCE"
    assert group(client, product, outcome="ELIGIBLE") == []
    collect(client)
    assert requests_of(admin, product) == []


def test_the_firmware_rule_reads_the_release_table(client, admin):
    # ACB-AS-2000 1.3 was released 2025-06-16 (policy/firmware_history.csv).
    fault = f"FLT-{uuid4().int % 800 + 100}"
    add_case(admin, "ACB-AS-2000", 0, "CUST-FW", cause="FIRMWARE", fw="1.2", fault=fault)
    items = [i for i in group(client, "ACB-AS-2000") if i["fault_id"] == fault]
    assert [i["reason_code"] for i in items] == ["FIRMWARE_FIX_RELEASED"]
    assert items[0]["fw_fixed_version"] == "1.3" and items[0]["required_customers"] == 1


def test_data_origins_are_counted_apart(client, admin, product):
    add_case(admin, product, 0, "CUST-1")
    add_case(admin, product, 1, "CUST-2")
    add_case(admin, product, 2, "CUST-3", origin="demo")
    items = group(client, product)
    assert {(i["data_origin"], i["outcome"]) for i in items} == {
        ("synthetic", "NOT_ELIGIBLE"),
        ("demo", "NOT_ELIGIBLE"),
    }


def test_a_saved_candidate_keeps_its_origin_and_proposes_known_departments(client, admin, product):
    add_case(admin, product, 0, "CUST-1", origin="demo", dept="TS_LV")
    add_case(admin, product, 1, "CUST-2", origin="demo", dept="QA_CLAIM")
    add_case(admin, product, 2, "CUST-3", origin="demo", dept="GCS_L1")
    collect(client)
    [row] = requests_of(admin, product)
    assert row[5] == "demo" and row[7] == ["QA_CLAIM", "TS_LV"]


def test_a_record_the_rules_or_requests_cannot_hold_is_left_out(client, admin, product):
    add_case(admin, product, 0, "CUST-1", fault="OTHER-1")
    three_customers(admin, product)
    add_case(admin, product, 21, "CUST-9", cause="UNKNOWN_KIND")
    collect(client)
    [item] = group(client, product)
    assert item["fault_id"] == FAULT and item["cases_total"] == 4
    assert len(requests_of(admin, product)) == 1


def test_documents_are_marked_not_connected(client):
    response = client.get(PATH)
    assert response.json()["data"]["document_candidates"] == "NOT_CONNECTED"


def test_a_draft_from_a_candidate_goes_through_the_normal_review(client, admin, product):
    three_customers(admin, product)
    collect(client)
    [row] = requests_of(admin, product)
    path = f"/api/v1/cs/improvement-requests/{row[0]}/review"
    reviewed = client.post(path, json={"version": 1})
    assert reviewed.status_code == 200, reviewed.text
    request = reviewed.json()["data"]["request"]
    assert (request["status"], request["cs_owner"]) == ("REVIEWED", str(AGENT.user_id))
