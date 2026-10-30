"""KAN-189 PR-B 경로 15·16·18·19·20을 HTTP로, 실제 마이그레이션의 전용 PostgreSQL에 tft_app으로 확인한다.

행은 모두 합성이다. 프로젝트·요구 준비는 tests/tft/test_rfp_http_base_db.py의 고정물을 쓴다.
"""

# ruff: noqa: F811

import asyncio
import hashlib
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.api.tft.main import create_app
from backend.api.tft.partners.loader import load_profiles
from backend.api.tft.rfp import commands, repository
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
    assert audit_actions(admin, gap["project"]).count("proposal.create") == 1
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


def test_own_development_opens_an_rnd_question_and_gates_partner_paths(client, gap, admin):
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
    # 자동 질문도 쓰기다. 선택 감사와 따로 남는다.
    assert audit_actions(admin, gap["project"]).count("question.create") == 1
    assert audit_actions(admin, gap["project"]).count("supplement.select") == 1
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


def draft(admin, chain, **extra):
    """처리 함수가 넣는 것과 같은 고객 질문 초안 한 행(DRAFT, 요구 연결)."""
    question_id = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.customer_questions (id, project_id, requirement_id, clause_id, "
            "issue_type, question_text, status, target, draft_key, data_origin, derivation_kind, "
            "known_at, created_by) VALUES (%s,%s,%s,%s,'EMPTY_VALUE','값을 알려 주세요','DRAFT',"
            "'CUSTOMER',%s,'synthetic','DERIVED',now(),%s)",
            (
                question_id,
                chain["project"],
                chain["requirement"],
                chain["clause"],
                uuid4().hex + uuid4().hex,
                OWNER.user_id,
            ),
        )
    return question_id


def customer_question_seen(admin, chain):
    async def read():
        async with await psycopg.AsyncConnection.connect(admin, row_factory=dict_row) as conn:
            return await repository.has_customer_question(conn, chain["requirement"])

    return asyncio.run(read())


def test_a_draft_is_not_a_sent_question_until_a_person_confirms_it(client, admin, chain):
    """경로 31(KAN-199): 초안은 목록에 상태로 구분되고 has_customer_question은 거짓이다. 확인하면 OPEN·참이다."""
    question_id = draft(admin, chain)
    url = f"/projects/{chain['project']}/questions"
    listed = call(client, "GET", f"{url}?status=DRAFT", OWNER).json()["data"]["items"]
    assert [q["question_id"] for q in listed] == [str(question_id)]
    assert call(client, "GET", f"{url}?status=OPEN", OWNER).json()["data"]["items"] == []
    assert customer_question_seen(admin, chain) is False
    # 초안에는 회신을 기록할 수 없다.
    refused = answer(client, {"question_id": question_id}, OWNER)
    assert refused.status_code == 409

    key = uuid4().hex
    confirmed = call(client, "POST", f"{url}/{question_id}/confirm", OWNER, None, key)
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()["data"]
    assert (body["status"], body["version"]) == ("OPEN", 2)
    assert customer_question_seen(admin, chain) is True
    # 같은 키는 첫 결과를 다시 준다. 새 키로 다시 부르면 이미 확인된 질문이라 409다.
    again = call(client, "POST", f"{url}/{question_id}/confirm", OWNER, None, key)
    assert (again.status_code, again.json()["data"]["version"]) == (200, 2)
    assert error(
        call(client, "POST", f"{url}/{question_id}/confirm", OWNER, None, uuid4().hex)
    ) == (
        409,
        "TFT_VERSION_CONFLICT",
    )
    assert (
        call(client, "GET", f"{url}?status=OPEN", OWNER).json()["data"]["items"][0]["status"]
        == "OPEN"
    )


