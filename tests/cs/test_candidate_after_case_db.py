"""KAN-206: 해결 사례 등록 직후 그 제품·고장 묶음의 개선 후보를 자동 판정한다(실제 PostgreSQL, 모델 없음).

문의 셋(서로 다른 고객, 같은 제품·고장)을 종결하고 사례를 등록하면 세 번째 등록에서 후보가 생긴다. 판정 실패는 등록을
막지 않는다.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.api.cs.workflow import routes as workflow_routes
from backend.shared.access import principal_header
from backend.shared.settings import Settings
from tests.cs.test_workflow_cases_db import AGENT, CASE, closed_with_result, register


@pytest.fixture(scope="module")
def client(cs_database):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as test_client:
        yield test_client


def add_ticket(admin, product: str, customer: str):
    ticket_id = uuid4()
    received = datetime(2026, 10, 2, tzinfo=UTC)
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan206', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', 'en', %s, %s, 'Oil leakage at gasket.')",
        (ticket_id, f"T-{ticket_id}", received, received, customer, product),
    )
    return ticket_id


def register_closed(client, admin, product: str, customer: str, **body) -> dict:
    """문의를 만들어 종결하고 사례를 등록한다. 응답에 문의 ID를 붙여 돌려준다."""
    ticket = add_ticket(admin, product, customer)
    closed_with_result(client, ticket)
    response = register(client, ticket, version=5, **body)
    assert response.status_code == 201, response.text
    return {**response.json()["data"], "ticket_id": str(ticket)}


def test_the_third_customer_creates_the_candidate_and_later_ones_see_it(client, admin):
    product = f"TRF-{uuid4().hex[:6].upper()}"
    first = register_closed(client, admin, product, "CUST-A001")
    second = register_closed(client, admin, product, "CUST-A002")
    assert first["improvement_candidate"] == {"outcome": "NOT_ELIGIBLE", "request_id": None}
    assert second["improvement_candidate"]["outcome"] == "NOT_ELIGIBLE"
    third = register_closed(client, admin, product, "CUST-A003")
    assert third["improvement_candidate"]["outcome"] == "CREATED"
    request_id = third["improvement_candidate"]["request_id"]
    row = admin.execute(
        "SELECT status, origin, product_code, fault_id, candidate_reason_code "
        "FROM cs.improvement_requests WHERE id = %s",
        (request_id,),
    ).fetchone()
    assert row[:4] == ("DRAFT", "SYSTEM_CANDIDATE", product, CASE["fault_id"])
    evidence = admin.execute(
        "SELECT count(*) FROM cs.request_evidence WHERE request_id = %s", (request_id,)
    ).fetchone()
    assert evidence == (3,)
    # 네 번째 고객: 요청이 이미 있어 새로 만들지 않고 그 요청을 가리킨다.
    fourth = register_closed(client, admin, product, "CUST-A004")
    assert fourth["improvement_candidate"] == {
        "outcome": "ALREADY_REQUESTED",
        "request_id": request_id,
    }
    assert admin.execute(
        "SELECT count(*) FROM cs.improvement_requests WHERE product_code = %s", (product,)
    ).fetchone() == (1,)
    # 저장된 사례를 뒤에 읽을 때는 이 칸이 없다.
    detail = client.get(f"/api/v1/cs/tickets/{third['ticket_id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["resolution_case"]["improvement_candidate"] is None


def test_a_judgement_failure_keeps_the_registration(client, admin, monkeypatch):
    product = f"TRF-{uuid4().hex[:6].upper()}"

    async def broken(*args, **kwargs):
        raise RuntimeError("judgement unavailable")

    monkeypatch.setattr(
        "backend.api.cs.improvements.candidates.save_candidates", broken, raising=True
    )
    data = register_closed(client, admin, product, "CUST-B001")
    assert data["improvement_candidate"] == {"outcome": "FAILED", "request_id": None}
    assert admin.execute(
        "SELECT count(*) FROM cs.resolution_cases WHERE product_code = %s", (product,)
    ).fetchone() == (1,)
    assert workflow_routes.candidate_after_case is not None


def test_a_group_without_a_transfer_department_is_skipped_not_saved(client, admin):
    product = f"TRF-{uuid4().hex[:6].upper()}"
    for customer in ("CUST-C001", "CUST-C002"):
        register_closed(client, admin, product, customer, resolved_by_dept="GCS_L1")
    third = register_closed(client, admin, product, "CUST-C003", resolved_by_dept="GCS_L1")
    assert third["improvement_candidate"] == {"outcome": "SKIPPED", "request_id": None}
    assert admin.execute(
        "SELECT count(*) FROM cs.improvement_requests WHERE product_code = %s", (product,)
    ).fetchone() == (0,)
