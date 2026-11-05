"""Explicit reply application preserves original evidence and checks versions."""

# ruff: noqa: F811
from uuid import uuid4

import psycopg

from tests.tft.test_rfp_http_base_db import admin, app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_routes_db import OUTSIDER, OWNER, answer, ask, call, client, decide, gap  # noqa: F401


def test_reply_source_spec_approval_and_stale_version(client, gap, admin):
    question = ask(
        client,
        gap,
        issue_type="CONDITION_MISSING",
        target="CUSTOMER",
        target_dept=None,
        question_text="BIL 시험 조건",
    ).json()["data"]
    reply = answer(client, question, OWNER).json()["data"]
    assert reply["created_proposal"] is None
    listed = call(client, "GET", f"/projects/{gap['project']}/questions", OWNER).json()["data"][
        "items"
    ]
    assert (
        next(q for q in listed if q["question_id"] == question["question_id"])["reply"]
        == reply["reply"]
    )
    body = {
        "requirement_id": str(gap["requirement"]),
        "requirement_version": 1,
        "source_question_id": question["question_id"],
        "proposed": {
            "spec": {"value": "110", "unit": "kV", "operator": "GTE", "tolerance": None},
            "conditions": [{"name": "duration", "value": "1", "unit": "min"}],
            "unresolved": [],
        },
        "rationale": "고객 회신을 확인함",
        "evidence_refs": ["reply-letter-1"],
    }
    url = f"/projects/{gap['project']}/proposals"
    made = call(client, "POST", url, OWNER, body, uuid4().hex)
    assert made.status_code == 201, made.text
    proposal = made.json()["data"]
    assert proposal["source_ref"]["reply_id"] == reply["reply"]["reply_id"]
    assert proposal["evidence_refs"] == ["reply-letter-1"]
    done = decide(client, proposal)
    assert done.status_code == 200, done.text
    data = done.json()["data"]
    assert data["spec"]["value"] == "110" and data["spec"]["unit"] == "kV"
    assert data["origin"]["quote"] == "BIL 95 kV"
    assert data["judgement"]["status"] == "SPEC_GAP"
    assert call(client, "POST", url, OWNER, body, uuid4().hex).status_code == 409
    body["requirement_version"] = 2
    body["source_question_id"] = str(uuid4())
    assert call(client, "POST", url, OWNER, body, uuid4().hex).status_code == 422
    assert call(client, "POST", url, OUTSIDER, body, uuid4().hex).status_code == 404
    with psycopg.connect(admin) as conn:
        assert conn.execute(
            "SELECT value FROM tft.requirement_versions WHERE requirement_id=%s ORDER BY version",
            (gap["requirement"],),
        ).fetchall() == [("95",), ("110",)]


def test_recompare_incomplete_legacy_is_not_reprocessed(client, gap):
    response = call(
        client,
        "POST",
        f"/requirements/{gap['requirement']}/recompare",
        OWNER,
        {"version": 1},
        uuid4().hex,
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "TFT_RECOMPARE_INPUT_INCOMPLETE"


def test_recompare_is_scoped_idempotent_and_keeps_human_judgement(client, gap, admin, monkeypatch):
    from backend.api.tft.rfp import comparison

    def no_model(*a, **kw):
        raise AssertionError("model must not run")

    monkeypatch.setattr("backend.shared.ai.create_generation_client", no_model, raising=False)
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET kind='SPEC',certification_listed=false,equipment_family='MV_EQUIP',attribute_key='bil' WHERE requirement_id=%s",
            (gap["requirement"],),
        )
    url = f"/requirements/{gap['requirement']}/recompare"
    key = uuid4().hex
    result = call(client, "POST", url, OWNER, {"version": 1}, key)
    assert result.status_code == 200, result.text
    assert (
        call(client, "POST", url, OWNER, {"version": 1}, key).json()["data"]
        == result.json()["data"]
    )
    assert result.json()["data"]["proposal"] is not None
    current = call(client, "GET", f"/requirements/{gap['requirement']}", OWNER).json()["data"]
    assert current["version"] == 1 and current["judgement"]["status"] == "SPEC_GAP"
    assert call(client, "POST", url, OUTSIDER, {"version": 1}, uuid4().hex).status_code == 404
    # A new search with no candidates is persisted and never exposes the earlier candidates.
    monkeypatch.setattr(comparison, "load_own_products", lambda: [])
    empty = call(client, "POST", url, OWNER, {"version": 1}, uuid4().hex)
    assert empty.status_code == 200, empty.text
    assert empty.json()["data"]["comparison"]["candidates"] == []
    viewed = call(client, "GET", f"/requirements/{gap['requirement']}/comparison", OWNER)
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()["data"]["candidates"] == []


