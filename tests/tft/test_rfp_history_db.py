"""KAN-226 변경 이력 조회(전용 PostgreSQL). 행은 모두 합성이다."""

# ruff: noqa: F811

from uuid import uuid4

import psycopg
import pytest

from backend.shared.access import principal_header
from tests.tft.test_rfp_decisions_db import decide, ok
from tests.tft.test_rfp_http_base_db import admin, app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_routes_db import OUTSIDER, OWNER, answer, ask, client  # noqa: F401


@pytest.fixture
def story(client, chain, admin):
    """AI 판본 1 → 충족 확정 → 되돌리기 → 재비교 → 고객 회신 기록 순으로 쌓는다."""
    made = ok(decide(client, chain, "confirm_met"))
    ok(decide(client, chain, "undo", version=2, decision_id=made["decision_id"]))
    # 재비교 경로가 남기는 감사 행(workflow.recompare와 같은 칸).
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.rfp_audit_events (id, project_id, entity, entity_id, action, "
            "actor_user_id) VALUES (%s,%s,'requirement',%s,'requirement.recompare',%s)",
            (uuid4(), chain["project"], str(chain["requirement"]), OWNER.user_id),
        )
    asked = ask(client, chain, issue_type="EMPTY_VALUE", target="CUSTOMER", target_dept=None)
    assert answer(client, asked.json()["data"], OWNER).status_code == 200
    return chain


def history(client, path, who=OWNER, **query):
    return client.get(
        "/api/v1/tft" + path + "/history", params=query, headers=principal_header(who)
    )


def events(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_project_history_lists_stored_events_newest_first(client, story):
    data = events(history(client, f"/projects/{story['project']}"))
    kinds = [e["kind"] for e in data["items"]]
    assert kinds == [
        "reply_recorded",
        "recompared",
        "human_change",
        "decision_undone",
        "human_change",
        "decision",
        "ai_verdict",
    ]
    assert data["next_cursor"] is None
    at = [e["at"] for e in data["items"]]
    assert at == sorted(at, reverse=True)
    first = data["items"][-1]
    assert (first["requirement_version"], first["actor_user_id"], first["before"]) == (
        1,
        None,
        None,
    )
    assert first["after"]["status"] == "SPEC_GAP"
    confirmed = data["items"][-3]
    # 앞 판본과 달라진 칸만 낸다.
    assert confirmed["before"] == {"status": "SPEC_GAP"}
    assert confirmed["after"] == {"status": "EVIDENCE_FOUND"}
    assert confirmed["actor_user_id"] == str(OWNER.user_id)
    assert data["items"][-2]["summary"] == "충족 확정"
    assert all(e["summary"] for e in data["items"])


def test_requirement_filter_and_kind_actor_filters(client, story, admin):
    other = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.requirements (id, project_id, code, data_origin, derivation_kind, "
            "known_at) VALUES (%s,%s,'R-2','synthetic','DERIVED',now())",
            (other, story["project"]),
        )
        conn.execute(
            "INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, "
            "clause_id, quote, operator, standard_system, necessity, status, reason, "
            "provenance_kind, data_origin, derivation_kind, known_at) VALUES (%s,1,%s,%s,'q',"
            "'TEXT','NONE','MANDATORY','CONDITIONAL','조건부','ai_draft','synthetic','DERIVED',now())",
            (other, story["docver"], story["clause"]),
        )
    project = events(history(client, f"/projects/{story['project']}"))["items"]
    assert len(project) == 8
    mine = events(history(client, f"/requirements/{story['requirement']}"))["items"]
    assert len(mine) == 7
    assert {e["requirement_id"] for e in mine} == {str(story["requirement"])}
    only = events(history(client, f"/requirements/{other}"))["items"]
    assert [(e["kind"], e["requirement_id"]) for e in only] == [("ai_verdict", str(other))]
    picked = events(
        history(client, f"/projects/{story['project']}", kind="decision,decision_undone")
    )["items"]
    assert [e["kind"] for e in picked] == ["decision_undone", "decision"]
    by_owner = events(history(client, f"/projects/{story['project']}", actor=str(OWNER.user_id)))
    assert len(by_owner["items"]) == 6
    assert {e["actor_user_id"] for e in by_owner["items"]} == {str(OWNER.user_id)}
    assert history(client, f"/projects/{story['project']}", kind="nope").status_code == 422


def test_paging_walks_every_event_once(client, story):
    path = f"/projects/{story['project']}"
    full = [e["id"] for e in events(history(client, path))["items"]]
    seen, cursor = [], None
    while True:
        data = events(history(client, path, limit=2, **({"cursor": cursor} if cursor else {})))
        assert len(data["items"]) <= 2
        seen += [e["id"] for e in data["items"]]
        cursor = data["next_cursor"]
        if cursor is None:
            break
    assert seen == full and len(set(seen)) == len(full) == 7


def test_bad_cursor_and_outside_scope(client, story):
    response = history(client, f"/projects/{story['project']}", cursor="not-a-cursor")
    assert (response.status_code, response.json()["error"]["code"]) == (400, "TFT_INVALID_CURSOR")
    outside = history(client, f"/projects/{story['project']}", who=OUTSIDER)
    assert (outside.status_code, outside.json()["error"]["code"]) == (404, "TFT_PROJECT_NOT_FOUND")
    req = history(client, f"/requirements/{story['requirement']}", who=OUTSIDER)
    assert (req.status_code, req.json()["error"]["code"]) == (404, "TFT_REQUIREMENT_NOT_FOUND")
    assert history(client, f"/requirements/{uuid4()}").status_code == 404