def test_confirm_follows_question_create_permission_and_project_scope(client, admin, chain):
    """확인 권한은 질문 작성과 같다. 다른 프로젝트 경로로는 질문이 없는 것과 같다."""
    question_id = draft(admin, chain)
    url = f"/projects/{chain['project']}/questions/{question_id}/confirm"
    # 볼 수 있지만 작성 권한이 없는 관리자는 403, 범위 밖 사용자는 없는 것과 같다.
    manager = Principal(
        user_id=uuid4(), roles=frozenset({"manager"}), managed_projects={chain["project"]}
    )
    assert call(client, "POST", url, manager, None, uuid4().hex).status_code == 403
    stranger = Principal(user_id=uuid4(), roles=frozenset({"sales"}))
    assert error(call(client, "POST", url, stranger, None, uuid4().hex)) == (
        404,
        "TFT_PROJECT_NOT_FOUND",
    )
    assert call(client, "POST", url, OWNER).status_code == 422  # Idempotency-Key 필수.
    # 내가 담당인 다른 프로젝트의 경로로는 이 질문을 확인할 수 없다.
    other = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.projects (id, source, source_instance, data_origin, derivation_kind, "
            "known_at, company_code, name, sales_user_id) "
            "VALUES (%s,'test','kan199','synthetic','PROVIDED',now(),'HOST','다른 프로젝트',%s)",
            (other, SALES),
        )
    elsewhere = f"/projects/{other}/questions/{question_id}/confirm"
    assert error(call(client, "POST", elsewhere, OWNER, None, uuid4().hex)) == (
        404,
        "TFT_QUESTION_NOT_FOUND",
    )
    missing = f"/projects/{chain['project']}/questions/{uuid4()}/confirm"
    assert error(call(client, "POST", missing, OWNER, None, uuid4().hex)) == (
        404,
        "TFT_QUESTION_NOT_FOUND",
    )


# --- 읽기 경로(KAN-188) ---------------------------------------------------------------------

STRANGER = Principal(user_id=uuid4(), roles=frozenset({"sales"}))


def audit_actions(admin, project):
    with psycopg.connect(admin) as conn:
        rows = conn.execute(
            "SELECT action FROM tft.rfp_audit_events WHERE project_id=%s", (project,)
        ).fetchall()
    return [row[0] for row in rows]


def get(client, path, who=OWNER):
    return call(client, "GET", path, who)


def data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def add_clause(admin, chain, clause_no, ordinal, **extra):
    """조항 하나를 더한다.

    Args:
        admin: 관리자 DSN.
        chain: 합성 프로젝트.
        clause_no: 조항 번호.
        ordinal: 문서 순서.
        extra: parent_clause_id·cross_references.

    Returns:
        UUID: 조항 ID.
    """
    clause_id = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.clauses (id, document_version_id, clause_no, parent_clause_id, depth, "
            "ordinal, text, cross_references, data_origin, derivation_kind, known_at) "
            "VALUES (%s,%s,%s,%s,3,%s,'본문',%s,'synthetic','DERIVED',now())",
            (
                clause_id,
                chain["docver"],
                clause_no,
                extra.get("parent_clause_id"),
                ordinal,
                Jsonb(extra.get("cross_references", [])),
            ),
        )
    return clause_id


def add_requirement(admin, chain, code, **columns):
    """최신 판본 1의 요구 하나를 더한다.

    Args:
        admin: 관리자 DSN.
        chain: 합성 프로젝트.
        code: 요구 코드.
        columns: requirement_versions 열 값.

    Returns:
        UUID: 요구 ID.
    """
    requirement_id = uuid4()
    row = {
        "quote": code,
        "operator": "EQ",
        "standard_system": "NONE",
        "necessity": "MANDATORY",
        "status": "EVIDENCE_FOUND",
        "reason": "근거 있음",
        **columns,
    }
    names = ", ".join(row)
    slots = ", ".join(["%s"] * len(row))
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.requirements (id, project_id, code, data_origin, derivation_kind, "
            "known_at) VALUES (%s,%s,%s,'synthetic','DERIVED',now())",
            (requirement_id, chain["project"], code),
        )
        conn.execute(
            f"INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, "
            f"clause_id, provenance_kind, data_origin, derivation_kind, known_at, {names}) "
            f"VALUES (%s,1,%s,%s,'ai_draft','synthetic','DERIVED',now(),{slots})",
            (requirement_id, chain["docver"], chain["clause"], *row.values()),
        )
    return requirement_id


def test_every_read_route_hides_other_projects_as_not_found(client, chain):
    """범위 밖 사용자에게는 모든 읽기 경로가 없는 것과 같다."""
    project, document, requirement = chain["project"], chain["document"], chain["requirement"]
    for path, code in (
        (f"/projects/{project}/documents", "TFT_PROJECT_NOT_FOUND"),
        (f"/projects/{project}/requirements", "TFT_PROJECT_NOT_FOUND"),
        (f"/projects/{project}/partner-candidates", "TFT_PROJECT_NOT_FOUND"),
        (f"/documents/{document}", "TFT_DOCUMENT_NOT_FOUND"),
        (f"/documents/{document}/clauses", "TFT_DOCUMENT_NOT_FOUND"),
        (f"/documents/{document}/file", "TFT_DOCUMENT_NOT_FOUND"),
        (f"/requirements/{requirement}", "TFT_REQUIREMENT_NOT_FOUND"),
        (f"/requirements/{requirement}/comparison", "TFT_REQUIREMENT_NOT_FOUND"),
    ):
        assert error(get(client, path, STRANGER)) == (404, code), path
    assert error(get(client, f"/documents/{uuid4()}")) == (404, "TFT_DOCUMENT_NOT_FOUND")