def test_draft_input_snapshot_stales_after_reply_and_preserves_old_snapshot(client, chain, app_dsn):
    gap = chain
    from backend.api.tft.rfp import draft_repository as db
    from tests.tft.test_rfp_draft_routes_db import generate
    from tests.tft.test_rfp_http_base_db import run

    generate(app_dsn, gap, uuid4())
    old = run(app_dsn, lambda conn: db.load(conn, gap["project"]))
    assert old.freshness.status == "CURRENT"
    question = ask(
        client, gap, issue_type="CONDITION_MISSING", target="CUSTOMER", target_dept=None
    ).json()["data"]
    answer(client, question, OWNER)
    fresh = run(app_dsn, lambda conn: db.load(conn, gap["project"]))
    assert fresh.freshness.status == "STALE"
    assert fresh.freshness.changed_requirement_ids == [gap["requirement"]]
    frozen = run(app_dsn, lambda conn: db.load_snapshot(conn, gap["project"], 1))
    assert frozen.freshness.status == "CURRENT"
    generate(app_dsn, gap, uuid4())
    latest = run(app_dsn, lambda conn: db.load(conn, gap["project"]))
    assert latest.freshness.status == "CURRENT"


def test_draft_generation_does_not_label_changed_inputs_as_current(
    client, chain, app_dsn, monkeypatch
):
    import pytest

    from backend.api.tft.rfp import drafts
    from backend.shared.errors import ApplicationError
    from tests.tft.test_rfp_draft_routes_db import generate

    question = ask(
        client, chain, issue_type="CONDITION_MISSING", target="CUSTOMER", target_dept=None
    ).json()["data"]
    original = drafts.build

    async def changed(conn, project_id):
        result = await original(conn, project_id)
        await conn.execute(
            "UPDATE tft.customer_questions SET version=version+1 WHERE id=%s",
            (question["question_id"],),
        )
        return result

    monkeypatch.setattr(drafts, "build", changed)
    with pytest.raises(ApplicationError) as exc:
        generate(app_dsn, chain, uuid4())
    assert exc.value.code == "TFT_VERSION_CONFLICT"


def test_recompare_snapshot_is_immutable_after_migration_reapply(admin, app_dsn, chain):
    from pathlib import Path

    import pytest

    from tests.tft.test_rfp_schema_db import apply

    ident = uuid4()
    with psycopg.connect(app_dsn) as conn:
        conn.execute(
            "INSERT INTO tft.requirement_recomparisons(id,requirement_id,requirement_version,result) VALUES (%s,%s,1,'{}')",
            (ident, chain["requirement"]),
        )
    with psycopg.connect(admin, autocommit=True) as conn:
        tables = (
            "requirements",
            "requirement_versions",
            "question_replies",
            "draft_versions",
            "review_versions",
            "exports",
            "rfp_commands",
        )

        def snapshot():
            return {
                table: conn.execute(
                    f"SELECT jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text) FROM tft.{table} r"
                ).fetchone()[0]
                for table in tables
            }

        old_records = snapshot()
        before = conn.execute(
            "SELECT row_to_json(r) FROM tft.requirement_recomparisons r WHERE id=%s", (ident,)
        ).fetchone()
        apply(conn, Path("infra/migrations/tft/20261120_KAN-188_workflow_snapshot.sql"))
        apply(conn, Path("infra/migrations/tft/20261104_KAN-199_tft_worker.sql"))
        after = conn.execute(
            "SELECT row_to_json(r) FROM tft.requirement_recomparisons r WHERE id=%s", (ident,)
        ).fetchone()
        assert before == after
        assert old_records == snapshot()
        assert conn.execute(
            "SELECT has_table_privilege('tft_worker','tft.requirement_recomparisons','SELECT')"
        ).fetchone() == (True,)
    for sql in (
        "UPDATE tft.requirement_recomparisons SET result='{\"changed\":true}' WHERE id=%s",
        "DELETE FROM tft.requirement_recomparisons WHERE id=%s",
    ):
        with psycopg.connect(app_dsn) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql, (ident,))


