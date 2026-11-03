"""Immutable context and review capture against only the disposable kan77_auth_test DB."""

# ruff: noqa: F811
import asyncio
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier

from backend.api.tft.main import create_app
from backend.api.tft.rfp import context_repository as db
from backend.api.tft.rfp import draft_repository
from backend.engine.rfp.context_models import ContextAssignment, ContextResult
from backend.shared.access import Principal, principal_header
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings
from tests.tft.test_rfp_http_base_db import SALES, admin, app_dsn, chain  # noqa: F401


def run(dsn, work, role="tft_worker"):
    async def inside():
        async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
            await conn.execute(SQL("SET LOCAL ROLE {}").format(Identifier(role)))
            return await work(conn)

    return asyncio.run(inside())


def result(source):
    return ContextResult(
        outcome="PARTIAL",
        groups=[],
        assignments=[
            ContextAssignment(
                requirement_id=r.requirement_id,
                primary_category="QUALITY",
                reason="원문 품질 조건 확인",
                evidence_requirement_ids=[r.requirement_id],
                needs_review=True,
            )
            for r in source.requirements
        ],
    )


def seed(dsn, project):
    source = run(dsn, lambda c: db.load_source(c, project))
    value = run(
        dsn, lambda c: db.save_generated(c, project, SALES, uuid4(), source, result(source))
    )
    return source, value


def test_source_fingerprint_append_replay_stale_and_originals_preserved(admin, chain):
    p = chain["project"]
    source = run(admin, lambda c: db.load_source(c, p))
    job = uuid4()
    assert source == run(admin, lambda c: db.load_source(c, p))
    assert source.requirements[0].clause_id == str(chain["clause"])
    assert source.source_refs[0].document_id == chain["document"]
    one = run(admin, lambda c: db.save_generated(c, p, SALES, job, source, result(source)))
    assert one.version == 1 and one.status == "CURRENT"
    assert run(admin, lambda c: db.save_generated(c, p, SALES, job, source, result(source))) == one
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.requirements WHERE project_id=%s", (p,)
            ).fetchone()[0]
            == 1
        )
        conn.execute(
            "UPDATE tft.document_versions SET sha256=%s WHERE id=%s", ("1" * 64, chain["docver"])
        )
    stale = run(admin, lambda c: db.load_context(c, p), role="tft_app")
    assert stale.status == "STALE" and stale.source_fingerprint == source.fingerprint
    assert stale.requirements == one.requirements and stale.source_refs == one.source_refs
    with pytest.raises(ApplicationError, match="TFT_CONTEXT_SOURCE_CHANGED") as error:
        run(admin, lambda c: db.save_generated(c, p, SALES, uuid4(), source, result(source)))
    assert error.value.code == "TFT_CONTEXT_SOURCE_CHANGED"


def test_context_cas_late_failure_and_permissions(admin, chain):
    p = chain["project"]
    old, first = seed(admin, p)
    source = run(admin, lambda c: db.load_source(c, p))

    async def human(conn):
        await db.lock_source(conn, p)
        return await db.append(
            conn,
            p,
            SALES,
            source,
            result(source),
            reason="원문 대조",
            request_key="manual",
            request_hash="0" * 64,
        )

    run(admin, human, role="tft_app")
    with pytest.raises(ApplicationError) as error:
        run(admin, lambda c: db.save_generated(c, p, SALES, uuid4(), source, result(source)))
    assert error.value.code == "VERSION_CONFLICT"
    current = run(admin, lambda c: db.load_source(c, p))

    async def late(conn):
        await db.save_generated(conn, p, SALES, uuid4(), current, result(current))
        raise ValueError("authored late failure")

    with pytest.raises(ValueError, match="authored"):
        run(admin, late)
    assert run(admin, lambda c: db.load_context(c, p)).version == 2
    for role in ("tft_app", "tft_worker"):
        for query in (
            "DELETE FROM tft.context_versions WHERE project_id=%s",
            "UPDATE tft.context_versions SET payload=payload WHERE project_id=%s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                run(admin, lambda c: c.execute(query, (p,)), role=role)
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.requirement_versions WHERE requirement_id=%s",
                (chain["requirement"],),
            ).fetchone()[0]
            == 1
        )


def test_review_captures_exact_context_and_absence_never_backfills(admin, chain):
    p = chain["project"]
    # Tiny authored review fixture: no model, original clauses and requirements stay unchanged.
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.drafts(project_id,latest_version,data_origin,derivation_kind,known_at) VALUES(%s,1,'synthetic','DERIVED',now())",
            (p,),
        )
        conn.execute(
            "INSERT INTO tft.draft_versions(project_id,version,provenance_kind,created_by,data_origin,derivation_kind,known_at) VALUES(%s,1,'system_record',%s,'synthetic','DERIVED',now())",
            (p, SALES),
        )
        for version in (1, 2):
            conn.execute(
                "INSERT INTO tft.review_versions(project_id,review_version,draft_version,judgement_status,saved_by) VALUES(%s,%s,1,'CONDITION_CHECK',%s)",
                (p, version, SALES),
            )
    run(admin, lambda c: draft_repository.capture_review_context(c, p, 1), role="tft_app")
    _, first = seed(admin, p)
    run(admin, lambda c: draft_repository.capture_review_context(c, p, 2), role="tft_app")
    source = run(admin, lambda c: db.load_source(c, p))
    run(admin, lambda c: db.save_generated(c, p, SALES, uuid4(), source, result(source)))
    empty = run(admin, lambda c: draft_repository.load_review_context(c, p, 1))
    frozen = run(admin, lambda c: draft_repository.load_review_context(c, p, 2))
    assert empty.status == "NOT_ANALYZED" and empty.result is None
    assert frozen.version == first.version == 1 and frozen.result == first.result
    assert frozen.requirements == first.requirements and frozen.source_refs == first.source_refs
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        run(
            admin,
            lambda c: c.execute(
                "INSERT INTO tft.review_context_snapshots(project_id,review_version,context_version,snapshot) VALUES(%s,3,1,%s)",
                (uuid4(), "{}"),
            ),
            role="tft_app",
        )