def test_documents_list_versions_and_detail_uses_the_latest(client, admin, chain):
    """목록은 판본 전부(새 판본 먼저), 상세는 최신 판본과 조항·삭제 값 수다."""
    newer = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.document_versions (id, document_id, version, mime, storage_key, sha256, "
            "size, edit_trace_support, data_origin, derivation_kind, known_at, uploaded_by) "
            "VALUES (%s,%s,2,'application/pdf','k2',%s,20,false,'synthetic','PROVIDED',now(),%s)",
            (newer, chain["document"], "1" * 64, uuid4()),
        )
        conn.execute("UPDATE tft.documents SET latest_version=2 WHERE id=%s", (chain["document"],))
    items = data(get(client, f"/projects/{chain['project']}/documents"))["items"]
    assert [i["document_version_id"] for i in items] == [str(newer), str(chain["docver"])]
    detail = data(get(client, f"/documents/{chain['document']}"))
    assert (detail["document_version_id"], detail["version"]) == (str(newer), 2)
    assert (detail["clauses_count"], detail["edit_trace_support"]) == (0, False)
    with psycopg.connect(admin) as conn:
        conn.execute("UPDATE tft.documents SET latest_version=1 WHERE id=%s", (chain["document"],))
        conn.execute(
            "INSERT INTO tft.clause_deleted_values (clause_id, ordinal, text, position) "
            "VALUES (%s,0,'33',4)",
            (chain["clause"],),
        )
    old = data(get(client, f"/documents/{chain['document']}"))
    assert (old["clauses_count"], old["deleted_values_count"]) == (1, 1)


def test_clauses_are_paged_in_document_order_with_their_links(client, admin, chain):
    """조항: 문서 순서·쪽 나눔·아래 조항 필터·삭제 값·표시·교차 참조·연결 요구."""
    second = add_clause(
        admin,
        chain,
        "2.5.D",
        1,
        parent_clause_id=chain["clause"],
        cross_references=[
            {"target_hint": "2.5.C", "resolved": True},
            {"target_hint": "2.9", "resolved": False},
        ],
    )
    add_clause(admin, chain, "2.5.E", 2, parent_clause_id=chain["clause"])
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.clause_deleted_values (clause_id, ordinal, text, position) "
            "VALUES (%s,0,'33',4)",
            (chain["clause"],),
        )
        conn.execute(
            "INSERT INTO tft.clause_flags (clause_id, flag) VALUES (%s,'HIGHLIGHT')",
            (chain["clause"],),
        )
    url = f"/documents/{chain['document']}/clauses"
    first = data(get(client, f"{url}?limit=2"))
    assert [c["clause_no"] for c in first["items"]] == ["2.5.C", "2.5.D"]
    top, child = first["items"]
    assert top["deleted_values"] == [{"text": "33", "position": 4}]
    assert top["flags"] == ["HIGHLIGHT"]
    assert top["requirement_ids"] == [str(chain["requirement"])]
    assert child["cross_references"] == [
        {"target": "2.5.C", "resolved_clause_id": str(chain["clause"])},
        {"target": "2.9", "resolved_clause_id": None},
    ]
    rest = data(get(client, f"{url}?limit=2&cursor={first['next_cursor']}"))
    assert [c["clause_no"] for c in rest["items"]] == ["2.5.E"] and rest["next_cursor"] is None
    children = data(get(client, f"{url}?parent={chain['clause']}"))
    assert [c["clause_id"] for c in children["items"]][0] == str(second)
    assert len(children["items"]) == 2
    assert error(get(client, f"{url}?cursor=not-a-cursor")) == (400, "TFT_INVALID_CURSOR")


