"""회신 반영 분석 API(KAN-230 2단계) — 실제 PostgreSQL, 가짜 모델(모델 호출 없음).

조회는 문의 조회 범위, 생성은 cs_agent만. 같은 회신·입력이면 모델을 다시 부르지 않고, 실패도 시도로 남긴다.
본문이 있는 회신이 없으면 모델을 부르지 않고 422다. 업무 상태·분석 칸·정정은 바꾸지 않는다.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.api.cs.reply_analysis.routes import (
    PROMPT_VERSION,
    CsReplyAnalysisSettings,
    configure_reply_analysis,
)
from backend.engine.cs.classification_call import ClassificationCallError
from backend.engine.cs.reply import REPLY_INPUT_VERSION
from backend.shared.access import Principal, principal_header
from backend.shared.ai import Generation, Usage
from backend.shared.settings import Settings
from tests.cs.test_analysis_db import draft_options, store
from tests.cs.test_worker_source_db import add_send
from tests.cs.test_workflow_db import act, ok

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
TRF_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_TRF")
RECEIVED = datetime(2026, 10, 1, 9, tzinfo=UTC)
REPLY_BODY = "Photos attached. Write to me at buyer@example.com."


def answer(
    *,
    answered=("photos",),
    remaining=(("serial_no", "Please send the serial number."),),
    unit="NOT_MENTIONED",
    request="APPLIES",
) -> str:
    return json.dumps(
        {
            "applicability": {
                "same_request": request,
                "same_request_reason": "The reply answers the photo request.",
                "same_unit": unit,
                "same_unit_reason": "The reply names no other unit.",
                "same_version": "NOT_MENTIONED",
                "same_version_reason": "No firmware version in the reply.",
            },
            "answered_codes": list(answered),
            "new_facts": [{"text": "The leak started after the storm.", "kind": "STATED"}],
            "remaining_questions": [{"info_code": c, "question": q} for c, q in remaining],
        }
    )


class FakeModel:
    """다음 응답을 정해 두고 받은 프롬프트를 기록한다."""

    def __init__(self):
        self.prompts: list[str] = []
        self.next: object = answer()

    def __call__(self, prompt: str) -> Generation:
        self.prompts.append(prompt)
        if isinstance(self.next, Exception):
            raise self.next
        usage = Usage(input_tokens=420, output_tokens=180, thought_tokens=30, total_tokens=630)
        return Generation(str(self.next), usage, "gemini-3.6-flash-001", ["STOP"], elapsed_ms=777)

    def payload(self, index: int = -1) -> dict:
        """프롬프트 뒤의 정제 입력 JSON."""
        return json.loads(self.prompts[index].split("\n\n입력:\n", 1)[1])


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
    configure_reply_analysis(app, CsReplyAnalysisSettings(enabled=True), provider=model)
    with TestClient(app, headers=principal_header(AGENT)) as test_client:
        yield test_client


def new_ticket(admin):
    ticket_id = uuid4()
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, subject, body, fw_version_reported) VALUES (%s, 'test', 'reply-analysis', "
        "%s, 'synthetic', 'PROVIDED', %s, %s, 'EMAIL', 'en', 'CUST-0374', 'TRF-OIL-1000', "
        "'Oil leak', 'Oil leakage at gasket.', '4.2.1')",
        (ticket_id, f"T-{ticket_id}", RECEIVED, RECEIVED),
    )
    return ticket_id


@pytest.fixture
def ticket(admin):
    ticket_id = new_ticket(admin)
    add_send(admin, ticket_id, 1, ["photos", "serial_no"], sent=True)
    # 승인만 되고 나가지 않은 질문은 요청 코드가 아니다.
    add_send(admin, ticket_id, 2, ["error_code"], sent=False)
    return ticket_id


def add_reply(admin, ticket_id, body=REPLY_BODY, minutes=0):
    message_id = uuid4()
    admin.execute(
        "INSERT INTO cs.ticket_customer_replies (message_id, ticket_id, method, received_at, "
        "reopened, subject, body) VALUES (%s, %s, 'HEADER', %s, false, 'Re: Oil leak', %s)",
        (message_id, ticket_id, RECEIVED + timedelta(days=1, minutes=minutes), body),
    )
    return message_id


def path(ticket_id):
    return f"/api/v1/cs/tickets/{ticket_id}/reply-analysis"


def rows(admin, ticket_id):
    return admin.execute(
        "SELECT outcome, error_code, interpretation IS NOT NULL, reply_update IS NOT NULL "
        "FROM cs.ticket_reply_analyses WHERE ticket_id = %s ORDER BY created_at",
        (ticket_id,),
    ).fetchall()


def test_an_agent_analyses_the_latest_reply_once_and_reads_it_back(client, model, admin, ticket):
    """POST는 본문 있는 최근 회신을 한 번 해석해 201, 같은 입력이면 200으로 저장본을 주고 다시 부르지 않는다."""
    add_reply(admin, ticket, body="Old reply.", minutes=0)
    # 본문 없는 회신(C30 이전 기록)은 기본 대상에서 빠진다.
    admin.execute(
        "INSERT INTO cs.ticket_customer_replies (message_id, ticket_id, method, received_at, "
        "reopened) VALUES (%s, %s, 'HEADER', %s, false)",
        (uuid4(), ticket, RECEIVED + timedelta(days=3)),
    )
    latest = add_reply(admin, ticket, minutes=10)
    empty = ok(client.get(path(ticket)))
    assert (empty["message_id"], empty["analysis"], empty["latest_attempt"]) == (
        str(latest),
        None,
        None,
    )
    made = client.post(path(ticket))
    assert made.status_code == 201, made.text
    data = made.json()["data"]
    assert data["message_id"] == str(latest) and data["provenance"] == "ai_draft"
    analysis = data["analysis"]
    assert analysis["interpretation"]["answered_codes"] == ["photos"]
    assert analysis["update"] == {
        "provided_codes": ["photos"],
        "still_missing": ["serial_no"],
        "next_action": "REQUEST_AGAIN",
        "applicability": analysis["interpretation"]["applicability"],
        "new_facts": [{"text": "The leak started after the storm.", "kind": "STATED"}],
        "remaining_questions": [
            {"info_code": "serial_no", "question": "Please send the serial number."}
        ],
    }
    assert analysis["stale"] is False and analysis["prompt_version"] == PROMPT_VERSION
    # 가린 본문만 모델에 간다. 제목은 넣지 않는다.
    assert len(model.prompts) == 1 and "buyer@example.com" not in model.prompts[0]
    payload = model.payload()
    assert payload["body"] == "Photos attached. Write to me at [PERSONAL_PROVIDED]."
    assert (payload["ticket_product_code"], payload["fw_version_reported"]) == (
        "TRF-OIL-1000",
        "4.2.1",
    )
    again = client.post(path(ticket), json={"message_id": str(latest)})
    assert again.status_code == 200 and len(model.prompts) == 1
    assert again.json()["data"]["analysis"]["id"] == analysis["id"]
    assert ok(client.get(path(ticket)))["analysis"]["id"] == analysis["id"]
    stored = admin.execute(
        "SELECT outcome, message_id, requested_model, model_version, input_tokens, latency_ms, "
        "requested_by, provenance_kind, input_version FROM cs.ticket_reply_analyses "
        "WHERE ticket_id = %s",
        (ticket,),
    ).fetchall()
    assert stored == [
        (
            "VALID",
            latest,
            "gemini-3.6-flash",
            "gemini-3.6-flash-001",
            420,
            777,
            AGENT.user_id,
            "ai_draft",
            REPLY_INPUT_VERSION,
        )
    ]
    # 상태·분석 칸·정정은 바꾸지 않는다.
    assert admin.execute(
        "SELECT count(*) FROM cs.ticket_classification_corrections WHERE ticket_id = %s",
        (ticket,),
    ).fetchone() == (0,)


def test_an_older_reply_can_be_chosen(client, model, admin, ticket):
    older = add_reply(admin, ticket, body="Serial will follow.", minutes=0)
    add_reply(admin, ticket, minutes=10)
    made = client.post(path(ticket), json={"message_id": str(older)})
    assert made.status_code == 201, made.text
    assert model.payload()["body"] == "Serial will follow."
    view = ok(client.get(path(ticket), params={"message_id": str(older)}))
    assert view["message_id"] == str(older) and view["analysis"] is not None
    # 기본 대상(최근 회신)에는 아직 해석이 없다.
    assert ok(client.get(path(ticket)))["analysis"] is None


def test_no_reply_text_is_422_without_a_model_call(client, model, admin, ticket):
    failed = client.post(path(ticket))
    assert (failed.status_code, failed.json()["error"]["code"]) == (422, "CS_REPLY_TEXT_MISSING")
    no_text = uuid4()
    admin.execute(
        "INSERT INTO cs.ticket_customer_replies (message_id, ticket_id, method, received_at, "
        "reopened) VALUES (%s, %s, 'THREAD', %s, false)",
        (no_text, ticket, RECEIVED + timedelta(days=1)),
    )
    failed = client.post(path(ticket), json={"message_id": str(no_text)})
    assert (failed.status_code, failed.json()["error"]["code"]) == (422, "CS_REPLY_TEXT_MISSING")
    assert client.post(path(ticket)).status_code == 422
    assert ok(client.get(path(ticket)))["message_id"] is None
    assert model.prompts == [] and rows(admin, ticket) == []


def test_scope_role_and_activation(cs_database, client, model, admin, ticket):
    """범위 밖은 404. 이관받은 부서 담당자도 회신 해석은 읽지도 만들지도 못한다(403, 회신은 C/S 업무). 다른 문의 회신은 404."""
    message_id = add_reply(admin, ticket)
    assert client.get(path(ticket), headers=principal_header(TRF_STAFF)).status_code == 404
    assert client.post(path(ticket), headers=principal_header(TRF_STAFF)).status_code == 404
    admin.execute(
        "INSERT INTO cs.ticket_transfers (id, ticket_id, dept_code, reason, round, status, "
        "transferred_by) VALUES (%s, %s, 'TS_TRF', 'check', 1, 'OPEN', %s)",
        (uuid4(), ticket, uuid4()),
    )
    assert client.get(path(ticket), headers=principal_header(TRF_STAFF)).status_code == 403
    assert client.post(path(ticket), headers=principal_header(TRF_STAFF)).status_code == 403
    assert client.post(path(uuid4())).status_code == 404
    other = new_ticket(admin)
    other_reply = add_reply(admin, other)
    for response in (
        client.post(path(ticket), json={"message_id": str(other_reply)}),
        client.get(path(ticket), params={"message_id": str(other_reply)}),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (
            404,
            "CS_REPLY_NOT_FOUND",
        )
    assert model.prompts == []
    assert client.post(path(ticket), json={"message_id": str(message_id)}).status_code == 201
    assert client.post(path(ticket), json={"unknown": 1}).status_code == 422
    off = create_app(settings_for(cs_database))
    configure_reply_analysis(off, CsReplyAnalysisSettings(enabled=False), provider=model)
    with TestClient(off, headers=principal_header(AGENT)) as disabled:
        assert disabled.get(path(ticket)).status_code == 404
        assert disabled.post(path(ticket)).status_code == 404
    assert len(model.prompts) == 1


@pytest.mark.parametrize(
    ("reply", "outcome", "code"),
    [
        # 요청하지 않은 코드를 답했다고 하면 형식 위반이다.
        (answer(answered=("photos", "error_code")), "INVALID_OUTPUT", "CS_INVALID_OUTPUT"),
        ("not json", "INVALID_OUTPUT", "CS_INVALID_OUTPUT"),
        (
            answer().replace(
                "The leak started after the storm.", "The warranty has been approved."
            ),
            "INVALID_OUTPUT",
            "CS_FORBIDDEN_COMMITMENT",
        ),
        (ClassificationCallError("CS_PROVIDER_TIMEOUT"), "PROVIDER_ERROR", "CS_PROVIDER_TIMEOUT"),
    ],
)
def test_a_failed_attempt_is_stored_and_answered_502(
    client, model, admin, ticket, reply, outcome, code
):
    add_reply(admin, ticket)
    model.next = reply
    failed = client.post(path(ticket))
    assert failed.status_code == 502
    error = failed.json()["error"]
    assert error["code"] == "CS_REPLY_ANALYSIS_FAILED" and error["details"] == [{"code": code}]
    view = ok(client.get(path(ticket)))
    assert view["analysis"] is None
    assert (view["latest_attempt"]["outcome"], view["latest_attempt"]["error_code"]) == (
        outcome,
        code,
    )
    assert rows(admin, ticket) == [(outcome, code, False, False)]
    model.next = answer()
    assert client.post(path(ticket)).status_code == 201


def test_a_provider_error_keeps_the_usage_it_reports(client, model, admin, ticket):
    add_reply(admin, ticket)
    model.next = ClassificationCallError(
        "CS_RATE_LIMITED", usage=Usage(input_tokens=90, output_tokens=0, total_tokens=90)
    )
    assert client.post(path(ticket)).status_code == 502
    assert admin.execute(
        "SELECT outcome, error_code, input_tokens FROM cs.ticket_reply_analyses "
        "WHERE ticket_id = %s",
        (ticket,),
    ).fetchone() == ("PROVIDER_ERROR", "CS_RATE_LIMITED", 90)


def test_the_context_uses_sent_questions_effective_provided_info_and_the_valid_draft(
    cs_database, client, model, admin, ticket
):
    """요청 코드는 실제로 나간 질문, 제공 정보는 사람 정정을 반영한 값, 이전 초안은 최근 유효 초안이다.
    정정하면 해석이 오래된 것(stale)이 되고 다시 만들 수 있다."""
    import asyncio

    from backend.api.cs.analysis.service import record_draft
    from backend.shared.database import connection

    settings = settings_for(cs_database)
    add_reply(admin, ticket)
    # 유효 분석·정정이 없으면 제공 정보는 비어 있다.
    assert client.post(path(ticket)).status_code == 201
    first = model.payload()
    assert first["requested_info_codes"] == ["photos", "serial_no"]
    assert (first["already_provided_codes"], first["prior_options"]) == ([], None)
    run = store(
        settings,
        ticket,
        {
            "category_code": "DEFECT",
            "secondary_category": None,
            "urgency_flag": "NONE",
            "provided_info": ["product_model"],
            "return_subtype": None,
        },
    )

    async def save_draft():
        async with connection(settings) as conn:
            return await record_draft(
                conn,
                ticket,
                {
                    "input_version": "cs-draft-input-v1",
                    "analysis_id": run["id"],
                    "outcome": "VALID",
                    "draft": draft_options(),
                },
            )

    asyncio.run(save_draft())
    assert ok(client.get(path(ticket)))["analysis"]["stale"] is True
    assert client.post(path(ticket)).status_code == 201
    second = model.payload()
    assert second["already_provided_codes"] == ["product_model"]
    assert [o["label"] for o in second["prior_options"]["options"]] == [
        "CONCISE",
        "DETAILED",
        "INFO_FIRST",
    ]
    assert ok(client.get(path(ticket)))["analysis"]["update"]["provided_codes"] == [
        "photos",
        "product_model",
    ]
    ok(act(client, ticket, "start-review", version=0))
    corrected = client.post(
        f"/api/v1/cs/tickets/{ticket}/analysis/corrections",
        json={
            "version": 0,
            "analysis_id": str(run["id"]),
            "provided_info": ["serial_no", "product_model"],
            "reason": "Customer confirmed by phone",
        },
    )
    assert corrected.status_code == 200, corrected.text
    assert ok(client.get(path(ticket)))["analysis"]["stale"] is True
    assert client.post(path(ticket)).status_code == 201
    assert model.payload()["already_provided_codes"] == ["product_model", "serial_no"]
    view = ok(client.get(path(ticket)))["analysis"]
    assert view["stale"] is False and view["update"]["still_missing"] == []
    assert view["update"]["next_action"] == "DRAFT_ANSWER"
    assert len(model.prompts) == 3


def test_the_serial_is_matched_against_the_confirmed_unit_and_never_sent(
    client, model, admin, ticket
):
    unit_id = uuid4()
    serial = f"VSN-TRF-{unit_id.hex[:8]}"
    add_reply(admin, ticket, body=f"Serial No: {serial}. Photos attached.")
    # 확정 장비가 없으면 NO_CONFIRMED_UNIT이고, 모델이 SAME이라 하면 형식 위반이다.
    model.next = answer(unit="SAME")
    assert client.post(path(ticket)).status_code == 502
    assert model.payload()["reply_serial_match"] == "NO_CONFIRMED_UNIT"
    admin.execute(
        "INSERT INTO cs.installed_units (id, source, source_instance, data_origin, known_at, "
        "serial_no, customer_external_id, product_code) VALUES (%s, 'test', 'reply-analysis', "
        "'demo', %s, %s, 'CUST-0374', 'TRF-OIL-1000')",
        (unit_id, RECEIVED, serial),
    )
    admin.execute(
        "INSERT INTO cs.ticket_unit_links (id, ticket_id, unit_id, data_origin, link_status, "
        "link_basis, decided_by, decided_at) VALUES (%s, %s, %s, 'demo', 'CONFIRMED', "
        "'SERIAL_PROVIDED', %s, now())",
        (uuid4(), ticket, unit_id, AGENT.user_id),
    )
    made = client.post(path(ticket))
    assert made.status_code == 201, made.text
    assert model.payload()["reply_serial_match"] == "MATCHES"
    assert all(serial not in prompt and serial.lower() not in prompt for prompt in model.prompts)
    assert made.json()["data"]["analysis"]["interpretation"]["applicability"]["same_unit"] == "SAME"


def test_reply_analyses_are_append_only_for_the_service(cs_database, client, admin, ticket):
    add_reply(admin, ticket)
    assert client.post(path(ticket)).status_code == 201
    runtime = make_conninfo(cs_database, user="cs_app", password="test-only")
    with psycopg.connect(runtime, autocommit=True) as conn:
        for sql in (
            "UPDATE cs.ticket_reply_analyses SET error_code = 'CS_X' WHERE ticket_id = %s",
            "DELETE FROM cs.ticket_reply_analyses WHERE ticket_id = %s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql, (ticket,))


def test_the_table_rejects_a_valid_row_without_interpretation(admin, ticket):
    message_id = add_reply(admin, ticket)
    base = (
        "INSERT INTO cs.ticket_reply_analyses (id, ticket_id, message_id, outcome, error_code, "
        "interpretation, reply_update, input_version, input_sha256, prompt_version, "
        "requested_model, requested_by) VALUES (%s, %s, %s, %s, %s, %s, %s, 'v', %s, 'v2', 'm', %s)"
    )
    obj = psycopg.types.json.Jsonb({})
    for outcome, code, interpretation, update in (
        ("VALID", None, None, obj),
        ("VALID", None, obj, None),
        ("INVALID_OUTPUT", "CS_INVALID_OUTPUT", obj, obj),
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(
                base,
                (
                    uuid4(),
                    ticket,
                    message_id,
                    outcome,
                    code,
                    interpretation,
                    update,
                    "0" * 64,
                    uuid4(),
                ),
            )