def test_http_correction_idempotency_conflicts_validation_and_scope(admin, app_dsn, chain):
    p = chain["project"]
    source, first = seed(admin, p)
    body = {
        "expected_version": 1,
        "expected_source_fingerprint": source.fingerprint,
        "reason": "원문 재검토",
        "result": first.result.model_dump(mode="json"),
    }
    headers = {
        **principal_header(Principal(user_id=SALES, roles=frozenset({"sales"}))),
        "Idempotency-Key": "context-test",
    }
    with TestClient(
        create_app(Settings(_env_file=None, environment="test", database_url=app_dsn))
    ) as client:
        url = f"/api/v1/tft/projects/{p}/context"
        response = client.patch(url, json=body, headers=headers)
        assert response.status_code == 200, response.text
        saved = response.json()["data"]
        assert saved["version"] == 2 and saved["provenance"] == "human_input"
        assert client.patch(url, json=body, headers=headers).json()["data"] == saved
        assert (
            client.patch(url, json={**body, "reason": "other"}, headers=headers).status_code == 409
        )
        assert (
            client.patch(
                url, json=body, headers={**headers, "Idempotency-Key": "new-key-2"}
            ).status_code
            == 409
        )
        invalid = {**body, "expected_version": 2, "result": {**body["result"], "assignments": []}}
        assert (
            client.patch(
                url, json=invalid, headers={**headers, "Idempotency-Key": "bad-key-2"}
            ).status_code
            == 422
        )
        outsider = principal_header(Principal(user_id=uuid4(), roles=frozenset({"sales"})))
        assert client.get(url, headers=outsider).status_code == 404
        assert (
            client.patch(
                url, json=body, headers={**outsider, "Idempotency-Key": "other-key"}
            ).status_code
            == 404
        )
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.rfp_audit_events WHERE project_id=%s AND action='context.edit'",
                (p,),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "same_version,ancestor_no,linked",
    [
        (True, "OCR-PDF-P0001", True),
        (False, "OCR-PDF-P0001", False),
        (True, "OCR-PDF-P00010", False),
    ],
)
def test_source_diagram_link_requires_exact_version_and_ancestor(same_version, ancestor_no, linked):
    """Authored source-query metadata only; same page/name never imply a diagram link."""
    p, req, doc, version, clause, ancestor = [str(uuid4()) for _ in range(6)]
    diagram_version = version if same_version else str(uuid4())
    row = dict(
        requirement_id=req,
        version=1,
        code="R-1",
        document_id=doc,
        document_project=p,
        document_version_id=version,
        clause_id=clause,
        page=1,
        quote="Transformer tag: T1",
        equipment="Transformer",
        task=None,
        attribute=None,
        value=None,
        unit=None,
        status="INSUFFICIENT_EVIDENCE",
        necessity="MANDATORY",
        conditions=[],
    )
    collected = dict(
        requirements=[row],
        documents=[],
        base_context_version=0,
        clauses=[
            dict(
                id=clause,
                document_version_id=version,
                parent_clause_id=ancestor,
                clause_no="OCR-PDF-P0001.1",
                title="",
                text="synthetic",
                page=1,
            ),
            dict(
                id=ancestor,
                document_version_id=version,
                parent_clause_id=None,
                clause_no=ancestor_no,
                title="synthetic",
                text="synthetic",
                page=1,
            ),
        ],
        diagrams=[
            dict(
                document_version_id=diagram_version,
                source_sha256="a" * 64,
                payload=dict(
                    schema_version=1, items=[dict(source_id="OCR-PDF-P0001")], warnings=[]
                ),
            )
        ],
    )

    class Cursor:
        async def fetchone(self):
            return collected

    class Connection:
        async def execute(self, *args):
            return Cursor()

    result = asyncio.run(db.load_source(Connection(), p))
    assert result.requirements[0].diagram_source_ids == (
        [f"{version}:OCR-PDF-P0001"] if linked else []
    )
    assert result.diagram_source_ids == [f"{diagram_version}:OCR-PDF-P0001"]


def test_storage_rejects_null_payload_schema_version(admin, chain):
    from psycopg.types.json import Jsonb

    with psycopg.connect(admin) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO tft.context_versions(project_id,version,source_job_id,source_fingerprint,source_snapshot,payload,provenance,created_by) VALUES(%s,1,%s,%s,'{}',%s,'ai_draft',%s)",
                (chain["project"], uuid4(), "0" * 64, Jsonb({"schema_version": None}), SALES),
            )