def test_the_file_is_a_checked_attachment_or_unavailable(
    client, admin, chain, tmp_path, monkeypatch
):
    """원문: 저장 해시가 맞으면 내려받기, 설정이 없거나 다르면 경로 없이 503이다."""
    body = b"synthetic document"
    (tmp_path / "doc.bin").write_bytes(body)
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.document_versions SET storage_key='doc.bin', sha256=%s, size=%s, "
            "mime='text/html' WHERE id=%s",
            (hashlib.sha256(body).hexdigest(), len(body), chain["docver"]),
        )
    url = f"/documents/{chain['document']}/file"
    monkeypatch.delenv("TFT_DOCUMENT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    unavailable = get(client, url)
    assert error(unavailable) == (503, "TFT_DOCUMENT_UNAVAILABLE")
    monkeypatch.setenv("TFT_DOCUMENT_ROOT", str(tmp_path))
    done = get(client, url)
    assert (done.status_code, done.content) == (200, body)
    # 저장 mime이 HTML이어도 화면에서 열리지 않고 내려받는다.
    assert done.headers["content-disposition"].startswith("attachment;")
    assert done.headers["x-content-type-options"] == "nosniff"
    # permissions.md 1.1·4.2: manager는 역할만으로 받지 못하고 관리 대상 프로젝트 안에서만 받는다.
    managing = Principal(
        user_id=uuid4(), roles=frozenset({"manager"}), managed_projects={chain["project"]}
    )
    assert get(client, url, managing).status_code == 200
    other_manager = Principal(
        user_id=uuid4(), roles=frozenset({"manager"}), managed_projects={uuid4()}
    )
    assert error(get(client, url, other_manager)) == (404, "TFT_DOCUMENT_NOT_FOUND")
    bare_manager = Principal(user_id=uuid4(), roles=frozenset({"manager"}))
    assert error(get(client, url, bare_manager)) == (404, "TFT_DOCUMENT_NOT_FOUND")
    (tmp_path / "doc.bin").write_bytes(b"changed document!!")
    changed = get(client, url)
    assert error(changed) == (503, "TFT_DOCUMENT_UNAVAILABLE")
    assert "doc.bin" not in changed.text and str(tmp_path) not in changed.text


def test_requirements_filter_and_page_in_sql(client, admin, chain):
    """요구 목록: 코드 순 쪽 나눔, 필터는 쪽 안에서가 아니라 전체에 걸리고, 시스템 표시가 보인다."""
    review = add_requirement(admin, chain, "R-2", needs_review=True, equipment="변압기")
    asked = add_requirement(admin, chain, "R-3", status="SPEC_GAP", value="95", unit="kV")
    add_requirement(admin, chain, "R-4", task="FACTORY_TEST", designated_model="SEL-751A")
    draft(admin, {**chain, "requirement": review})
    ask(client, {**chain, "requirement": asked})
    url = f"/projects/{chain['project']}/requirements"
    first = data(get(client, f"{url}?limit=2"))
    assert [i["code"] for i in first["items"]] == ["R-1", "R-2"]
    second = data(get(client, f"{url}?limit=2&cursor={first['next_cursor']}"))
    assert [i["code"] for i in second["items"]] == ["R-3", "R-4"]
    assert second["next_cursor"] is None
    by_code = {i["code"]: i for i in (*first["items"], *second["items"])}
    assert by_code["R-2"]["needs_review"] is True and by_code["R-1"]["needs_review"] is False
    # 초안은 열린 질문이 아니다.
    assert (by_code["R-2"]["open_questions"], by_code["R-3"]["open_questions"]) == (0, 1)
    assert by_code["R-3"]["value_text"] == "95 kV"
    assert by_code["R-4"]["is_designated_item"] is True

    def codes(query):
        return [i["code"] for i in data(get(client, f"{url}?{query}"))["items"]]

    # 첫 쪽 밖의 행도 찾는다(limit=1이어도 조건에 맞는 첫 행).
    assert codes("has_open_question=true&limit=1") == ["R-3"]
    assert codes("status=SPEC_GAP") == ["R-1", "R-3"]
    assert codes("equipment=%EB%B3%80%EC%95%95%EA%B8%B0") == ["R-2"]
    assert codes("task=FACTORY_TEST") == ["R-4"]
    assert codes("necessity=PREFERRED") == []
    assert codes(f"document_version_id={uuid4()}") == []
    assert codes("needs_rereview=true") == []
    proposal = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.change_proposals (id, project_id, requirement_id, origin, current, "
            "proposed, rationale, proposer_kind, data_origin, derivation_kind, known_at) VALUES "
            "(%s,%s,%s,'web','{}','{}','합성','system','synthetic','DERIVED',now())",
            (proposal, chain["project"], review),
        )
        conn.execute(
            "INSERT INTO tft.rereview_marks (id, project_id, target_kind, target_id, "
            "cause_proposal_id) VALUES (%s,%s,'requirement',%s,%s)",
            (uuid4(), chain["project"], review, proposal),
        )
    assert codes("needs_rereview=true") == ["R-2"]
    assert codes("needs_rereview=false") == ["R-1", "R-3", "R-4"]
    pending = data(get(client, f"/requirements/{review}"))["links"]["pending_proposal_ids"]
    assert pending == [str(proposal)]


def test_requirement_detail_links_origin_spec_and_judgement(client, admin, chain):
    """요구 상세: 원문·허용 오차·조건·지정품·연결·시스템 표시."""
    requirement = add_requirement(
        admin,
        chain,
        "R-9",
        value="5.75",
        unit="%",
        tolerance="±10%",
        needs_review=True,
        designated_model="Resibloc",
        designated_manufacturer="Eaton",
    )
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.requirement_conditions (requirement_id, version, ordinal, name, value, unit) "
            "VALUES (%s,1,0,'reference_temperature','85','°C')",
            (requirement,),
        )
        conn.execute(
            "INSERT INTO tft.clause_deleted_values (clause_id, ordinal, text, position) "
            "VALUES (%s,0,'33',4)",
            (chain["clause"],),
        )
    question = draft(admin, {**chain, "requirement": requirement})
    body = data(get(client, f"/requirements/{requirement}"))
    assert (
        body["code"] == "R-9" and body["needs_review"] is True and body["needs_rereview"] is False
    )
    assert body["origin"]["clause_no"] == "2.5.C"
    assert body["origin"]["deleted_values"] == [{"text": "33", "position": 4}]
    assert body["origin"]["edit_trace_support"] is True
    assert (body["spec"]["value"], body["spec"]["tolerance"]) == ("5.75", "±10%")
    assert body["spec"]["conditions"] == [
        {"name": "reference_temperature", "value": "85", "unit": "°C"}
    ]
    assert body["spec"]["designated_item"]["manufacturer"] == "Eaton"
    assert body["spec"]["designated_item"]["clause_id"] == str(chain["clause"])
    assert body["judgement"]["status"] == "EVIDENCE_FOUND"
    assert body["links"]["question_ids"] == [str(question)]
    assert body["links"]["comparison_summary"] is None
    plain = data(get(client, f"/requirements/{chain['requirement']}"))
    # T15 전 판본의 NULL은 그대로 null이다('오차 없음'이 아니다).
    assert plain["spec"]["tolerance"] is None and plain["spec"]["designated_item"] is None


