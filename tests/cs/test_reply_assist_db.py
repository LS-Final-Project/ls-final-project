"""회신 초안 AI 보조 API(KAN-251) — 실제 PostgreSQL, 가짜 모델(모델 호출 없음).

부서 답변 기반 초안은 확인 완료(ACCEPTED)한 부서 답변만 쓴다. 한국어 번역과 한국어 수정 반영은 같은 입력이면
저장본을 다시 쓰고, 이미 한국어인 초안은 모델을 부르지 않는다. 세 경로 모두 cs_agent만 쓰고 범위 밖은 404다.
"""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.api.cs.reply_assist.routes import (
    CsDeptReplyDraftSettings,
    CsReplyTranslationSettings,
    configure_reply_assist,
)
from backend.engine.cs.classification_call import ClassificationCallError
from backend.shared.access import Principal, principal_header
from backend.shared.ai import Generation, Usage
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
DEPT = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_TRF")
OTHER_DEPT = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="QA_CLAIM")
ANSWER = "펌웨어 4.2.1에서 E-204 오탐이 고쳐졌습니다. 2주 안에 배포 예정입니다."
DRAFT = "Firmware 4.2.1 fixes the false E-204 trip. It will be released within 2 weeks."
DRAFT_KO = "펌웨어 4.2.1에서 E-204 오탐이 고쳐집니다. 2주 안에 배포됩니다."


class FakeModel:
    def __init__(self, *answers: object):
        self.prompts: list[str] = []
        self.answers = list(answers)

    def __call__(self, prompt: str) -> Generation:
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        usage = Usage(input_tokens=300, output_tokens=60, thought_tokens=0, total_tokens=360)
        return Generation(
            json.dumps({"text": answer}, ensure_ascii=False),
            usage,
            "gemini-3.6-flash-001",
            ["STOP"],
            elapsed_ms=500,
        )


def app_for(cs_database, dept=None, translation=None):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    app = create_app(settings)
    configure_reply_assist(
        app,
        CsDeptReplyDraftSettings(_env_file=None, enabled=dept is not None),
        CsReplyTranslationSettings(_env_file=None, enabled=translation is not None),
        dept_provider=dept,
        translation_provider=translation,
    )
    return app


def add_ticket(admin, language="en"):
    ticket_id = uuid4()
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, subject, body) VALUES (%s, 'test', 'kan251', %s, 'synthetic', 'PROVIDED', "
        "now(), now(), 'EMAIL', %s, 'CUST-0001', 'TRF-OIL-1000', 'Breaker trip', "
        "'Our breaker trips with E-204. Mail me at buyer@example.com.')",
        (ticket_id, f"T-{ticket_id}", language),
    )
    return ticket_id


def add_result(admin, ticket_id, status="ACCEPTED", dept="TS_TRF"):
    transfer_id, result_id = uuid4(), uuid4()
    admin.execute(
        "INSERT INTO cs.ticket_transfers (id, ticket_id, round, dept_code, status, reason, "
        "transferred_by, ended_by, ended_at) VALUES (%s, %s, "
        "(SELECT COALESCE(max(round), 0) + 1 FROM cs.ticket_transfers WHERE ticket_id = %s), "
        "%s, 'RESULT_SUBMITTED', '확인 요청', %s, %s, now())",
        (transfer_id, ticket_id, ticket_id, dept, AGENT.user_id, DEPT.user_id),
    )
    reviewed = status != "SUBMITTED"
    admin.execute(
        "INSERT INTO cs.dept_results (id, ticket_id, transfer_id, dept_code, result, "
        "submitted_by, review_status, reviewed_by, reviewed_at) VALUES "
        "(%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END)",
        (
            result_id,
            ticket_id,
            transfer_id,
            dept,
            ANSWER,
            DEPT.user_id,
            status,
            AGENT.user_id if reviewed else None,
            reviewed,
        ),
    )
    return result_id


def path(ticket_id, name):
    return f"/api/v1/cs/tickets/{ticket_id}/reply-draft/{name}"


def rows(admin, ticket_id):
    return (
        admin.cursor(row_factory=dict_row)
        .execute(
            "SELECT kind, outcome, error_code, dept_result_id, total_tokens FROM "
            "cs.reply_draft_assists WHERE ticket_id = %s ORDER BY created_at, id",
            (ticket_id,),
        )
        .fetchall()
    )


