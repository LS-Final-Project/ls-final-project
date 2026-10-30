"""KAN-189 PR-B 경로 15·16·18·19·20을 HTTP로, 실제 마이그레이션의 전용 PostgreSQL에 tft_app으로 확인한다.

행은 모두 합성이다. 프로젝트·요구 준비는 tests/tft/test_rfp_http_base_db.py의 고정물을 쓴다.
"""

# ruff: noqa: F811

import asyncio
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from backend.api.tft.main import create_app
from backend.api.tft.rfp import commands
from backend.shared.access import Principal, principal_header
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

# 고정물을 가져와 쓴다(pytest가 이름으로 찾는다).
from tests.tft.test_rfp_http_base_db import SALES, admin, app_dsn, chain  # noqa: F401

RND = Principal(user_id=uuid4(), roles=frozenset({"dept_staff"}), dept="RND_FW")
TECH = Principal(user_id=uuid4(), roles=frozenset({"dept_staff"}), dept="TS_TRF")
OUTSIDER = Principal(user_id=uuid4(), roles=frozenset({"sales"}))
OWNER = Principal(user_id=SALES, roles=frozenset({"sales"}))
NOW = "2026-10-30T09:00:00+09:00"


@pytest.fixture
def client(app_dsn):
    return TestClient(
        create_app(Settings(_env_file=None, environment="test", database_url=app_dsn))
    )


@pytest.fixture
def gap(admin, chain):
    """요구를 정격 부족(RATING)·외부 허용으로 두고, 외부 제품 후보와 미해결 항목 하나를 저장한다."""
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET equipment='변압기', attribute='BIL', value='95', "
            "unit='kV', unresolved='[\"BIL 시험 조건\"]' WHERE requirement_id=%s",
            (chain["requirement"],),
        )
        conn.execute(
            "INSERT INTO tft.supplement_options (id, requirement_id, requirement_version, "
            "deficiency_cause, customer_permission, path, allowed, candidate_codes, data_origin, "
            "derivation_kind, known_at) VALUES (%s,%s,1,'RATING','ALLOWED','EXTERNAL_PRODUCT',true,"
            "'{P-A}','synthetic','DERIVED',now())",
            (uuid4(), chain["requirement"]),
        )
    return chain


def call(client, method, path, who, body=None, key=None):
    headers = principal_header(who)
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.request(method, "/api/v1/tft" + path, json=body, headers=headers)


def ask(client, chain, who=OWNER, **extra):
    body = {
        "requirement_id": str(chain["requirement"]),
        "origin": {"clause_id": str(chain["clause"]), "page": None},
        "issue_type": "RND_FEASIBILITY",
        "question_text": "BIL 95 kV 개발 가능 여부",
        "target": "INTERNAL",
        "target_dept": "RND_FW",
        **extra,
    }
    return call(client, "POST", f"/projects/{chain['project']}/questions", who, body, uuid4().hex)


def answer(client, question, who, **extra):
    body = {"version": 1, "reply_text": "확인했습니다", "received_at": NOW, **extra}
    return call(
        client, "POST", f"/questions/{question['question_id']}/reply", who, body, uuid4().hex
    )


def choose(client, chain, path, key=None, **extra):
    body = {"version": 1, "path": path, "candidate_codes": [], "role_split": "역할 분담", **extra}
    url = f"/requirements/{chain['requirement']}/supplement-selection"
    return call(client, "POST", url, OWNER, body, key or uuid4().hex)


def options(client, chain):
    response = call(
        client, "GET", f"/requirements/{chain['requirement']}/supplement-options", OWNER
    )
    assert response.status_code == 200, response.text
    return {o["path"]: o for o in response.json()["data"]["options"]}


def error(response):
    return response.status_code, response.json()["error"]["code"]


def test_internal_questions_are_created_for_review_departments_only(client, chain):
    """19 INTERNAL 질문 201·18 필터, 검토 부서 밖 대상은 422, 범위 밖 사용자는 404."""
    created = ask(client, chain, who=TECH)
    assert created.status_code == 201, created.text
    question = created.json()["data"]
    assert (question["status"], question["target_dept"], question["version"]) == (
        "OPEN",
        "RND_FW",
        1,
    )
    listed = call(
        client,
        "GET",
        f"/projects/{chain['project']}/questions?target=INTERNAL&target_dept=RND_FW",
        RND,
    )
    assert [q["question_id"] for q in listed.json()["data"]["items"]] == [question["question_id"]]
    empty = call(client, "GET", f"/projects/{chain['project']}/questions?target=CUSTOMER", RND)
    assert empty.json()["data"]["items"] == []
    outside = ask(client, chain, target_dept="CERT")
    assert error(outside) == (422, "VALIDATION_ERROR")
    assert outside.json()["error"]["details"][0]["field"] == "target_dept"
    assert error(ask(client, chain, who=OUTSIDER)) == (404, "TFT_PROJECT_NOT_FOUND")
    foreign = ask(client, chain, origin={"clause_id": str(uuid4()), "page": None})
    assert error(foreign) == (422, "VALIDATION_ERROR")