def add_comparison(admin, chain, *, product, result="MEETS", requirement_version=1, version=1):
    comparison_id = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.product_comparisons (id, requirement_id, requirement_version, "
            "product_code, scope, lifecycle_status, matched_attribute, our_value, our_unit, "
            "our_conditions, comparable, result, search_trace, provenance_kind, version, data_origin, "
            "derivation_kind, known_at) VALUES (%s,%s,%s,%s,'[]','ACTIVE','bil','95','kV',%s,true,%s,"
            "%s,'ai_draft',%s,'synthetic','DERIVED',now())",
            (
                comparison_id,
                chain["requirement"],
                requirement_version,
                product,
                Jsonb([{"name": "duration", "value": "2", "unit": "s"}]),
                result,
                Jsonb(
                    {
                        "filters": ["family=TRANSFORMER"],
                        "documents_searched": 2,
                        "search_failed": False,
                    }
                ),
                version,
            ),
        )
        conn.execute(
            "INSERT INTO tft.comparison_evidence (comparison_id, ordinal, document, clause, "
            "applicability, limits) VALUES (%s,0,'DS-1','2.1',NULL,NULL)",
            (comparison_id,),
        )
    return comparison_id


def test_comparison_uses_the_latest_stored_version_and_rebuilds_an_empty_scope(
    client, admin, chain
):
    """비교: 저장된 가장 최근 판본의 후보별 최신 행, 후보가 없으면 저장된 계열로 범위를 만든다."""
    url = f"/requirements/{chain['requirement']}/comparison"
    empty = data(get(client, url))
    assert empty["candidates"] == []
    # T15 전 판본은 계열을 모른다.
    assert empty["search_trace"] == {
        "filters": ["family=NONE"],
        "documents_searched": 0,
        "search_failed": False,
    }
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET equipment_family='OTHER' WHERE requirement_id=%s",
            (chain["requirement"],),
        )
    assert data(get(client, url))["search_trace"]["filters"] == ["family=OTHER"]

    add_comparison(admin, chain, product="TRF-1", result="DIFFERS")
    add_comparison(admin, chain, product="TRF-1", result="MEETS", version=2)
    add_comparison(admin, chain, product="TRF-2", result="NOT_COMPARABLE")
    body = data(get(client, url))
    assert body["requirement_version"] == 1
    assert body["search_trace"]["filters"] == ["family=TRANSFORMER"]
    assert [(c["product_code"], c["attribute_match"]["result"]) for c in body["candidates"]] == [
        ("TRF-1", "MEETS"),
        ("TRF-2", "NOT_COMPARABLE"),
    ]
    match = body["candidates"][0]["attribute_match"]
    assert match["our_conditions"] == [{"name": "duration", "value": "2", "unit": "s"}]
    assert match["evidence"][0]["document"] == "DS-1"
    links = data(get(client, f"/requirements/{chain['requirement']}"))["links"]
    assert links["comparison_summary"] == "후보 2개: 충족 1, 비교 불가 1"


