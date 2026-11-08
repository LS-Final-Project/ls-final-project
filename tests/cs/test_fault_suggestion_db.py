"""같은 고장 AI 추천 API(KAN-245) — 실제 PostgreSQL, 가짜 모델(모델 호출 없음).

이 문의를 같은 제품의 알려진(출처 무관) 해결 기록 묶음과 비교한다. 고장 ID·해결 기록 문장은 모델에 가지 않고,
가린 참조(G1..)를 고장 ID로 되돌려 준다. 비교할 묶음이 없으면 모델을 부르지 않는다. cs_agent만 쓴다.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.faults.suggestion import CsFaultSuggestionSettings, configure_fault_suggestion
from backend.api.cs.main import create_app
from backend.engine.cs.classification_call import ClassificationCallError
from backend.shared.access import Principal, principal_header
from backend.shared.ai import Generation, Usage
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
CS_MANAGER = Principal(
    user_id=uuid4(), roles={"manager"}, managed_departments=frozenset({"GCS_L1"})
)
PRODUCT = "TRF-OIL-1000"
START = datetime(2026, 1, 1, tzinfo=UTC)


def reply(decision="EXISTING", ref="G1", quote="oil is leaking"):
    return json.dumps(
        {"decision": decision, "group_ref": ref, "evidence_quote": quote, "reason": "같은 증상"}
    )


class FakeModel:
    def __init__(self):
        self.prompts: list[str] = []
        self.answers: list[object] = [reply()]

    def __call__(self, prompt: str) -> Generation:
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        usage = Usage(input_tokens=500, output_tokens=40, thought_tokens=0, total_tokens=540)
        return Generation(str(answer), usage, "gemini-3.6-flash-001", ["STOP"], elapsed_ms=800)


@pytest.fixture
def model():
    return FakeModel()


def app_for(cs_database, model, enabled=True):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    app = create_app(settings)
    configure_fault_suggestion(app, CsFaultSuggestionSettings(enabled=enabled), provider=model)
    return app


@pytest.fixture
def client(cs_database, model):
    with TestClient(app_for(cs_database, model), headers=principal_header(AGENT)) as c:
        yield c


def add_ticket(admin, tag, body, *, origin="synthetic", product=PRODUCT, at=START):
    ticket_id = uuid4()
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, subject, body) VALUES (%s, 'test', %s, %s, %s, 'PROVIDED', %s, %s, "
        "'EMAIL', 'en', 'CUST-0001', %s, NULL, %s)",
        (ticket_id, f"kan245-{tag}", f"T-{ticket_id}", origin, at, at, product, body),
    )
    return ticket_id


def add_case(admin, tag, fault, body, *, origin="synthetic", product=PRODUCT, at=START):
    ticket_id = add_ticket(admin, tag, body, origin=origin, product=product, at=at)
    case_id = uuid4()
    admin.execute(
        "INSERT INTO cs.resolution_cases (id, ticket_id, source, source_instance, external_id, "
        "data_origin, derivation_kind, resolved_at, known_at, product_code, fault_id, symptom, "
        "root_cause, root_cause_type, action_taken, onsite_visit, resolved_by_dept) VALUES "
        "(%s, %s, 'test', %s, %s, %s, 'PROVIDED', %s, %s, %s, %s, 'SECRET-SYMPTOM', "
        "'SECRET-CAUSE', 'HARDWARE', 'SECRET-ACTION', false, 'QA_CLAIM')",
        (case_id, ticket_id, f"kan245-{tag}", f"RC-{case_id}", origin, at, at, product, fault),
    )
    return ticket_id


def path(ticket_id):
    return f"/api/v1/cs/tickets/{ticket_id}/fault-suggestion"


def seeded(admin):
    """시험마다 새 제품 코드를 써서 다른 시험(같은 DB)의 해결 기록과 섞이지 않게 한다."""
    tag = uuid4().hex[:8]
    product = f"KAN245-{tag}"
    day = timedelta(days=1)
    add_case(admin, tag, "FLT-067", "Oil leaking from the gasket.", product=product, at=START)
    add_case(admin, tag, "FLT-067", "Gasket oil leak again.", product=product, at=START + day)
    add_case(admin, tag, "FLT-067", "Third gasket leak.", product=product, at=START + 2 * day)
    add_case(
        admin,
        tag,
        "FLT-065",
        "Buchholz alarm A.BH1 after filling.",
        product=product,
        at=START + 3 * day,
    )
    # 다른 출처(real)의 기록도 비교에 들어간다(화면 등록 문의도 제공 이력과 비교). 다른 제품은 빠진다.
    add_case(
        admin,
        tag,
        "FLT-066",
        "Real-origin Buchholz trip.",
        origin="real",
        product=product,
        at=START + 4 * day,
    )
    add_case(admin, tag, "FLT-001", "Other product.", product=f"{product}-X", at=START)
    target = add_ticket(
        admin,
        tag,
        "Our transformer oil is leaking. Mail me at buyer@example.com.",
        product=product,
        at=START,
    )
    return target


def test_an_agent_gets_the_existing_fault_back_from_the_hidden_reference(client, model, admin):
    """G1(처음 알려진 FLT-067)을 고장 ID로 되돌리고, 모델에는 고장 ID·해결 문장·이메일이 가지 않는다."""
    target = seeded(admin)
    response = client.post(path(target))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["decision"], data["fault_id"], data["matched_cases"]) == ("EXISTING", "FLT-067", 3)
    assert data["fault_symptom"] == "Oil leakage at gasket"
    assert (data["compared_faults"], data["compared_cases"]) == (3, 5)
    assert data["evidence_quote"] == "oil is leaking" and data["provenance"] == "ai_draft"
    assert data["model"] == "gemini-3.6-flash" and data["prompt_version"] == "fault-match-v2"
    [prompt] = model.prompts
    assert "Real-origin Buchholz trip." in prompt and '"ref": "G3"' in prompt
    for hidden in ("FLT-", "SECRET-", "buyer@example.com", "Other product"):
        assert hidden not in prompt
    # 묶음마다 이전 문의 예시는 2건까지(세 번째 누유 문의는 빠진다).
    assert "Oil leaking from the gasket." in prompt and "Third gasket leak." not in prompt
    assert '"ref": "G4"' not in prompt


def test_new_and_unsure_carry_no_fault(client, model, admin):
    target = seeded(admin)
    for decision in ("NEW", "UNSURE"):
        model.answers = [reply(decision, None, None)]
        data = client.post(path(target)).json()["data"]
        assert (data["decision"], data["fault_id"], data["matched_cases"]) == (decision, None, 0)


def test_with_no_known_fault_of_the_product_the_model_is_not_called(client, model, admin):
    tag = uuid4().hex[:8]
    target = add_ticket(admin, tag, "Transformer noise.", product=f"KAN245-{tag}")
    data = client.post(path(target)).json()["data"]
    assert (data["decision"], data["fault_id"], data["model"]) == ("NEW", None, None)
    assert data["compared_faults"] == 0 and model.prompts == []


def test_a_bad_answer_is_retried_once_then_502(client, model, admin):
    """규칙 밖 응답(없는 참조)이나 호출 실패는 한 번 더 부르고, 두 번 다 안 되면 502다."""
    target = seeded(admin)
    model.answers = [reply("EXISTING", "G9"), reply()]
    ok = client.post(path(target))
    assert ok.status_code == 200 and len(model.prompts) == 2
    model.prompts.clear()
    model.answers = [ClassificationCallError("CS_PROVIDER_TIMEOUT"), reply("NEW", "G1")]
    failed = client.post(path(target))
    assert failed.status_code == 502 and len(model.prompts) == 2
    assert failed.json()["error"]["code"] == "CS_FAULT_SUGGESTION_FAILED"


def test_only_a_cs_agent_may_ask_and_the_route_is_off_by_default(cs_database, model, admin):
    target = seeded(admin)
    with TestClient(app_for(cs_database, model), headers=principal_header(CS_MANAGER)) as c:
        assert c.post(path(target)).status_code == 403
    with TestClient(
        app_for(cs_database, model, enabled=False), headers=principal_header(AGENT)
    ) as c:
        assert c.post(path(target)).status_code == 404
    assert model.prompts == []