def test_rnd_answer_fields_follow_the_target_department(client, gap):
    """R&D 칸은 R&D 부서 대상 내부 질문에만, 회신은 대상 부서 dept_staff가 하고 제안은 만들지 않는다."""
    tech_question = ask(client, gap, issue_type="TECH_ADVICE", target_dept="TS_TRF").json()["data"]
    refused = answer(client, tech_question, TECH, rnd_feasibility="FEASIBLE")
    assert error(refused) == (422, "TFT_QUESTION_FIELD_NOT_ALLOWED")
    customer = ask(
        client, gap, issue_type="EMPTY_VALUE", target="CUSTOMER", target_dept=None
    ).json()["data"]
    assert error(answer(client, customer, OWNER, rnd_feasibility="FEASIBLE")) == (
        422,
        "TFT_QUESTION_FIELD_NOT_ALLOWED",
    )
    # 본문에 미해결 항목이 들어 있어도 INTERNAL 회신은 제안을 만들지 않는다.
    rnd_question = ask(client, gap, question_text="BIL 시험 조건과 개발 가능 여부").json()["data"]
    assert answer(client, rnd_question, TECH, rnd_feasibility="FEASIBLE").status_code == 403
    replied = answer(client, rnd_question, RND, rnd_feasibility="NOT_FEASIBLE")
    assert replied.status_code == 200, replied.text
    data = replied.json()["data"]
    assert (data["status"], data["version"], data["rnd_feasibility"]) == (
        "REPLIED",
        2,
        "NOT_FEASIBLE",
    )
    assert data["created_proposal"] is None


def test_customer_reply_proposes_dropping_the_answered_unresolved_item(client, gap, admin):
    """CUSTOMER 회신은 본문에 든 미해결 항목만 뺀 제안을 만들고, 맞는 항목이 없으면 제안이 없다."""
    asked = ask(
        client,
        gap,
        issue_type="CONDITION_MISSING",
        target="CUSTOMER",
        target_dept=None,
        question_text="BIL 시험 조건을 알려 주십시오",
    ).json()["data"]
    data = answer(client, asked, OWNER).json()["data"]
    proposal = data["created_proposal"]
    assert (proposal["origin"], proposal["status"], proposal["proposer_kind"]) == (
        "customer_reply",
        "PENDING",
        "system",
    )
    assert proposal["proposer_user_id"] is None
    assert proposal["rationale"] == "회신 내용 미검증(담당자 확인 필요). 고객 회신: 확인했습니다"
    assert proposal["source_ref"]["question_id"] == asked["question_id"]
    assert proposal["current"]["unresolved"] == ["BIL 시험 조건"]
    assert proposal["proposed"] == {**proposal["current"], "unresolved": []}
    other = ask(
        client, gap, issue_type="SCOPE_UNCLEAR", target="CUSTOMER", target_dept=None
    ).json()["data"]
    assert answer(client, other, OWNER).json()["data"]["created_proposal"] is None
    with psycopg.connect(admin) as conn:
        # 승인 전에는 요구가 바뀌지 않는다.
        assert conn.execute(
            "SELECT latest_version FROM tft.requirements WHERE id=%s", (gap["requirement"],)
        ).fetchone() == (1,)


def test_own_development_opens_an_rnd_question_and_gates_partner_paths(client, gap):
    """OWN_DEVELOPMENT 선택은 R&D 질문을 만들고 협력사 경로 게이트가 PENDING이 된다. dept_staff는 고를 수 없다."""
    before = options(client, gap)
    assert before["EXTERNAL_PRODUCT"]["candidate_codes"] == ["P-A"]
    assert before["EXTERNAL_PRODUCT"]["gate"] == {
        "requires": "OWN_DEVELOPMENT_CHECK",
        "status": "NOT_REQUIRED",
    }
    staff = call(
        client,
        "POST",
        f"/requirements/{gap['requirement']}/supplement-selection",
        RND,
        {"version": 1, "path": "OWN_DEVELOPMENT", "role_split": "자사"},
        uuid4().hex,
    )
    assert staff.status_code == 403
    chosen = choose(client, gap, "OWN_DEVELOPMENT")
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["data"]["path"] == "OWN_DEVELOPMENT"
    questions = call(client, "GET", f"/projects/{gap['project']}/questions?target=INTERNAL", OWNER)
    items = questions.json()["data"]["items"]
    assert [(q["target_dept"], q["issue_type"], q["status"]) for q in items] == [
        ("RND_FW", "RND_FEASIBILITY", "OPEN")
    ]
    assert "2.5.C" in items[0]["question_text"] and "BIL 95 kV" in items[0]["question_text"]
    assert options(client, gap)["EXTERNAL_PRODUCT"]["gate"]["status"] == "PENDING"
    # 열린 R&D 질문이 있으면 다시 골라도 질문을 더 만들지 않는다.
    assert choose(client, gap, "OWN_DEVELOPMENT").status_code == 200
    again = call(client, "GET", f"/projects/{gap['project']}/questions?target=INTERNAL", OWNER)
    assert len(again.json()["data"]["items"]) == 1