def add_grade(admin, chain, requirement, code, grade, unknown=()):
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.partner_grades (id, project_id, candidate_code, profile_version, "
            "requirement_id, requirement_version, grade, reasons, exclusion_reasons, mandatory_check, "
            "data_origin, derivation_kind, known_at) "
            "VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s,%s,'synthetic','DERIVED',now())",
            (
                uuid4(),
                chain["project"],
                code,
                1,
                requirement,
                grade,
                Jsonb(["사유"]),
                Jsonb(["제외"] if grade == "EXCLUDED" else []),
                Jsonb({"passed": [], "failed": [], "unknown": list(unknown)}),
            ),
        )


def test_partner_candidates_come_from_the_stored_grades(client, admin, chain):
    """후보: 저장된 등급이 있는 후보만, 후보 등급은 전체 기준, 요구 필터는 등급 목록만 좁힌다."""
    code = "Q-" + chr(ord("A") + uuid4().int % 26)
    profile = load_profiles()[1]
    row = profile.model_dump(mode="json") | {"candidate_code": code, "profile_version": 1}
    columns = [k for k in row if k != "known_at"]
    with psycopg.connect(admin) as conn:
        conn.execute(
            f"INSERT INTO tft.partner_profiles ({', '.join(columns)}, known_at) "
            f"VALUES ({', '.join(['%s'] * len(columns))}, now())",
            [
                Jsonb(row[k])
                if isinstance(row[k], dict | list) and k not in ("similar_tasks", "supply_regions")
                else row[k]
                for k in columns
            ],
        )
    url = f"/projects/{chain['project']}/partner-candidates"
    assert data(get(client, url))["items"] == []
    other = add_requirement(admin, chain, "R-5", status="INSUFFICIENT_EVIDENCE")
    add_grade(admin, chain, chain["requirement"], code, "MANDATORY_CONFIRMED")
    add_grade(admin, chain, other, code, "REVIEW", unknown=["bil", "bil", "form"])
    try:
        [candidate] = data(get(client, url))["items"]
        assert candidate["candidate_code"] == code
        assert candidate["overall_grade"] == "REVIEW"
        assert candidate["conditions_to_confirm"] == ["bil", "form"]
        assert candidate["on_customer_manufacturer_list"] is None
        assert len(candidate["grades"]) == 2
        [narrow] = data(get(client, f"{url}?requirement_id={chain['requirement']}"))["items"]
        assert [g["grade"] for g in narrow["grades"]] == ["MANDATORY_CONFIRMED"]
        # 후보 등급은 요구 필터와 무관하게 전체 기준이다.
        assert narrow["overall_grade"] == "REVIEW"
        assert data(get(client, f"{url}?requirement_id={uuid4()}"))["items"] == []
    finally:
        with psycopg.connect(admin) as conn:
            conn.execute("DELETE FROM tft.partner_grades WHERE candidate_code=%s", (code,))
            conn.execute("DELETE FROM tft.partner_profiles WHERE candidate_code=%s", (code,))


# --- 경로 21~24 변경 제안 ---------------------------------------------------------


def propose(client, gap, who=OWNER, key=None, **proposed):
    body = {
        "requirement_id": str(gap["requirement"]),
        "proposed": proposed or {"status": "EVIDENCE_FOUND", "reason": "자료 확인"},
        "rationale": "시험 성적서 확인",
    }
    return call(
        client, "POST", f"/projects/{gap['project']}/proposals", who, body, key or uuid4().hex
    )


def decide(client, proposal, action="approve", who=OWNER, key=None, **body):
    url = f"/proposals/{proposal['proposal_id']}/{action}"
    payload = {"version": proposal["version"], **body}
    return call(client, "POST", url, who, payload, key or uuid4().hex)