def test_an_agent_gets_a_customer_language_draft_from_the_accepted_answer(cs_database, admin):
    """부서 답변이 모델 입력에 들어가고, 같은 답변으로 다시 누르면 모델을 부르지 않는다."""
    ticket_id = add_ticket(admin)
    result_id = add_result(admin, ticket_id)
    model = FakeModel(DRAFT)
    saved = f"{path(ticket_id, 'dept-answer')}?dept_result_id={result_id}"
    with TestClient(app_for(cs_database, dept=model), headers=principal_header(AGENT)) as c:
        before = c.get(saved)
        first = c.post(path(ticket_id, "dept-answer"), json={"dept_result_id": str(result_id)})
        again = c.post(path(ticket_id, "dept-answer"), json={"dept_result_id": str(result_id)})
        after = c.get(saved)
    # 저장본 조회는 모델을 부르지 않는다. 만들기 전에는 null, 만든 뒤에는 그 초안이다.
    assert before.status_code == 200 and before.json()["data"] is None
    assert after.json()["data"]["text"] == DRAFT and after.json()["data"]["reused"] is True
    assert first.status_code == again.status_code == 200, first.text
    data = first.json()["data"]
    assert (data["kind"], data["text"], data["dept_result_id"]) == (
        "DEPT_DRAFT",
        DRAFT,
        str(result_id),
    )
    assert data["provenance"] == "ai_draft" and data["reused"] is False
    assert again.json()["data"]["reused"] is True and len(model.prompts) == 1
    assert ANSWER in model.prompts[0] and "buyer@example.com" not in model.prompts[0]
    assert [r["outcome"] for r in rows(admin, ticket_id)] == ["VALID"]


