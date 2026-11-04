"""묶음 질문 작성·묶음 보완 선택(KAN-188 검토 대상 묶음)을 HTTP로, 실제 마이그레이션의 PostgreSQL에서 확인한다."""

# ruff: noqa: F811

from uuid import uuid4

import psycopg
import pytest

from tests.tft.test_rfp_http_base_db import admin, app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_routes_db import (  # noqa: F401
    NOW,
    OUTSIDER,
    OWNER,
    RND,
    TECH,
    answer,
    audit_actions,
    call,
    client,
    error,
    gap,
)


@pytest.fixture
def second(admin, gap):
    """같은 프로젝트의 두 번째 요구(판본 1). 보완 선택지 행이 없어 외부 제품 경로는 막힌다."""
    requirement = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.requirements (id, project_id, code, data_origin, derivation_kind, "
            "known_at) VALUES (%s,%s,'R-2','synthetic','DERIVED',%s)",
            (requirement, gap["project"], NOW),
        )
        conn.execute(
            "INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, "
            "clause_id, quote, operator, standard_system, necessity, status, reason, "
            "provenance_kind, data_origin, derivation_kind, known_at) "
            "SELECT %s, 1, document_version_id, clause_id, quote, operator, standard_system, "
            "necessity, status, reason, provenance_kind, data_origin, derivation_kind, known_at "
            "FROM tft.requirement_versions WHERE requirement_id=%s",
            (requirement, gap["requirement"]),
        )
    return requirement


def item(requirement, **extra):
    return {
        "requirement_id": None if requirement is None else str(requirement),
        "origin": {"clause_id": None, "page": None},
        "issue_type": "CONDITION_MISSING",
        "question_text": "BIL 시험 조건을 알려 주십시오",
        "target": "CUSTOMER",
        **extra,
    }


def batch_questions(client, chain, items, who=OWNER, key=None):
    url = f"/projects/{chain['project']}/questions/batch"
    return call(client, "POST", url, who, {"items": items}, key or uuid4().hex)


def count(admin, table, project):
    with psycopg.connect(admin) as conn:
        return conn.execute(
            f"SELECT count(*) FROM tft.{table} WHERE project_id=%s",  # noqa: S608
            (project,),
        ).fetchone()[0]


def test_batch_questions_save_in_order_and_show_in_the_list(client, gap, second, admin):
    items = [item(gap["requirement"]), item(second, question_text="두 번째 질문")]
    response = batch_questions(client, gap, items)
    assert response.status_code == 201, response.text
    saved = response.json()["data"]["items"]
    assert [q["requirement_id"] for q in saved] == [str(gap["requirement"]), str(second)]
    assert all(q["status"] == "OPEN" and q["version"] == 1 for q in saved)
    listed = call(client, "GET", f"/projects/{gap['project']}/questions", OWNER)
    ids = {q["question_id"]: q["requirement_id"] for q in listed.json()["data"]["items"]}
    assert {q["question_id"]: q["requirement_id"] for q in saved} == ids
    assert audit_actions(admin, gap["project"]).count("question.create") == 2


def test_batch_questions_are_all_or_nothing_with_index_errors(client, gap, admin):
    items = [
        item(gap["requirement"]),
        item(uuid4()),
        item(
            gap["requirement"], target="INTERNAL", target_dept="CERT", issue_type="RND_FEASIBILITY"
        ),
        item(None),
    ]
    response = batch_questions(client, gap, items)
    assert error(response) == (422, "VALIDATION_ERROR")
    details = response.json()["error"]["details"]
    assert [(d["index"], d["code"]) for d in details] == [
        ("1", "TFT_REQUIREMENT_NOT_FOUND"),
        ("2", "VALIDATION_ERROR"),
        ("3", "VALIDATION_ERROR"),
    ]
    assert count(admin, "customer_questions", gap["project"]) == 0
    assert error(batch_questions(client, gap, []))[0] == 422
    assert error(batch_questions(client, gap, [item(gap["requirement"])] * 51))[0] == 422


def test_batch_questions_replay_and_conflict_by_key(client, gap, second, admin):
    key = "batch-" + uuid4().hex
    body = [item(gap["requirement"]), item(second)]
    first = batch_questions(client, gap, body, key=key)
    again = batch_questions(client, gap, body, key=key)
    assert again.status_code == 201
    assert again.json()["data"] == first.json()["data"]
    assert count(admin, "customer_questions", gap["project"]) == 2
    other = batch_questions(client, gap, [item(second)], key=key)
    assert error(other) == (409, "IDEMPOTENCY_CONFLICT")


def test_batch_questions_follow_single_question_permissions(client, gap):
    assert error(batch_questions(client, gap, [item(gap["requirement"])], who=OUTSIDER)) == (
        404,
        "TFT_PROJECT_NOT_FOUND",
    )
    # 검토 부서 직원은 단건 작성과 같이 쓸 수 있다.
    assert batch_questions(client, gap, [item(gap["requirement"])], who=RND).status_code == 201
    assert batch_questions(client, gap, [item(gap["requirement"])], who=TECH).status_code == 201