def test_recompare_rechecks_version_after_waiting_for_an_approval(client, gap, admin):
    import time
    from concurrent.futures import ThreadPoolExecutor

    with psycopg.connect(admin) as held:
        held.execute(
            "SELECT id FROM tft.requirements WHERE id=%s FOR UPDATE", (gap["requirement"],)
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                call,
                client,
                "POST",
                f"/requirements/{gap['requirement']}/recompare",
                OWNER,
                {"version": 1},
                uuid4().hex,
            )
            with psycopg.connect(admin, autocommit=True) as watch:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if watch.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'SELECT id FROM tft.requirements%%')"
                    ).fetchone()[0]:
                        break
                    time.sleep(0.01)
                else:
                    held.rollback()
                    raise AssertionError("recompare never waited for the requirement lock")
            held.execute(
                "INSERT INTO tft.requirement_versions SELECT (jsonb_populate_record(NULL::tft.requirement_versions,to_jsonb(v)||'{\"version\":2}'::jsonb)).* FROM tft.requirement_versions v WHERE requirement_id=%s AND version=1",
                (gap["requirement"],),
            )
            held.execute(
                "UPDATE tft.requirements SET latest_version=2 WHERE id=%s", (gap["requirement"],)
            )
            held.commit()
            result = future.result(timeout=5)
    assert result.status_code == 409, result.text
    assert result.json()["error"]["code"] == "TFT_VERSION_CONFLICT"


def test_recompare_view_only_manager_cannot_write(client, gap, admin):
    from backend.shared.access import Principal

    manager = Principal(
        user_id=uuid4(), roles=frozenset({"manager"}), managed_projects={gap["project"]}
    )
    assert call(client, "GET", f"/requirements/{gap['requirement']}", manager).status_code == 200
    response = call(
        client,
        "POST",
        f"/requirements/{gap['requirement']}/recompare",
        manager,
        {"version": 1},
        uuid4().hex,
    )
    assert response.status_code == 403, response.text
    with psycopg.connect(admin) as conn:
        assert conn.execute(
            "SELECT count(*) FROM tft.requirement_recomparisons WHERE requirement_id=%s",
            (gap["requirement"],),
        ).fetchone() == (0,)


def test_changed_spec_draft_does_not_reuse_an_old_product_match(client, chain, app_dsn):
    from backend.api.tft.rfp import repository
    from tests.tft.test_rfp_draft_routes_db import generate
    from tests.tft.test_rfp_http_base_db import run

    async def matched(conn):
        await repository.insert_comparison(
            conn,
            chain["requirement"],
            {
                "product_code": "SYNTHETIC-OLD-MATCH",
                "lifecycle_status": "ACTIVE",
                "our_value": "95",
                "our_unit": "kV",
                "our_conditions": [],
                "comparable": True,
                "result": "MEETS",
                "evidence": [
                    {
                        "document": "synthetic-proof",
                        "clause": "1",
                        "applicability": None,
                        "limits": None,
                    }
                ],
            },
            matched_attribute="bil",
            search_trace={"filters": [], "documents_searched": 1, "search_failed": False},
            data_origin="synthetic",
        )

    run(app_dsn, matched)
    proposal = call(
        client,
        "POST",
        f"/projects/{chain['project']}/proposals",
        OWNER,
        {
            "requirement_id": str(chain["requirement"]),
            "requirement_version": 1,
            "proposed": {
                "spec": {"value": "110", "unit": "kV", "operator": "GTE", "tolerance": None}
            },
            "rationale": "합성 고객 요구값 변경",
        },
        uuid4().hex,
    )
    assert proposal.status_code == 201, proposal.text
    approved = decide(client, proposal.json()["data"])
    assert approved.status_code == 200, approved.text
    draft = generate(app_dsn, chain, uuid4())
    own = next(part for part in draft.sections if part.section == "OUR_RESPONSE")
    assert not own.sentences
    assert any(row.needs_rereview for part in draft.sections for row in part.sentences)