def test_proposals_are_listed_with_filters_and_replayed(client, gap, admin):
    """21·22: 값이 온 칸만 바꾸고 나머지는 잇는다. 같은 키는 같은 제안, 필터는 SQL에서."""
    key = uuid4().hex
    made = propose(client, gap, key=key)
    assert made.status_code == 201, made.text
    proposal = made.json()["data"]
    assert (proposal["origin"], proposal["status"], proposal["proposer_kind"]) == (
        "web",
        "PENDING",
        "user",
    )
    assert proposal["proposer_user_id"] == str(SALES)
    assert proposal["proposed"]["status"] == "EVIDENCE_FOUND"
    assert proposal["proposed"]["necessity"] == proposal["current"]["necessity"]
    assert proposal["proposed"]["unresolved"] == proposal["current"]["unresolved"]
    assert propose(client, gap, key=key).json()["data"] == proposal
    url = f"/projects/{gap['project']}/proposals"
    assert [p["proposal_id"] for p in data(get(client, url))["items"]] == [proposal["proposal_id"]]
    assert data(get(client, f"{url}?status=APPROVED"))["items"] == []
    assert data(get(client, f"{url}?origin=pdf_annot"))["items"] == []
    assert audit_actions(admin, gap["project"]).count("proposal.create") == 1


def test_proposal_refuses_no_change_and_other_project_requirements(client, gap):
    """22: 바뀌는 칸이 없으면 422, 다른 프로젝트 요구는 404."""
    same = propose(client, gap, status="SPEC_GAP").json()
    assert error_of(same) == "VALIDATION_ERROR"
    body = {
        "requirement_id": str(uuid4()),
        "proposed": {"status": "EVIDENCE_FOUND"},
        "rationale": "근거",
    }
    url = f"/projects/{gap['project']}/proposals"
    assert error(call(client, "POST", url, OWNER, body, uuid4().hex)) == (
        404,
        "TFT_REQUIREMENT_NOT_FOUND",
    )


def error_of(body):
    return body["error"]["code"]


def test_proposal_permissions_follow_the_role_table(client, gap):
    """22는 검토 부서도 되고, 23·24는 담당 sales·책임자만, 범위 밖은 404."""
    made = propose(client, gap, who=TECH)
    assert made.status_code == 201, made.text
    proposal = made.json()["data"]
    assert error(propose(client, gap, who=OUTSIDER)) == (404, "TFT_PROJECT_NOT_FOUND")
    assert decide(client, proposal, who=TECH).status_code == 403
    assert decide(client, proposal, "reject", who=TECH, reason="x").status_code == 403
    assert error(decide(client, proposal, who=OUTSIDER)) == (404, "TFT_PROPOSAL_NOT_FOUND")
    manager = Principal(
        user_id=uuid4(), roles=frozenset({"manager"}), managed_projects={gap["project"]}
    )
    assert decide(client, proposal, who=manager).status_code == 403
    assert propose(client, gap, who=manager).status_code == 403
    assert data(get(client, f"/projects/{gap['project']}/proposals", manager))["items"]


def test_approval_writes_a_new_version_and_keeps_other_fields(client, gap, admin):
    """23: 판단 칸만 바뀌고 나머지 칸·보완 선택지는 이어지며, 감사에 제안자·승인자가 남는다."""
    selected = choose(client, gap, "EXTERNAL_PRODUCT", candidate_codes=["P-A"])
    assert selected.status_code == 200, selected.text
    proposal = propose(
        client,
        gap,
        who=TECH,
        **{
            "status": "EVIDENCE_FOUND",
            "necessity": "PREFERRED",
            "reason": "자료 확인",
            "unresolved": [],
            "conditions": [{"name": "시험", "value": "형식", "unit": None}],
        },
    ).json()["data"]
    done = decide(client, proposal, note="확인함")
    assert done.status_code == 200, done.text
    detail = done.json()["data"]
    assert detail["version"] == 2
    assert detail["judgement"]["status"] == "EVIDENCE_FOUND"
    assert detail["spec"]["necessity"] == "PREFERRED"
    assert detail["origin"]["quote"] == "BIL 95 kV"
    assert detail["needs_rereview"] is True
    with psycopg.connect(admin) as conn:
        version = conn.execute(
            "SELECT provenance_kind, decided_by, proposal_id::text, attribute, value, unit, "
            "document_version_id FROM tft.requirement_versions "
            "WHERE requirement_id=%s AND version=2",
            (gap["requirement"],),
        ).fetchone()
        assert version[:6] == (
            "human_input",
            SALES,
            proposal["proposal_id"],
            "BIL",
            "95",
            "kV",
        )
        assert version[6] == gap["docver"]
        assert conn.execute(
            "SELECT name, value, unit FROM tft.requirement_conditions "
            "WHERE requirement_id=%s AND version=2",
            (gap["requirement"],),
        ).fetchall() == [("시험", "형식", None)]
        kinds = conn.execute(
            "SELECT target_kind FROM tft.rereview_marks WHERE cause_proposal_id=%s ORDER BY 1",
            (proposal["proposal_id"],),
        ).fetchall()
        assert kinds == [("requirement",), ("supplement",)]
        audit = conn.execute(
            "SELECT entity_id, actor_user_id, proposer_user_id, approver_user_id "
            "FROM tft.rfp_audit_events WHERE action='proposal.approve' AND project_id=%s",
            (gap["project"],),
        ).fetchall()
    assert audit == [(proposal["proposal_id"], SALES, TECH.user_id, SALES)]
    # 한계(계약): 근거 확인으로 승인되면 고를 경로가 없어 표시를 해소할 수 없다.
    stuck = choose(client, gap, "EXTERNAL_PRODUCT", candidate_codes=["P-A"], version=2)
    assert error(stuck) == (409, "TFT_INVALID_TRANSITION")
    assert data(get(client, f"/requirements/{gap['requirement']}"))["needs_rereview"] is True
    listed = data(get(client, f"/projects/{gap['project']}/proposals?status=APPROVED"))["items"]
    assert [p["decided_by"] for p in listed] == [str(SALES)]