def test_batch_question_gets_a_reply_and_a_change_proposal(client, gap):
    """묶음으로 만든 질문의 회신이 단건 질문과 같이 변경 제안을 만든다."""
    [question] = batch_questions(
        client,
        gap,
        [item(gap["requirement"], question_text="BIL 시험 조건을 알려 주십시오")],
    ).json()["data"]["items"]
    result = answer(client, question, OWNER)
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["status"] == "REPLIED"
    assert data["created_proposal"]["origin"] == "customer_reply"
    assert data["created_proposal"]["proposed"]["unresolved"] == []


def select_body(items, **extra):
    return {
        "dry_run": False,
        "path": "EXTERNAL_PRODUCT",
        "role_split": "역할 분담",
        "conditions": [],
        "note": None,
        "candidate_codes": ["P-A"],
        "rnd_check_waiver_reason": None,
        "items": [{"requirement_id": str(r), "version": v} for r, v in items],
        **extra,
    }


def batch_select(client, chain, body, who=OWNER, key="auto"):
    key = uuid4().hex if key == "auto" else key
    url = f"/projects/{chain['project']}/supplement-selections/batch"
    return call(client, "POST", url, who, body, key)


def selection_rows(admin, requirement):
    with psycopg.connect(admin) as conn:
        return conn.execute(
            "SELECT count(*) FROM tft.supplement_selections WHERE requirement_id=%s",
            (requirement,),
        ).fetchone()[0]


def test_batch_selection_dry_run_writes_nothing_and_splits_the_items(client, gap, second, admin):
    body = select_body([(gap["requirement"], 1), (second, 1)], dry_run=True)
    response = batch_select(client, gap, body, key=None)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["saved"] == []
    assert data["allowed"] == [{"requirement_id": str(gap["requirement"])}]
    assert [(b["requirement_id"], b["code"]) for b in data["blocked"]] == [
        (str(second), "TFT_INVALID_TRANSITION")
    ]
    assert selection_rows(admin, gap["requirement"]) == 0
    assert "supplement.select" not in audit_actions(admin, gap["project"])


def test_batch_selection_saves_allowed_items_and_reports_blocked(client, gap, second, admin):
    body = select_body([(gap["requirement"], 1), (second, 1)])
    response = batch_select(client, gap, body)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert [x["requirement_id"] for x in data["saved"]] == [str(gap["requirement"])]
    assert data["saved"][0]["selection"]["path"] == "EXTERNAL_PRODUCT"
    assert data["allowed"] == []
    assert [b["requirement_id"] for b in data["blocked"]] == [str(second)]
    assert selection_rows(admin, gap["requirement"]) == 1
    assert selection_rows(admin, second) == 0
    assert audit_actions(admin, gap["project"]).count("supplement.select") == 1


def test_batch_selection_blocks_a_stale_version_and_returns_200_when_none_saved(client, gap, admin):
    response = batch_select(client, gap, select_body([(gap["requirement"], 2)]))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["saved"] == []
    assert [b["code"] for b in data["blocked"]] == ["TFT_VERSION_CONFLICT"]
    assert selection_rows(admin, gap["requirement"]) == 0


def test_batch_selection_follows_the_candidate_and_rnd_rules(client, gap):
    bad = batch_select(client, gap, select_body([(gap["requirement"], 1)], candidate_codes=["P-Z"]))
    assert [b["code"] for b in bad.json()["data"]["blocked"]] == ["VALIDATION_ERROR"]
    # 자사 개발은 R&D 질문을 만들고, 그 뒤 협력사 경로는 면제 사유가 필요하다.
    own = batch_select(
        client,
        gap,
        select_body([(gap["requirement"], 1)], path="OWN_DEVELOPMENT", candidate_codes=[]),
    )
    assert own.status_code == 201, own.text
    gated = batch_select(client, gap, select_body([(gap["requirement"], 1)]))
    assert [b["code"] for b in gated.json()["data"]["blocked"]] == ["TFT_RND_CHECK_REQUIRED"]


def test_batch_selection_replays_by_key_and_needs_a_key_and_permission(client, gap, admin):
    key = "sel-" + uuid4().hex
    body = select_body([(gap["requirement"], 1)])
    first = batch_select(client, gap, body, key=key)
    again = batch_select(client, gap, body, key=key)
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json()["data"] == first.json()["data"]
    assert selection_rows(admin, gap["requirement"]) == 1
    changed = batch_select(
        client, gap, select_body([(gap["requirement"], 1)], note="다름"), key=key
    )
    assert error(changed) == (409, "IDEMPOTENCY_CONFLICT")
    assert error(batch_select(client, gap, body, key=None))[0] == 422
    assert batch_select(client, gap, body, who=RND).status_code == 403
    assert error(batch_select(client, gap, body, who=OUTSIDER)) == (404, "TFT_PROJECT_NOT_FOUND")
    assert error(batch_select(client, gap, select_body([])))[0] == 422