def test_partner_path_while_rnd_is_pending_needs_a_waiver(client, gap, admin):
    """열린 R&D 질문 중 협력사 경로는 사유 없이 422(질문 ID 안내), 사유가 있으면 WAIVED로 저장한다."""
    pending = ask(client, gap).json()["data"]
    refused = choose(client, gap, "EXTERNAL_PRODUCT")
    assert error(refused) == (422, "TFT_RND_CHECK_REQUIRED")
    assert refused.json()["error"]["details"] == [
        {"field": "rnd_question_id", "reason": pending["question_id"]}
    ]
    waived = choose(
        client,
        gap,
        "EXTERNAL_PRODUCT",
        candidate_codes=["P-A"],
        rnd_check_waiver_reason="R&D 회신 지연으로 협력사 경로를 먼저 검토합니다",
    )
    assert waived.status_code == 200, waived.text
    with psycopg.connect(admin) as conn:
        assert conn.execute(
            "SELECT rnd_check, rnd_check_waiver_reason IS NOT NULL FROM tft.supplement_selections "
            "WHERE requirement_id=%s",
            (gap["requirement"],),
        ).fetchall() == [("WAIVED", True)]
    assert options(client, gap)["EXTERNAL_PRODUCT"]["gate"]["status"] == "WAIVED"


def test_feasible_rnd_answer_blocks_partner_paths(client, gap):
    """R&D가 개발 가능이라고 답하면 협력사 경로는 막히고 고르면 409다."""
    question = ask(client, gap).json()["data"]
    assert answer(client, question, RND, rnd_feasibility="FEASIBLE").status_code == 200
    external = options(client, gap)["EXTERNAL_PRODUCT"]
    assert (external["allowed"], external["blocked_reason"]) == (False, "OWN_DEVELOPMENT_FEASIBLE")
    assert error(choose(client, gap, "EXTERNAL_PRODUCT")) == (409, "TFT_INVALID_TRANSITION")


def test_requests_replay_by_key_and_concurrent_keys_conflict(client, gap, admin, app_dsn):
    """같은 키·같은 요청은 첫 결과 재생(판본이 바뀌어도), 다른 요청은 409, 동시에 같은 키가 기록되면 409다."""
    key = "select-" + uuid4().hex
    first = choose(client, gap, "OTHER_OWN_PRODUCT", key=key)
    assert first.status_code == 200, first.text
    again = choose(client, gap, "OTHER_OWN_PRODUCT", key=key)
    assert again.json()["data"] == first.json()["data"]
    assert error(choose(client, gap, "OWN_DEVELOPMENT", key=key)) == (409, "IDEMPOTENCY_CONFLICT")
    with psycopg.connect(admin) as conn:
        assert conn.execute(
            "SELECT count(*) FROM tft.supplement_selections WHERE requirement_id=%s",
            (gap["requirement"],),
        ).fetchone() == (1,)

    actor, race = uuid4(), "race-" + uuid4().hex

    async def main():
        async with await psycopg.AsyncConnection.connect(app_dsn, row_factory=dict_row) as conn:

            async def work():
                # 다른 요청이 같은 키를 먼저 기록하고 커밋했다.
                with psycopg.connect(admin, autocommit=True) as other:
                    other.execute(
                        "INSERT INTO tft.rfp_commands (actor_user_id, operation, idempotency_key, "
                        "project_id, request_hash, status_code, result) "
                        "VALUES (%s,'question.create',%s,%s,%s,201,'{}')",
                        (actor, race, gap["project"], "0" * 64),
                    )
                return {"question_id": "q"}

            await commands.run_command(
                conn,
                actor=actor,
                operation="question.create",
                key=race,
                digest="0" * 64,
                project_id=gap["project"],
                status_code=201,
                work=work,
            )

    with pytest.raises(ApplicationError) as conflict:
        asyncio.run(main())
    assert (conflict.value.status_code, conflict.value.code) == (409, "IDEMPOTENCY_CONFLICT")
