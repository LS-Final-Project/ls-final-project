"""KAN-131 한국어 요약 API — 실제 PostgreSQL, 가짜 모델(모델 호출 없음).

조회는 문의 조회 범위, 생성은 cs_agent. 같은 원문이면 모델을 다시 부르지 않고, 실패도 시도로 남긴다.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.api.cs.summary.routes import CsSummarySettings, configure_summary
from backend.engine.cs.classification_call import ClassificationCallError
from backend.engine.cs.summary import (
    SUMMARY_INPUT_VERSION,
    SUMMARY_PROMPT_VERSION,
    build_summary_input,
    input_sha256,
)
from backend.shared.access import Principal, principal_header
from backend.shared.ai import Generation, Usage
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
TRF_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_TRF")
SUMMARY = "TRF-OIL-1000 개스킷에서 누유가 있어 보증 처리 가능 여부를 묻는다고 함."


class FakeModel:
    """다음 응답을 정해 두고 받은 프롬프트를 기록한다."""

    def __init__(self):
        self.prompts: list[str] = []
        self.next: object = json.dumps({"summary_ko": SUMMARY}, ensure_ascii=False)
        # 모델이 답하기 직전에 할 일(예: 다른 인스턴스가 먼저 저장하는 상황).
        self.during = None

    def __call__(self, prompt: str) -> Generation:
        self.prompts.append(prompt)
        if self.during:
            self.during()
        if isinstance(self.next, Exception):
            raise self.next
        usage = Usage(input_tokens=120, output_tokens=40, thought_tokens=10, total_tokens=170)
        return Generation(str(self.next), usage, "gemini-3.6-flash-001", ["STOP"], elapsed_ms=812)


def settings_for(cs_database):
    return Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def client(cs_database, model):
    app = create_app(settings_for(cs_database))
    configure_summary(app, CsSummarySettings(enabled=True), provider=model)
    with TestClient(app, headers=principal_header(AGENT)) as test_client:
        yield test_client


@pytest.fixture
def ticket(admin):
    ticket_id = uuid4()
    received = datetime(2026, 10, 1, tzinfo=UTC)
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, subject, body) VALUES (%s, 'test', 'kan131-summary', %s, 'synthetic', "
        "'PROVIDED', %s, %s, 'EMAIL', 'en', 'CUST-0374', 'TRF-OIL-1000', 'Oil leak on TRF-OIL-1000', "
        "'The gasket leaks oil. Is this covered by warranty? Reach me at buyer@example.com.')",
        (ticket_id, f"T-{ticket_id}", received, received),
    )
    return ticket_id


def path(ticket_id):
    return f"/api/v1/cs/tickets/{ticket_id}/summary"


def test_an_agent_makes_a_summary_once_and_reads_it_back(client, model, admin, ticket):
    """처음 POST는 모델을 불러 201, 같은 원문이면 200으로 저장본을 돌려주고 모델을 다시 부르지 않는다."""
    empty = client.get(path(ticket)).json()["data"]
    assert (empty["summary"], empty["latest_attempt"]) == (None, None)
    made = client.post(path(ticket))
    assert made.status_code == 201, made.text
    data = made.json()["data"]
    assert data["summary"]["summary_ko"] == SUMMARY and data["summary"]["stale"] is False
    assert data["provenance"] == "ai_draft"
    # 가린 입력만 모델에 간다.
    assert len(model.prompts) == 1 and "buyer@example.com" not in model.prompts[0]
    again = client.post(path(ticket))
    assert again.status_code == 200 and len(model.prompts) == 1
    assert again.json()["data"]["summary"]["id"] == data["summary"]["id"]
    assert client.get(path(ticket)).json()["data"]["summary"]["summary_ko"] == SUMMARY
    row = admin.execute(
        "SELECT outcome, requested_model, model_version, input_tokens, output_tokens, "
        "latency_ms, requested_by, provenance_kind FROM cs.ticket_summaries WHERE ticket_id = %s",
        (ticket,),
    ).fetchall()
    assert row == [
        (
            "VALID",
            "gemini-3.6-flash",
            "gemini-3.6-flash-001",
            120,
            40,
            812,
            AGENT.user_id,
            "ai_draft",
        )
    ]


def test_an_edited_ticket_makes_the_summary_stale_and_a_new_one_can_be_made(
    client, model, admin, ticket
):
    client.post(path(ticket))
    admin.execute(
        "UPDATE cs.tickets SET body = body || ' It started yesterday.' WHERE id = %s", (ticket,)
    )
    assert client.get(path(ticket)).json()["data"]["summary"]["stale"] is True
    assert client.post(path(ticket)).status_code == 201 and len(model.prompts) == 2
    assert client.get(path(ticket)).json()["data"]["summary"]["stale"] is False


def test_reverting_the_ticket_shows_the_summary_that_matches_it_again(client, model, admin, ticket):
    """원문을 고쳐 새 요약을 만든 뒤 되돌리면, 되돌린 원문에 맞는 처음 요약을 오래되지 않은 것으로 보인다."""
    first = client.post(path(ticket)).json()["data"]["summary"]["id"]
    original = admin.execute("SELECT body FROM cs.tickets WHERE id = %s", (ticket,)).fetchone()[0]
    admin.execute(
        "UPDATE cs.tickets SET body = body || ' It started yesterday.' WHERE id = %s", (ticket,)
    )
    second = client.post(path(ticket)).json()["data"]["summary"]["id"]
    assert second != first
    admin.execute("UPDATE cs.tickets SET body = %s WHERE id = %s", (original, ticket))
    view = client.get(path(ticket)).json()["data"]["summary"]
    assert (view["id"], view["stale"]) == (first, False)
    assert len(model.prompts) == 2


@pytest.mark.parametrize(
    ("reply", "outcome", "code"),
    [
        (json.dumps({"summary_ko": "Oil leak."}), "INVALID_OUTPUT", "CS_SUMMARY_NOT_KOREAN"),
        (
            json.dumps({"summary_ko": "SN-777 장비 누유라고 함."}, ensure_ascii=False),
            "INVALID_OUTPUT",
            "CS_SUMMARY_UNGROUNDED",
        ),
        (ClassificationCallError("CS_PROVIDER_TIMEOUT"), "PROVIDER_ERROR", "CS_PROVIDER_TIMEOUT"),
    ],
)
def test_a_failed_attempt_is_stored_and_answered_502(
    client, model, admin, ticket, reply, outcome, code
):
    """실패도 시도로 남기고 502로 답한다. 성공한 요약은 그대로 보이고, 다시 만들 수 있다."""
    model.next = reply
    failed = client.post(path(ticket))
    assert failed.status_code == 502
    error = failed.json()["error"]
    assert error["code"] == "CS_SUMMARY_FAILED" and error["details"] == [{"code": code}]
    view = client.get(path(ticket)).json()["data"]
    assert view["summary"] is None
    assert (view["latest_attempt"]["outcome"], view["latest_attempt"]["error_code"]) == (
        outcome,
        code,
    )
    stored = admin.execute(
        "SELECT summary_ko FROM cs.ticket_summaries WHERE ticket_id = %s", (ticket,)
    ).fetchone()
    assert stored == (None,)
    model.next = json.dumps({"summary_ko": SUMMARY}, ensure_ascii=False)
    assert client.post(path(ticket)).status_code == 201


def test_scope_role_and_activation(cs_database, client, model, admin, ticket):
    """범위 밖은 404(문의 없음과 같음), 범위 안 부서 사용자는 조회만, 꺼져 있으면 두 경로 모두 404."""
    assert client.get(path(ticket), headers=principal_header(TRF_STAFF)).status_code == 404
    admin.execute(
        "INSERT INTO cs.ticket_transfers (id, ticket_id, dept_code, reason, round, status, "
        "transferred_by) VALUES (%s, %s, 'TS_TRF', 'check', 1, 'OPEN', %s)",
        (uuid4(), ticket, uuid4()),
    )
    assert client.get(path(ticket), headers=principal_header(TRF_STAFF)).status_code == 200
    assert client.post(path(ticket), headers=principal_header(TRF_STAFF)).status_code == 403
    assert client.post(path(uuid4())).status_code == 404
    assert model.prompts == []
    off = create_app(settings_for(cs_database))
    configure_summary(off, CsSummarySettings(enabled=False), provider=model)
    with TestClient(off, headers=principal_header(AGENT)) as disabled:
        assert disabled.get(path(ticket)).status_code == 404
        assert disabled.post(path(ticket)).status_code == 404


def test_summaries_are_append_only_for_the_service(cs_database, client, ticket):
    client.post(path(ticket))
    runtime = make_conninfo(cs_database, user="cs_app", password="test-only")
    with psycopg.connect(runtime, autocommit=True) as conn:
        for sql in (
            "UPDATE cs.ticket_summaries SET summary_ko = 'x' WHERE ticket_id = %s",
            "DELETE FROM cs.ticket_summaries WHERE ticket_id = %s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql, (ticket,))


def test_another_instance_saving_first_is_returned_instead_of_a_duplicate(
    client, model, admin, ticket
):
    """모델을 기다리는 사이 다른 인스턴스가 같은 원문의 요약을 저장하면, 중복 행 없이 그것을 200으로 준다."""
    row = admin.execute(
        "SELECT channel, body_language, subject, body FROM cs.tickets WHERE id = %s", (ticket,)
    ).fetchone()
    keys = ("channel", "body_language", "subject", "body")
    current = input_sha256(build_summary_input(dict(zip(keys, row, strict=True))))

    def other_instance_saves():
        admin.execute(
            "INSERT INTO cs.ticket_summaries (id, ticket_id, outcome, summary_ko, input_version, "
            "input_sha256, prompt_version, requested_model, requested_by) VALUES "
            "(%s, %s, 'VALID', '다른 인스턴스 요약이라고 함.', %s, %s, %s, 'gemini-3.6-flash', %s)",
            (uuid4(), ticket, SUMMARY_INPUT_VERSION, current, SUMMARY_PROMPT_VERSION, uuid4()),
        )

    model.during = other_instance_saves
    response = client.post(path(ticket))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["summary"]["summary_ko"] == "다른 인스턴스 요약이라고 함."
    count = admin.execute(
        "SELECT count(*) FROM cs.ticket_summaries WHERE ticket_id = %s AND outcome = 'VALID'",
        (ticket,),
    ).fetchone()
    assert count == (1,)


def test_a_provider_error_keeps_the_usage_it_reports(client, model, admin, ticket):
    model.next = ClassificationCallError(
        "CS_RATE_LIMITED", usage=Usage(input_tokens=90, output_tokens=0, total_tokens=90)
    )
    assert client.post(path(ticket)).status_code == 502
    assert admin.execute(
        "SELECT outcome, error_code, input_tokens FROM cs.ticket_summaries WHERE ticket_id = %s",
        (ticket,),
    ).fetchone() == ("PROVIDER_ERROR", "CS_RATE_LIMITED", 90)


def test_reading_works_even_when_the_ticket_cannot_be_summarized(client, admin, ticket):
    """요약할 수 없는 원문(언어 표기 오류)이어도 GET은 저장본을 보여 주고, POST만 422다."""
    client.post(path(ticket))
    admin.execute("UPDATE cs.tickets SET body_language = 'not a language' WHERE id = %s", (ticket,))
    view = client.get(path(ticket))
    assert view.status_code == 200 and view.json()["data"]["summary"]["stale"] is True
    failed = client.post(path(ticket))
    assert (failed.status_code, failed.json()["error"]["code"]) == (422, "CS_SUMMARY_INPUT_INVALID")