def test_a_dept_draft_needs_an_accepted_answer_of_this_ticket(cs_database, admin):
    ticket_id = add_ticket(admin)
    submitted = add_result(admin, ticket_id, status="SUBMITTED")
    other = add_result(admin, add_ticket(admin))
    model = FakeModel()
    with TestClient(app_for(cs_database, dept=model), headers=principal_header(AGENT)) as c:
        for result_id in (submitted, other):
            response = c.post(
                path(ticket_id, "dept-answer"), json={"dept_result_id": str(result_id)}
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "CS_DEPT_RESULT_NOT_ACCEPTED"
    assert model.prompts == []


def test_a_draft_that_invents_a_promise_is_stored_as_a_failure_and_returns_502(cs_database, admin):
    ticket_id = add_ticket(admin)
    result_id = add_result(admin, ticket_id)
    model = FakeModel("Your RMA is approved and firmware 4.2.1 fixes E-204.")
    with TestClient(app_for(cs_database, dept=model), headers=principal_header(AGENT)) as c:
        response = c.post(path(ticket_id, "dept-answer"), json={"dept_result_id": str(result_id)})
    assert response.status_code == 502
    assert response.json()["error"]["details"][0]["code"] == "CS_FORBIDDEN_COMMITMENT"
    assert [(r["outcome"], r["error_code"], r["total_tokens"]) for r in rows(admin, ticket_id)] == [
        ("INVALID_OUTPUT", "CS_FORBIDDEN_COMMITMENT", 360)
    ]


def test_translate_then_apply_a_korean_edit(cs_database, admin):
    """번역 저장본이 반영의 기준이다. 같은 초안 번역은 다시 부르지 않고, 반영 결과는 보내지 않는다."""
    ticket_id = add_ticket(admin)
    edited = DRAFT_KO + " 로그를 3일 안에 확인하겠습니다."
    applied = DRAFT + " We will check the log within 3 days."
    model = FakeModel(DRAFT_KO, applied)
    with TestClient(app_for(cs_database, translation=model), headers=principal_header(AGENT)) as c:
        missing = c.post(path(ticket_id, "korean-edit"), json={"text": DRAFT, "edited_ko": edited})
        ko = c.post(path(ticket_id, "korean"), json={"text": DRAFT})
        ko_again = c.post(path(ticket_id, "korean"), json={"text": DRAFT})
        same = c.post(path(ticket_id, "korean-edit"), json={"text": DRAFT, "edited_ko": DRAFT_KO})
        back = c.post(path(ticket_id, "korean-edit"), json={"text": DRAFT, "edited_ko": edited})
    assert missing.json()["error"]["code"] == "CS_REPLY_TRANSLATION_BASE_MISSING"
    assert ko.status_code == 200 and ko.json()["data"]["text"] == DRAFT_KO
    assert ko_again.json()["data"]["reused"] is True
    assert same.json()["error"]["code"] == "CS_REPLY_TRANSLATION_UNCHANGED"
    assert back.status_code == 200, back.text
    data = back.json()["data"]
    assert (data["kind"], data["text"], data["commitment_warning"]) == ("FROM_KO", applied, False)
    # 반영 프롬프트에는 원래 초안·그 번역·고친 한국어가 모두 들어간다.
    assert len(model.prompts) == 2 and all(s in model.prompts[1] for s in (DRAFT, DRAFT_KO, edited))
    assert [r["kind"] for r in rows(admin, ticket_id)] == ["TO_KO", "FROM_KO"]
    sends = admin.execute(
        "SELECT count(*) FROM cs.ticket_customer_sends WHERE ticket_id = %s", (ticket_id,)
    ).fetchone()
    assert sends == (0,)


def test_a_promise_the_agent_wrote_in_korean_comes_back_with_a_warning(cs_database, admin):
    ticket_id = add_ticket(admin)
    model = FakeModel(DRAFT_KO, "We will replace the unit free of charge.")
    with TestClient(app_for(cs_database, translation=model), headers=principal_header(AGENT)) as c:
        c.post(path(ticket_id, "korean"), json={"text": DRAFT})
        back = c.post(
            path(ticket_id, "korean-edit"),
            json={"text": DRAFT, "edited_ko": "무상으로 교체해 드리겠습니다."},
        )
    assert back.status_code == 200 and back.json()["data"]["commitment_warning"] is True


def test_korean_drafts_and_korean_customers_need_no_model_call(cs_database, admin):
    model = FakeModel()
    with TestClient(app_for(cs_database, translation=model), headers=principal_header(AGENT)) as c:
        korean_text = c.post(path(add_ticket(admin), "korean"), json={"text": DRAFT_KO})
        korean_customer = c.post(path(add_ticket(admin, "ko"), "korean"), json={"text": DRAFT})
    for response in (korean_text, korean_customer):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CS_REPLY_TRANSLATION_NOT_NEEDED"
    assert model.prompts == []


def test_a_provider_failure_is_stored_and_returns_502(cs_database, admin):
    ticket_id = add_ticket(admin)
    model = FakeModel(ClassificationCallError("CS_PROVIDER_TIMEOUT"))
    with TestClient(app_for(cs_database, translation=model), headers=principal_header(AGENT)) as c:
        response = c.post(path(ticket_id, "korean"), json={"text": DRAFT})
    assert response.status_code == 502
    assert [(r["outcome"], r["error_code"]) for r in rows(admin, ticket_id)] == [
        ("PROVIDER_ERROR", "CS_PROVIDER_TIMEOUT")
    ]


@pytest.mark.parametrize("name", ["dept-answer", "korean", "korean-edit"])
def test_disabled_features_are_404(cs_database, admin, name):
    ticket_id = add_ticket(admin)
    with TestClient(app_for(cs_database), headers=principal_header(AGENT)) as c:
        body = {"dept_result_id": str(uuid4())} if name == "dept-answer" else {"text": DRAFT}
        if name == "korean-edit":
            body["edited_ko"] = DRAFT_KO
        assert c.post(path(ticket_id, name), json=body).status_code == 404


def test_scope_is_checked_before_the_role(cs_database, admin):
    """이관받은 부서 담당자도 쓸 수 없고(403), 범위 밖 부서 담당자는 문의가 없는 것과 같다(404)."""
    ticket_id = add_ticket(admin)
    add_result(admin, ticket_id)
    app = app_for(cs_database, translation=FakeModel())
    with TestClient(app) as c:
        own = c.post(
            path(ticket_id, "korean"), json={"text": DRAFT}, headers=principal_header(DEPT)
        )
        other = c.post(
            path(ticket_id, "korean"), json={"text": DRAFT}, headers=principal_header(OTHER_DEPT)
        )
    assert (own.status_code, other.status_code) == (403, 404)
