"""KAN-231 요구 목록의 묶음·중복·설비 계열과 중복 구성원 결정 규칙(전용 PostgreSQL). 행은 모두 합성이다."""

# ruff: noqa: F811

from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from tests.tft.test_rfp_decisions_db import call, decide, ok, project_gate
from tests.tft.test_rfp_http_base_db import NOW, admin, app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_routes_db import OWNER, client  # noqa: F401

CONTENT = {"equipment": "TR-01", "attribute": "BIL", "value": "95", "unit": "kV"}


def twin(admin, chain):
    """R-1과 같은 설비·속성·값의 R-2(원문 다른 곳에 반복된 요구)를 만들고 둘 다 TR-01 BIL 95 kV로 둔다."""
    twin_id = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.requirements (id, project_id, code, data_origin, derivation_kind, known_at) "
            "VALUES (%s,%s,'R-2','synthetic','DERIVED',%s)",
            (twin_id, chain["project"], NOW),
        )
        conn.execute(
            "INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, "
            "clause_id, quote, operator, standard_system, necessity, status, reason, provenance_kind, "
            "data_origin, derivation_kind, known_at) VALUES (%s,1,%s,%s,'BIL 95 kV','GTE','IEEE',"
            "'MANDATORY','SPEC_GAP','자사 정격 부족','ai_draft','synthetic','DERIVED',%s)",
            (twin_id, chain["docver"], chain["clause"], NOW),
        )
        conn.execute(
            "UPDATE tft.requirement_versions SET equipment=%s, attribute=%s, value=%s, unit=%s, "
            "equipment_family='TRANSFORMER' WHERE requirement_id IN (%s,%s)",
            (*CONTENT.values(), chain["requirement"], twin_id),
        )
    return twin_id


def context(admin, chain, twin_id):
    ids = [str(chain["requirement"]), str(twin_id)]
    snapshot = [
        {
            "requirement_id": rid,
            "version": 1,
            "document_version_id": str(chain["docver"]),
            **CONTENT,
        }
        for rid in ids
    ]
    payload = {
        "schema_version": "rfp-context-v1",
        "outcome": "VALID",
        "groups": [
            {
                "entity_id": "g-tr",
                "name": "몰드 변압기",
                "kind": "EQUIPMENT",
                "tag": "TR-01",
                "evidence_requirement_ids": ids,
            }
        ],
        "assignments": [],
        "duplicates": [
            {
                "canonical_requirement_id": ids[0],
                "requirement_ids": ids,
                "tag": "TR-01",
                "attribute": "BIL",
                "value": "95",
                "unit": "kV",
                "clause_nos": ["2.5.C", "OCR-DOCX-0001"],
            }
        ],
    }
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.context_versions (project_id, version, source_job_id, "
            "source_fingerprint, source_snapshot, payload, provenance, created_by) "
            "VALUES (%s,1,%s,%s,%s,%s,'ai_draft',%s)",
            (
                chain["project"],
                uuid4(),
                "0" * 64,
                Jsonb({"requirements": snapshot}),
                Jsonb(payload),
                OWNER.user_id,
            ),
        )


def items(client, chain):
    got = call(client, "GET", f"/projects/{chain['project']}/requirements", OWNER)
    assert got.status_code == 200, got.text
    return {i["code"]: i for i in got.json()["data"]["items"]}


def test_duplicates_follow_the_representative_until_it_is_excluded_or_changed(client, chain, admin):
    twin_id = twin(admin, chain)
    before = items(client, chain)
    # 맥락 전: 계열 묶음만 있고 둘 다 결정 대상이다.
    assert {
        c: (i["bucket"], i["group"], i["duplicate"], i["context_stale"]) for c, i in before.items()
    } == {
        "R-1": ("TR", None, None, None),
        "R-2": ("TR", None, None, None),
    }
    assert project_gate(client, chain)["undecided"] == 2

    context(admin, chain, twin_id)
    after = items(client, chain)
    rep, member = after["R-1"], after["R-2"]
    assert rep["group"] == {"group_id": "g-tr", "tag": "TR-01", "name": "몰드 변압기"}
    assert rep["duplicate"] == {
        "representative_id": str(chain["requirement"]),
        "member_count": 2,
        "is_representative": True,
    }
    assert member["duplicate"]["is_representative"] is False
    assert (rep["context_stale"], member["context_stale"]) == (False, False)
    assert (rep["decision"]["needed"], member["decision"]["needed"]) == (True, False)
    assert member["decision"]["follows"] == str(chain["requirement"])
    gate = project_gate(client, chain)
    assert (gate["undecided"], gate["deviation_missing"]) == (1, 1)

    # 구성원은 대표가 결정한다.
    refused = decide(client, {**chain, "requirement": twin_id}, "confirm_met")
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "TFT_DUPLICATE_MEMBER"
    assert error["details"] == [{"field": "representative_id", "reason": str(chain["requirement"])}]

    # 대표를 빼면 구성원이 다시 결정 대상이다.
    ok(decide(client, chain, "exclude_requirement", text="반복 조항 정리 시험"))
    member = items(client, chain)["R-2"]
    assert (member["decision"]["needed"], member["decision"]["follows"]) == (True, None)
    ok(decide(client, chain, "restore_requirement"))

    # 맥락 뒤 구성원 값이 바뀌면(회신 반영 등) 그 요구는 context_stale이고 중복에서 빠진다.
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET value='110' WHERE requirement_id=%s", (twin_id,)
        )
    changed = items(client, chain)
    assert changed["R-2"]["context_stale"] is True
    assert (changed["R-1"]["duplicate"], changed["R-2"]["duplicate"]) == (None, None)
    assert changed["R-2"]["decision"]["needed"] is True


def test_a_member_of_a_met_representative_is_settled(client, chain, admin):
    """40444 P26-0003: 대표가 결정이 필요 없는 충족이면, 결정할 수 없는 구성원(같은 값의 도면 OCR 요구)의
    '검토 필요' 표시가 초안 저장을 막지 않는다."""
    import asyncio

    from psycopg.rows import dict_row

    from backend.api.tft.rfp.drafts import settled_requirements

    twin_id = twin(admin, chain)
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET status='EVIDENCE_FOUND', operator='GTE' "
            "WHERE requirement_id=%s",
            (chain["requirement"],),
        )
        conn.execute(
            "UPDATE tft.requirement_versions SET status='CONDITIONAL', needs_review=true "
            "WHERE requirement_id=%s",
            (twin_id,),
        )
    context(admin, chain, twin_id)
    found = items(client, chain)
    assert (found["R-1"]["bid_verdict"], found["R-1"]["decision"]["needed"]) == ("MET", False)
    assert found["R-2"]["decision"]["follows"] == str(chain["requirement"])

    async def settled():
        async with await psycopg.AsyncConnection.connect(admin, row_factory=dict_row) as conn:
            return await settled_requirements(conn, chain["project"])

    assert twin_id in asyncio.run(settled())