def test_reselecting_the_supplement_clears_the_rereview_marks(client, gap, admin):
    """재검토 표시는 같은 요구의 보완 선택을 다시 저장하면 해소된다(사람 재확인)."""
    assert choose(client, gap, "EXTERNAL_PRODUCT", candidate_codes=["P-A"]).status_code == 200
    proposal = propose(client, gap, reason="다시 본 사유").json()["data"]
    assert decide(client, proposal).status_code == 200
    url = f"/requirements/{gap['requirement']}"
    assert data(get(client, url))["needs_rereview"] is True
    # 새 판본에는 선택지 행이 없어도 저장된 후보를 그대로 읽는다.
    options = data(get(client, f"{url}/supplement-options"))
    external = [o for o in options["options"] if o["path"] == "EXTERNAL_PRODUCT"]
    assert external and external[0]["candidate_codes"] == ["P-A"]
    again = choose(client, gap, "EXTERNAL_PRODUCT", candidate_codes=["P-A"], version=2)
    assert again.status_code == 200, again.text
    assert data(get(client, url))["needs_rereview"] is False
    with psycopg.connect(admin) as conn:
        assert conn.execute(
            "SELECT count(*), count(cleared_at), min(cleared_by::text) FROM tft.rereview_marks "
            "WHERE cause_proposal_id=%s",
            (proposal["proposal_id"],),
        ).fetchone() == (2, 2, str(SALES))


def test_decisions_are_once_only_and_against_the_latest_version(client, gap):
    """결정은 한 번, 판본이 다르면 409, 재전송은 첫 결과, 뒤처진 제안은 승인 못 하고 기각만 된다."""
    first = propose(client, gap, reason="첫 제안").json()["data"]
    late = propose(client, gap, reason="늦은 제안").json()["data"]
    stale = decide(client, {**first, "version": 9})
    assert error(stale) == (409, "TFT_VERSION_CONFLICT")
    key = uuid4().hex
    done = decide(client, first, key=key)
    assert done.status_code == 200, done.text
    assert decide(client, first, key=key).json()["data"] == done.json()["data"]
    assert error(decide(client, first)) == (409, "TFT_PROPOSAL_DECIDED")
    assert error(decide(client, late)) == (409, "TFT_VERSION_CONFLICT")
    rejected = decide(client, late, "reject", reason="이미 반영됨")
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()["data"]
    assert (body["status"], body["decided_by"], body["version"]) == ("REJECTED", str(SALES), 2)
    assert error(decide(client, {**late, "version": 2})) == (409, "TFT_PROPOSAL_DECIDED")
    assert data(get(client, f"/requirements/{gap['requirement']}"))["version"] == 2


def test_note_without_a_requirement_can_only_be_rejected(client, gap, admin):
    """조항을 못 맞춘 메모 제안은 승인 422, 기각은 된다."""
    proposal_id = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.change_proposals (id, project_id, clause_unresolved, origin, current, "
            "proposed, rationale, proposer_kind, data_origin, derivation_kind, known_at) "
            "VALUES (%s,%s,true,'pdf_annot','{}','{}','메모','system','synthetic','DERIVED',now())",
            (proposal_id, gap["project"]),
        )
    proposal = {"proposal_id": proposal_id, "version": 1}
    assert error(decide(client, proposal)) == (422, "VALIDATION_ERROR")
    assert decide(client, proposal, "reject", reason="조항 없음").status_code == 200
