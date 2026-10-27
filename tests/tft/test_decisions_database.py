"""Immutable decision records with real PostgreSQL and tft_app permissions."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from backend.shared.access import Principal, principal_header
from tests.tft.test_recommendations_database import DSN, recommend
from tests.tft.test_recommendations_database import database as database
from tests.tft.test_recommendations_database import scenario as scenario

pytestmark = pytest.mark.skipif(not DSN, reason="requires dedicated TFT_TEST_DATABASE_URL")


@pytest.fixture
def ready(scenario):
    client, request, candidate, dept, dsn, role, org = scenario
    recommendation = recommend(scenario).json()["data"]["id"]
    with psycopg.connect(dsn) as conn:
        project = conn.execute(
            "SELECT project_id FROM tft.staffing_requests WHERE id=%s", (request,)
        ).fetchone()[0]
        owner = uuid4()
        conn.execute(
            "UPDATE tft.projects SET responsible_user_id=%s,sales_user_id=%s WHERE id=%s",
            (owner, owner, project),
        )
        conn.execute(
            "UPDATE tft.required_roles SET headcount=1,period_start='2027-01-01',period_end='2027-01-31',allocation_rate=50 WHERE id=%s",
            (role,),
        )
    for suffix, body in [
        ("experience", {"summary": "실제 업무 수행", "basis": "부서 검증", "status": "CONFIRMED"}),
        (
            "availability",
            {
                "period_start": "2027-01-01",
                "period_end": "2027-01-31",
                "allocation_rate": 50,
                "basis": "일정 확인",
                "status": "CONFIRMED",
            },
        ),
    ]:
        response = client.post(
            f"/api/v1/tft/staffing-requests/{request}/recommendations/{recommendation}/{suffix}",
            headers={
                **dept,
                "If-Match": '"2"' if suffix == "experience" else '"3"',
                "Idempotency-Key": suffix,
            },
            json=body,
        )
        assert response.status_code == 200
    headers = principal_header(Principal(user_id=owner, roles={"sales"}))
    return {
        "client": client,
        "project": project,
        "role": role,
        "org": org,
        "recommendation": recommendation,
        "headers": headers,
        "owner": owner,
        "dsn": dsn,
        "request": request,
        "dept": dept,
    }


def post(s, action, body, version=1, key="one", headers=None):
    return s["client"].post(
        f"/api/v1/tft/projects/{s['project']}/{action}",
        headers={**(headers or s["headers"]), "If-Match": f'"{version}"', "Idempotency-Key": key},
        json=body,
    )


def composition(s, status="CONFIRMED"):
    return {
        "members": [
            {
                "required_role_id": str(s["role"]),
                "recommendation_id": s["recommendation"],
                "status": status,
            }
        ],
        "note": "책임자 검토",
    }


def test_create_confirm_reopen_and_preserve_versions(ready):
    s = ready
    first = post(s, "compositions", composition(s))
    assert first.status_code == 200, first.text
    assert first.json()["data"]["status"] == "COMPOSING"
    result = post(s, "confirm", {"composition_version": 1, "reason": "검증 확인"}, 2)
    assert result.status_code == 200, result.text
    assert result.json()["data"]["status"] == "CONFIRMED"
    assert post(s, "compositions", composition(s), 3, key="second").status_code == 409
    assert post(s, "reopen", {"reason": "기간 변경"}, 3).status_code == 200
    assert (
        post(
            s, "confirm", {"composition_version": 1, "reason": "이전 판본 재승인"}, 4, key="again"
        ).status_code
        == 409
    )
    assert post(s, "compositions", composition(s), 4, key="second").status_code == 200
    old = s["client"].get(
        f"/api/v1/tft/projects/{s['project']}/compositions/1", headers=s["headers"]
    )
    assert old.status_code == 200 and old.json()["data"]["id"] == first.json()["data"]["id"]
    history = (
        s["client"]
        .get(f"/api/v1/tft/projects/{s['project']}/decisions", headers=s["headers"])
        .json()["data"]
    )
    assert [r["action"] for r in history] == ["COMPOSE", "CONFIRM", "REOPEN", "COMPOSE"]
    with psycopg.connect(s["dsn"]) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.final_confirmations WHERE project_id=%s", (s["project"],)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT has_table_privilege('tft_app','tft.composition_members','UPDATE')"
            ).fetchone()[0]
            is False
        )


def test_only_current_responsible_can_confirm_and_replay(ready):
    s = ready
    assert post(s, "compositions", composition(s)).status_code == 200
    manager = principal_header(
        Principal(user_id=uuid4(), roles={"manager"}, managed_projects={s["project"]})
    )
    body = {"composition_version": 1, "reason": "확인"}
    assert post(s, "confirm", body, 2, headers=manager).status_code == 403
    assert post(s, "confirm", body, 2).status_code == 200
    with psycopg.connect(s["dsn"]) as conn:
        conn.execute(
            "UPDATE tft.projects SET responsible_user_id=%s WHERE id=%s", (uuid4(), s["project"])
        )
    assert post(s, "confirm", body, 2).status_code == 403


def test_rejected_version_cannot_be_confirmed_without_new_version(ready):
    s = ready
    assert post(s, "compositions", composition(s)).status_code == 200
    assert (
        post(s, "reject", {"composition_version": 1, "reason": "추가 확인 필요"}, 2).status_code
        == 200
    )
    assert post(s, "confirm", {"composition_version": 1, "reason": "확인"}, 3).status_code == 409
    assert post(s, "compositions", composition(s), 3, key="revised").status_code == 200
    assert (
        post(s, "confirm", {"composition_version": 2, "reason": "보완 확인"}, 4).status_code == 200
    )


def test_partial_or_stale_verification_cannot_confirm_whole_team(ready):
    s = ready
    assert post(s, "compositions", composition(s, "PROPOSED")).status_code == 200
    assert post(s, "confirm", {"composition_version": 1, "reason": "확인"}, 2).status_code == 422
    assert post(s, "compositions", composition(s), 2, key="confirmed").status_code == 200
    response = s["client"].post(
        f"/api/v1/tft/staffing-requests/{s['request']}/recommendations/{s['recommendation']}/availability",
        headers={**s["dept"], "If-Match": '"4"', "Idempotency-Key": "changed"},
        json={"status": "NEEDS_CHECK"},
    )
    assert response.status_code == 200
    assert post(s, "confirm", {"composition_version": 2, "reason": "확인"}, 3).status_code == 422


def test_concurrent_retry_and_version_conflict(ready):
    s = ready
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post(s, "compositions", composition(s)), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert post(s, "compositions", composition(s, "PROPOSED")).status_code == 409
    assert post(s, "compositions", composition(s), key="new").status_code == 412
    with psycopg.connect(s["dsn"]) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.composition_versions WHERE project_id=%s", (s["project"],)
            ).fetchone()[0]
            == 1
        )


def test_invalid_input_does_not_create_version(ready):
    s = ready
    invalid = composition(s)
    invalid["members"][0]["required_role_id"] = str(uuid4())
    assert post(s, "compositions", invalid).status_code == 422
    assert (
        post(
            s, "compositions", {"members": [{"required_role_id": str(s["role"])}], "note": "공백"}
        ).status_code
        == 422
    )
    with psycopg.connect(s["dsn"]) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.composition_versions WHERE project_id=%s", (s["project"],)
            ).fetchone()[0]
            == 0
        )


def test_failure_after_inserting_composition_rolls_back(ready, monkeypatch):
    from backend.api.tft.decisions import routes

    original = routes.save_composition

    async def fail_after_insert(conn, detail, payload, actor):
        await original(conn, detail, payload, actor)
        await conn.execute("SELECT 1/0")

    monkeypatch.setattr(routes, "save_composition", fail_after_insert)
    s = ready
    assert post(s, "compositions", composition(s)).status_code == 503
    with psycopg.connect(s["dsn"]) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.composition_versions WHERE project_id=%s", (s["project"],)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT version FROM tft.projects WHERE id=%s", (s["project"],)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.decision_commands WHERE project_id=%s", (s["project"],)
            ).fetchone()[0]
            == 0
        )


def test_unassigned_responsible_and_scope_are_rejected(ready):
    s = ready
    assert post(s, "compositions", composition(s)).status_code == 200
    outsider = principal_header(Principal(user_id=uuid4(), roles={"sales"}))
    assert (
        post(
            s, "confirm", {"composition_version": 1, "reason": "확인"}, 2, headers=outsider
        ).status_code
        == 404
    )
    with psycopg.connect(s["dsn"]) as conn:
        conn.execute(
            "UPDATE tft.projects SET responsible_user_id=NULL WHERE id=%s", (s["project"],)
        )
    assert post(s, "confirm", {"composition_version": 1, "reason": "확인"}, 2).status_code == 409


def test_post_confirmation_evidence_write_is_rejected(ready):
    s = ready
    assert post(s, "compositions", composition(s)).status_code == 200
    assert post(s, "confirm", {"composition_version": 1, "reason": "확인"}, 2).status_code == 200
    response = s["client"].post(
        f"/api/v1/tft/staffing-requests/{s['request']}/recommendations/{s['recommendation']}/availability",
        headers={**s["dept"], "If-Match": '"4"', "Idempotency-Key": "after-confirm"},
        json={"status": "NEEDS_CHECK"},
    )
    assert response.status_code == 409
