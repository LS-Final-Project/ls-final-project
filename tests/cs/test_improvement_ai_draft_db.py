"""KAN-115 개선요청 AI 작성 초안 API — 실제 PostgreSQL, 가짜 모델(모델 호출 없음).

조회는 요청 조회 범위, 생성은 cs_agent. 같은 근거 입력이면 모델을 다시 부르지 않고, 실패도 시도로 남긴다.
초안은 요청서를 바꾸지 않는다(사람이 내용 수정으로 반영).
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from backend.api.cs.improvements.ai_draft import (
    PROMPT_VERSION,
    CsImprovementDraftSettings,
    configure_improvement_draft,
    context_for,
    input_sha256,
    prepared,
    references,
)
from backend.api.cs.improvements.ai_draft_repository import EVIDENCE_SQL
from backend.api.cs.main import create_app
from backend.engine.cs.classification_call import ClassificationCallError
from backend.engine.cs.improvement_drafting import IMPROVEMENT_DRAFT_INPUT_VERSION
from backend.shared.access import Principal, principal_header
from backend.shared.ai import Generation, Usage
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
QA_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="QA_CLAIM")
LV_STAFF = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_LV")
PRODUCT = "TRF-OIL-1000"
PATH = "/api/v1/cs/improvement-requests"


def answer(refs: list[str], **changes) -> str:
    """엔진 검사를 통과하는 초안 JSON. refs는 입력 근거 참조(문의 외부 ID·사례 외부 ID)다."""
    draft = {
        "problem_statement": "TRF-OIL-1000 개스킷 누유가 고객 여러 곳에서 반복되는지 조사한다.",
        "confirmed_facts": [{"text": "세 고객이 개스킷 누유를 신고했다.", "evidence_refs": refs}],
        "inconsistencies": [],
        "missing_info": ["출하 로트", "운송 경로"],
        "not_claimed": ["운송 파손이라고 단정하지 않는다."],
        "review_task": "누유가 특정 로트에 몰렸는지 확인한다.",
        "proposed_depts": ["FIELD_SVC"],
        "dept_questions": [
            {"dept_code": "FIELD_SVC", "question": "같은 로트 출하분을 알려 주세요."}
        ],
        **changes,
    }
    return json.dumps(draft, ensure_ascii=False)


class FakeModel:
    """다음 응답을 정해 두고 받은 프롬프트를 기록한다."""

    def __init__(self):
        self.prompts: list[str] = []
        self.next: object = None
        self.during = None

    def __call__(self, prompt: str) -> Generation:
        self.prompts.append(prompt)
        if self.during:
            self.during()
        if isinstance(self.next, Exception):
            raise self.next
        usage = Usage(input_tokens=900, output_tokens=300, thought_tokens=200, total_tokens=1400)
        return Generation(str(self.next), usage, "gemini-3.6-flash-001", ["STOP"], elapsed_ms=4321)


@pytest.fixture(scope="module")
def runtime_dsn(cs_database):
    return make_conninfo(cs_database, user="cs_app", password="test-only")


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def client(runtime_dsn, model):
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(runtime_dsn))
    app = create_app(settings)
    configure_improvement_draft(app, CsImprovementDraftSettings(enabled=True), provider=model)
    with TestClient(app, headers=principal_header(AGENT)) as test_client:
        yield test_client


def add_ticket(admin):
    """문의와 그 해결 사례. (ticket_id, case_id, 문의 외부 ID, 사례 외부 ID)."""
    ticket_id, case_id = uuid4(), uuid4()
    at = datetime(2025, 10, 5, tzinfo=UTC)
    ticket_ref, case_ref = f"T-{uuid4().hex[:8]}", f"RC-{uuid4().hex[:8]}"
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, subject, body) VALUES (%s, 'test', 'kan115', %s, 'synthetic', 'PROVIDED', "
        "%s, %s, 'EMAIL', 'en', %s, %s, 'Oil leak', "
        "'Oil leakage at gasket. Contact buyer@example.com.')",
        (ticket_id, ticket_ref, at, at, f"CUST-{uuid4().hex[:6]}", PRODUCT),
    )
    admin.execute(
        "INSERT INTO cs.resolution_cases (id, ticket_id, source, source_instance, external_id, "
        "data_origin, derivation_kind, resolved_at, known_at, product_code, fault_id, symptom, "
        "root_cause, root_cause_type, action_taken, onsite_visit, resolved_by_dept) VALUES "
        "(%s, %s, 'test', 'kan115', %s, 'synthetic', 'PROVIDED', %s, %s, %s, 'FLT-067', "
        "'Oil leak', 'Gasket damaged in transport', 'WEAR_PART', 'Replace gasket', true, "
        "'FIELD_SVC')",
        (case_id, ticket_id, case_ref, at, at, PRODUCT),
    )
    return ticket_id, case_id, ticket_ref, case_ref


@pytest.fixture
def request_with_evidence(client, admin):
    rows = [add_ticket(admin) for _ in range(3)]
    body = {
        "request_type": "FAULT",
        "product_code": PRODUCT,
        "fault_id": "FLT-067",
        "problem_statement": "개스킷 누유 반복",
        "proposed_depts": ["QA_CLAIM"],
        "evidence": [{"ticket_id": str(t), "case_id": str(c)} for t, c, _, _ in rows],
    }
    created = client.post(PATH, json=body)
    assert created.status_code == 201, created.text
    return created.json()["data"]["request"], rows


def path(request_id):
    return f"{PATH}/{request_id}/ai-draft"


def test_an_agent_makes_a_draft_once_and_the_request_is_unchanged(
    client, model, admin, request_with_evidence
):
    """처음 POST는 모델을 불러 201, 같은 근거면 200으로 저장본을 돌려준다. 요청서 판본은 그대로다."""
    request, rows = request_with_evidence
    model.next = answer([rows[0][2], rows[0][3]])
    empty = client.get(path(request["id"])).json()["data"]
    assert (empty["draft"], empty["latest_attempt"]) == (None, None)
    made = client.post(path(request["id"]))
    assert made.status_code == 201, made.text
    draft = made.json()["data"]["draft"]
    assert draft["stale"] is False and draft["request_version"] == 1
    assert draft["missing_info"] == ["출하 로트", "운송 경로"]
    assert draft["dept_questions"] == [
        {"dept_code": "FIELD_SVC", "question": "같은 로트 출하분을 알려 주세요."}
    ]
    # 근거 참조는 원문 링크를 위해 문의·사례 ID와 함께 돌려준다.
    refs = {item["ref"]: item for item in draft["evidence"]}
    assert refs[rows[0][2]] == {"ref": rows[0][2], "ticket_id": str(rows[0][0]), "case_id": None}
    assert refs[rows[0][3]]["case_id"] == str(rows[0][1])
    # 가린 입력만 모델에 가고, 근거 문의 3건과 사례 3건이 들어간다.
    prompt = model.prompts[0]
    assert "buyer@example.com" not in prompt
    assert all(ref in prompt for _, _, t, c in rows for ref in (t, c))
    again = client.post(path(request["id"]))
    assert again.status_code == 200 and len(model.prompts) == 1
    assert client.get(f"{PATH}/{request['id']}").json()["data"]["request"]["version"] == 1
    stored = admin.execute(
        "SELECT outcome, requested_model, model_version, input_tokens, latency_ms, requested_by, "
        "provenance_kind FROM cs.improvement_ai_drafts WHERE request_id = %s",
        (request["id"],),
    ).fetchall()
    assert stored == [
        ("VALID", "gemini-3.6-flash", "gemini-3.6-flash-001", 900, 4321, AGENT.user_id, "ai_draft")
    ]


def test_editing_the_content_keeps_the_draft_but_changing_evidence_makes_it_stale(
    client, model, request_with_evidence
):
    request, rows = request_with_evidence
    model.next = answer([rows[1][2]])
    client.post(path(request["id"]))
    # 사람이 내용·제안 부서를 고쳐도(판본 2) 근거 입력이 같으니 같은 초안이다.
    edited = client.patch(
        f"{PATH}/{request['id']}",
        json={"version": 1, "review_task": "x", "proposed_depts": ["QA_CLAIM", "SCM"]},
    )
    assert edited.status_code == 200, edited.text
    assert client.get(path(request["id"])).json()["data"]["draft"]["stale"] is False
    # 근거를 빼면 입력이 바뀌어 오래된 초안이 되고, 새로 만들 수 있다.
    link = next(
        e["id"] for e in edited.json()["data"]["evidence"] if e["ticket_id"] == str(rows[0][0])
    )
    removed = client.post(
        f"{PATH}/{request['id']}/evidence/{link}/remove", json={"version": 2, "reason": "other"}
    )
    assert removed.status_code == 200, removed.text
    assert client.get(path(request["id"])).json()["data"]["draft"]["stale"] is True
    made = client.post(path(request["id"]))
    assert made.status_code == 201 and len(model.prompts) == 2
    assert rows[0][2] not in model.prompts[1]


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (lambda refs: answer(["T-unknown"]), "CS_INVALID_OUTPUT"),
        (lambda refs: answer(refs, proposed_depts=["SCM"], dept_questions=[]), "CS_INVALID_OUTPUT"),
        (
            lambda refs: answer(refs, review_task="RMA 승인 완료되었습니다."),
            "CS_FORBIDDEN_COMMITMENT",
        ),
        (lambda refs: "not json", "CS_INVALID_OUTPUT"),
    ],
)
def test_a_rejected_output_is_stored_and_answered_502(
    client, model, admin, request_with_evidence, reply, error
):
    """근거 밖 참조·후보 밖 부서·확정 약속·형식 오류는 초안이 아니다. 시도는 남긴다."""
    request, rows = request_with_evidence
    model.next = reply([rows[0][2]])
    failed = client.post(path(request["id"]))
    assert failed.status_code == 502, failed.text
    assert failed.json()["error"]["code"] == "CS_IMPROVEMENT_DRAFT_FAILED"
    view = client.get(path(request["id"])).json()["data"]
    assert view["draft"] is None
    assert (view["latest_attempt"]["outcome"], view["latest_attempt"]["error_code"]) == (
        "INVALID_OUTPUT",
        error,
    )


def test_a_provider_error_keeps_the_usage_it_reports(client, model, admin, request_with_evidence):
    request, _ = request_with_evidence
    model.next = ClassificationCallError(
        "CS_PROVIDER_TIMEOUT", usage=Usage(input_tokens=900, output_tokens=0, total_tokens=900)
    )
    assert client.post(path(request["id"])).status_code == 502
    assert admin.execute(
        "SELECT outcome, error_code, input_tokens FROM cs.improvement_ai_drafts "
        "WHERE request_id = %s",
        (request["id"],),
    ).fetchall() == [("PROVIDER_ERROR", "CS_PROVIDER_TIMEOUT", 900)]


def test_scope_role_state_and_activation(runtime_dsn, client, model, admin, request_with_evidence):
    request, rows = request_with_evidence
    model.next = answer([rows[0][2]])
    # 받는 부서 담당자는 볼 수 있어도 만들 수 없고, 관계없는 부서는 요청이 보이지 않는다.
    admin.execute(
        "INSERT INTO cs.request_departments (request_id, round, dept_code, status) "
        "VALUES (%s, 1, 'QA_CLAIM', 'SENT')",
        (request["id"],),
    )
    # 초안은 모든 근거로 만든 것이라, 근거 일부만 볼 수 있는 받는 부서 담당자는 조회도 생성도 못 한다.
    assert client.get(path(request["id"]), headers=principal_header(QA_STAFF)).status_code == 403
    assert client.post(path(request["id"]), headers=principal_header(QA_STAFF)).status_code == 403
    assert client.post(path(request["id"]), headers=principal_header(LV_STAFF)).status_code == 404
    assert client.post(path(uuid4())).status_code == 404
    # 완료된 요청은 만들지 않는다.
    admin.execute(
        "UPDATE cs.improvement_requests SET status = 'COMPLETED' WHERE id = %s", (request["id"],)
    )
    done = client.post(path(request["id"]))
    assert (done.status_code, done.json()["error"]["code"]) == (409, "CS_REQUEST_COMPLETED")
    assert model.prompts == []
    # 꺼져 있으면 두 경로 모두 404다.
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(runtime_dsn))
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as off:
        assert off.get(path(request["id"])).status_code == 404
        assert off.post(path(request["id"])).status_code == 404


def test_drafts_are_append_only_for_the_service(cs_database, client, model, request_with_evidence):
    request, rows = request_with_evidence
    model.next = answer([rows[0][2]])
    client.post(path(request["id"]))
    with psycopg.connect(make_conninfo(cs_database, user="cs_app", password="test-only")) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE cs.improvement_ai_drafts SET outcome = 'VALID'")


def test_another_instance_saving_first_is_returned_instead_of_a_duplicate(
    client, model, admin, request_with_evidence
):
    """모델을 기다리는 사이 다른 인스턴스가 같은 입력의 초안을 저장하면, 중복 행 없이 그것을 200으로 준다."""
    request, rows = request_with_evidence
    model.next = answer([rows[0][2]])
    request_row = (
        admin.cursor(row_factory=dict_row)
        .execute("SELECT * FROM cs.improvement_requests WHERE id = %s", (request["id"],))
        .fetchone()
    )
    evidence = admin.cursor(row_factory=dict_row).execute(EVIDENCE_SQL, (request["id"],)).fetchall()
    current = input_sha256(prepared(context_for(request_row, evidence, datetime.now(UTC))))
    other = json.loads(answer([rows[1][2]], review_task="다른 인스턴스 검토 과제"))

    def other_instance_saves():
        admin.execute(
            "INSERT INTO cs.improvement_ai_drafts (id, request_id, request_version, outcome, draft, "
            "input_version, input_sha256, prompt_version, requested_model, requested_by) VALUES "
            "(%s, %s, 1, 'VALID', %s, %s, %s, %s, 'gemini-3.6-flash', %s)",
            (
                uuid4(),
                request["id"],
                Jsonb({"draft": other, "refs": references(evidence)}),
                IMPROVEMENT_DRAFT_INPUT_VERSION,
                current,
                PROMPT_VERSION,
                uuid4(),
            ),
        )

    model.during = other_instance_saves
    response = client.post(path(request["id"]))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["draft"]["review_task"] == "다른 인스턴스 검토 과제"
    assert admin.execute(
        "SELECT count(*) FROM cs.improvement_ai_drafts WHERE request_id = %s AND outcome = 'VALID'",
        (request["id"],),
    ).fetchone() == (1,)
