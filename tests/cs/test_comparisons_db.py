"""KAN-114 조건 비교 조회를 실제 PostgreSQL에서: 합성 예시 5종이 실제 응답, 판본, 범위, 제약."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.comparisons import examples
from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
QA_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="QA_CLAIM")
SALES = Principal(user_id=uuid4(), roles={"sales"})
GROUP = "/api/v1/cs/condition-comparisons"
NO_REQUEST = "FLT-905"
DOCUMENT_REQUEST = uuid4()
AT = datetime(2026, 10, 20, tzinfo=UTC)


def insert(admin, table, row):
    values = {k: json.dumps(v) if isinstance(v, dict) else v for k, v in row.items()}
    admin.execute(
        f"INSERT INTO cs.{table} ({', '.join(values)}) VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


def add_ticket(admin, ticket_id):
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan114', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', 'ko', 'CUST-0001', %s, '합성 문의')",
        (ticket_id, f"T-{ticket_id}", AT, AT, examples.PRODUCT),
    )


def add_request(admin, row, fault_id, request_type="FAULT"):
    fault = {"fault_id": fault_id} if request_type == "FAULT" else {"doc_signal": "NOT_FOUND"}
    insert(
        admin,
        "improvement_requests",
        {
            "id": row["id"],
            "request_type": request_type,
            "origin": "MANUAL",
            "data_origin": "synthetic",
            "product_code": examples.PRODUCT,
            "problem_statement": "합성 비교 묶음",
            "version": row["version"],
            "condition_summary": row["condition_summary"],
            "fw_fixed_version": row["fw_fixed_version"],
            "fw_released_on": row["fw_released_on"],
        }
        | fault,
    )


def add_version(admin, comparison_id, version, items, evidence):
    insert(admin, "condition_comparison_versions", {"comparison_id": comparison_id, **version})
    for row in evidence:
        insert(
            admin,
            "condition_comparison_evidence",
            {"comparison_id": comparison_id, "version": version["version"], **row},
        )
    for row in items:
        insert(
            admin,
            "condition_comparison_items",
            {"comparison_id": comparison_id, "version": version["version"], **row},
        )


@pytest.fixture(scope="module")
def seeded(cs_database):
    """예시 행을 그대로 저장한다. QA_CLAIM은 '가림' 요청서와 보이는 문의 하나만 받는다."""
    with psycopg.connect(cs_database, autocommit=True) as admin:
        add_ticket(admin, examples.VISIBLE_TICKET)
        add_ticket(admin, examples.HIDDEN_TICKET)
        admin.execute(
            "INSERT INTO cs.resolution_cases (id, ticket_id, source, source_instance, "
            "external_id, data_origin, derivation_kind, resolved_at, known_at, product_code, "
            "fault_id, symptom, root_cause, root_cause_type, action_taken, onsite_visit, "
            "resolved_by_dept) VALUES (%s, %s, 'test', 'kan114', 'RC-KAN114', 'synthetic', "
            "'SYSTEM', %s, %s, %s, 'FLT-904', '합성', '합성', 'WEAR_PART', '합성', false, "
            "'FIELD_SVC')",
            (examples.CASE, examples.VISIBLE_TICKET, AT, AT, examples.PRODUCT),
        )
        for _, comparison, request, versions, _, _ in examples.SEEDS.values():
            add_request(admin, request, comparison["fault_id"])
            insert(admin, "condition_comparisons", comparison)
            for version, items, evidence in versions:
                add_version(admin, comparison["id"], version, items, evidence)
        hidden_request = examples.SEEDS["hidden"][2]["id"]
        admin.execute(
            "INSERT INTO cs.request_departments (request_id, round, dept_code, status) "
            "VALUES (%s, 1, 'QA_CLAIM', 'SENT')",
            (hidden_request,),
        )
        admin.execute(
            "INSERT INTO cs.ticket_transfers (id, ticket_id, round, dept_code, status, reason, "
            "transferred_by) VALUES (%s, %s, 1, 'QA_CLAIM', 'OPEN', '합성', %s)",
            (uuid4(), examples.VISIBLE_TICKET, AGENT.user_id),
        )
        # 요청서 없는 묶음과 문서 요청서.
        orphan = examples.comparison(5)
        insert(admin, "condition_comparisons", orphan)
        add_version(
            admin,
            orphan["id"],
            examples.version(1, examples.request(5))
            | {"improvement_request_id": None, "request_version": None},
            [examples.SAME],
            [examples.CASE_EVIDENCE],
        )
        add_request(admin, examples.request(9) | {"id": DOCUMENT_REQUEST}, None, "DOCUMENT")
    return cs_database


@pytest.fixture(scope="module")
def client(seeded):
    runtime = make_conninfo(seeded, user="cs_app", password="test-only")
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(runtime))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def get(client, path, user=AGENT, **params):
    return client.get(path, params=params, headers=principal_header(user))


@pytest.mark.parametrize("name", list(examples.SEEDS))
def test_each_contract_example_is_the_real_response(client, name):
    _, comparison, request, versions, wanted, visible = examples.SEEDS[name]
    user = AGENT if visible is None else QA_STAFF
    version = {} if wanted == comparison["latest_version"] else {"version": wanted}
    expected = examples.response(name).model_dump(mode="json")["data"]
    by_group = get(
        client,
        GROUP,
        user,
        product_code=examples.PRODUCT,
        fault_id=comparison["fault_id"],
        **version,
    )
    assert by_group.status_code == 200, by_group.text
    assert by_group.json()["data"] == expected
    by_request = get(
        client,
        f"/api/v1/cs/improvement-requests/{request['id']}/condition-comparison",
        user,
        **version,
    )
    assert by_request.status_code == 200, by_request.text
    assert by_request.json()["data"] == expected


def test_versions_are_kept_apart_and_the_latest_is_the_default(client):
    latest = get(client, GROUP, product_code=examples.PRODUCT, fault_id="FLT-900").json()["data"]
    assert (latest["version"], latest["is_latest"], latest["request_version"]) == (4, True, 6)
    assert latest["conditions"][0]["judgement"] == "CONFIRMED"
    past = get(client, GROUP, product_code=examples.PRODUCT, fault_id="FLT-900", version=3)
    assert past.json()["data"]["conditions"][0]["judgement"] == "SUSPECTED"
    missing = get(client, GROUP, product_code=examples.PRODUCT, fault_id="FLT-900", version=2)
    assert (missing.status_code, missing.json()["error"]["code"]) == (
        404,
        "CS_COMPARISON_NOT_FOUND",
    )


def test_a_full_scope_user_sees_hidden_evidence(client):
    data = get(client, GROUP, product_code=examples.PRODUCT, fault_id="FLT-904").json()["data"]
    assert data["hidden_evidence_count"] == 0
    assert [e["evidence_id"] for e in data["evidence"]] == ["ev-1", "ev-2"]
    assert data["conditions"][0]["target"]["masked_value"] == "야간 연속 운전(현장 ***)"


def test_a_group_without_a_request_is_only_for_full_scope_users(client):
    agent = get(client, GROUP, product_code=examples.PRODUCT, fault_id=NO_REQUEST)
    assert agent.status_code == 200
    data = agent.json()["data"]
    assert data["improvement_request_id"] is None and data["condition_summary"] is None
    staff = get(client, GROUP, QA_STAFF, product_code=examples.PRODUCT, fault_id=NO_REQUEST)
    assert staff.status_code == 404


@pytest.mark.parametrize(
    ("user", "path", "params", "status", "code"),
    [
        (SALES, GROUP, {"product_code": examples.PRODUCT, "fault_id": "FLT-901"}, 403, None),
        (QA_STAFF, GROUP, {"product_code": examples.PRODUCT, "fault_id": "FLT-901"}, 404, None),
        (AGENT, GROUP, {"product_code": examples.PRODUCT, "fault_id": "FLT-999"}, 404, None),
        (AGENT, GROUP, {"product_code": examples.PRODUCT, "fault_id": "FLT-9"}, 422, None),
        (AGENT, GROUP, {"product_code": "", "fault_id": "FLT-901"}, 422, None),
        (
            AGENT,
            GROUP,
            {"product_code": examples.PRODUCT, "fault_id": "FLT-901", "version": 0},
            422,
            None,
        ),
        (
            QA_STAFF,
            f"/api/v1/cs/improvement-requests/{examples.request(1)['id']}/condition-comparison",
            {},
            404,
            "CS_REQUEST_NOT_FOUND",
        ),
        (
            AGENT,
            f"/api/v1/cs/improvement-requests/{uuid4()}/condition-comparison",
            {},
            404,
            "CS_REQUEST_NOT_FOUND",
        ),
        (
            AGENT,
            f"/api/v1/cs/improvement-requests/{DOCUMENT_REQUEST}/condition-comparison",
            {},
            404,
            "CS_COMPARISON_NOT_FOUND",
        ),
    ],
)
def test_scope_and_input_errors(client, user, path, params, status, code):
    response = get(client, path, user, **params)
    assert response.status_code == status, response.text
    if code:
        assert response.json()["error"]["code"] == code


def test_the_database_rejects_rows_against_the_contract(seeded):
    comparison_id = examples.comparison(1)["id"]
    bad = [
        examples.SAME | {"code": "load_profile", "finding": None},
        examples.SAME | {"code": "load_profile", "target_value": None},
        examples.SAME
        | {"code": "load_profile", "target_value": None, "target_missing_reason": "NOT_PROVIDED"},
        examples.SAME
        | {"code": "load_profile", "evidence_ids": [], "baseline_evidence_ids": []}
        | {"target_evidence_ids": []},
        examples.SAME | {"code": "unknown_field"},
        examples.DIFFERS | {"code": "load_profile", "decided_by_name": "누군가"},
    ]
    with psycopg.connect(seeded, autocommit=True) as admin:
        for row in bad:
            with pytest.raises(psycopg.errors.CheckViolation):
                insert(
                    admin,
                    "condition_comparison_items",
                    {"comparison_id": comparison_id, "version": 1, **row},
                )


def test_the_service_account_cannot_rewrite_versions(seeded):
    comparison_id = examples.comparison(1)["id"]
    runtime = make_conninfo(seeded, user="cs_app", password="test-only")
    with psycopg.connect(runtime, autocommit=True) as conn:
        for statement in (
            "UPDATE cs.condition_comparison_items SET masked_summary = 'x' WHERE comparison_id = %s",
            "DELETE FROM cs.condition_comparison_versions WHERE comparison_id = %s",
            "UPDATE cs.condition_comparisons SET fault_id = 'FLT-999' WHERE id = %s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement, (comparison_id,))
        conn.execute(
            "UPDATE cs.condition_comparisons SET latest_version = 1 WHERE id = %s",
            (comparison_id,),
        )
