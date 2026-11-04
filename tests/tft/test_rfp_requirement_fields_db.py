"""KAN-227 자사 보증값, 도면 확인 결정, 설비 목록(전용 PostgreSQL). 행은 모두 합성이다."""

# ruff: noqa: F811

from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from backend.shared.access import principal_header
from tests.tft.test_rfp_decisions_db import decide, ok
from tests.tft.test_rfp_http_base_db import admin, app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_routes_db import OUTSIDER, OWNER, add_comparison, client  # noqa: F401


def requirement(client, chain):
    response = client.get(
        f"/api/v1/tft/requirements/{chain['requirement']}", headers=principal_header(OWNER)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def listed(client, chain):
    response = client.get(
        f"/api/v1/tft/projects/{chain['project']}/requirements", headers=principal_header(OWNER)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"][0]


def test_own_value_reads_the_stored_comparison_and_prefers_a_meeting_candidate(
    client, chain, admin
):
    assert requirement(client, chain)["own_value"] is None
    add_comparison(admin, chain, product="TRF-B", result="DIFFERS")
    add_comparison(admin, chain, product="TRF-A", result="MEETS")
    own = requirement(client, chain)["own_value"]
    assert (own["product_code"], own["value"], own["unit"], own["result"]) == (
        "TRF-A",
        "95",
        "kV",
        "MEETS",
    )
    assert own["conditions"] == [{"name": "duration", "value": "2", "unit": "s"}]
    assert listed(client, chain)["own_value"] == own


def make_drawing(admin, chain):
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.clauses SET clause_no='OCR-DOCX-0001' WHERE id=%s", (chain["clause"],)
        )
        conn.execute(
            "UPDATE tft.requirement_versions SET status='EVIDENCE_FOUND' WHERE requirement_id=%s",
            (chain["requirement"],),
        )


def test_drawing_requirement_needs_a_check_even_when_met(client, chain, admin):
    refused = decide(client, chain, "confirm_drawing")
    assert refused.status_code == 422  # 도면 요구가 아니다.
    make_drawing(admin, chain)
    before = requirement(client, chain)
    assert before["bid_verdict"] == "MET"
    assert (before["decision"]["drawing_check"], before["decision"]["needed"]) == (True, True)
    made = ok(decide(client, chain, "confirm_drawing"))
    assert (made["summary"], made["requirement_version"]) == ("도면과 일치 확인", 1)
    after = requirement(client, chain)["decision"]
    assert (after["decided"], after["action"], after["reviewed"]) == (
        True,
        "confirm_drawing",
        False,
    )
    # 책임자는 결정하지 못한다(KAN-219).
    assert decide(client, chain, "confirm_drawing", who=OUTSIDER).status_code in (403, 404)
    ok(decide(client, chain, "undo", decision_id=made["decision_id"]))
    assert requirement(client, chain)["decision"]["decided"] is False


def context_row(admin, chain, groups):
    payload = {
        "schema_version": "rfp-context-v1",
        "outcome": "VALID",
        "groups": groups,
        "assignments": [],
    }
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.context_versions (project_id, version, source_job_id, "
            "source_fingerprint, source_snapshot, payload, provenance, created_by) "
            "VALUES (%s,1,%s,%s,%s,%s,'ai_draft',%s)",
            (chain["project"], uuid4(), "0" * 64, Jsonb({}), Jsonb(payload), OWNER.user_id),
        )


def equipment(client, chain, who=OWNER):
    return client.get(
        f"/api/v1/tft/projects/{chain['project']}/equipment", headers=principal_header(who)
    )


def test_equipment_list_uses_context_groups_or_falls_back_to_requirements(client, chain, admin):
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET equipment='변압기' WHERE requirement_id=%s",
            (chain["requirement"],),
        )
    fallback = equipment(client, chain)
    assert fallback.status_code == 200, fallback.text
    assert fallback.json()["data"] == {
        "source": "requirements",
        "items": [{"tag": None, "name": "변압기"}],
    }
    group = {"evidence_requirement_ids": [str(chain["requirement"])], "kind": "EQUIPMENT"}
    context_row(
        admin,
        chain,
        [
            {**group, "entity_id": "g1", "name": "공통 시험"},
            {**group, "entity_id": "g2", "name": "몰드 변압기", "tag": "TR-01"},
            {**group, "entity_id": "g3", "name": "몰드 변압기", "tag": "TR-01"},
            {**group, "entity_id": "g4", "name": "시스템", "kind": "SYSTEM"},
        ],
    )
    data = equipment(client, chain).json()["data"]
    assert data == {
        "source": "context",
        "items": [{"tag": "TR-01", "name": "몰드 변압기"}, {"tag": None, "name": "공통 시험"}],
    }
    assert equipment(client, chain, who=OUTSIDER).status_code == 404
