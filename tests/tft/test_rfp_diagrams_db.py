"""Diagram manifests are immutable, least-privilege, and part of the caller's transaction.

Only AUTH_TEST_DATABASE_URL=.../kan77_auth_test may run PostgreSQL tests. Inputs are
small authored dictionaries; no original files, credentials, or model calls are used.
"""

# ruff: noqa: F811

import asyncio
import copy
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier
from psycopg.types.json import Jsonb

from backend.api.tft.rfp.diagram_repository import read_diagrams, store_diagrams, validate_payload
from tests.tft.test_rfp_http_base_db import admin, chain  # noqa: F401
from tests.tft.test_rfp_schema_db import apply

MIGRATION = Path("infra/migrations/tft/20261117_KAN-188_document_diagrams.sql")
WORKER_GRANT = Path("infra/migrations/tft/20261104_KAN-199_tft_worker.sql")
PAYLOAD = {
    "schema_version": 1,
    "items": [{"source_id": "OCR-DOCX-0001", "graph": None}],
    "warnings": ["단선결선도 연결 후보이며 원문 확인이 필요합니다."],
}


@pytest.fixture
def diagrams_database(admin):
    with psycopg.connect(admin, autocommit=True) as conn:
        apply(conn, MIGRATION)
    return admin


def run_as(dsn, role, work):
    async def run():
        async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
            await conn.execute(SQL("SET LOCAL ROLE {}").format(Identifier(role)))
            return await work(conn)

    return asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**PAYLOAD, "schema_version": True},
        {**PAYLOAD, "schema_version": 2},
        {**PAYLOAD, "items": {}},
        {**PAYLOAD, "items": [None]},
        {**PAYLOAD, "items": [{}] * 17},
        {**PAYLOAD, "warnings": [False]},
        {**PAYLOAD, "warnings": ["x" * 2001]},
        {**PAYLOAD, "warnings": [""] * 513},
        {**PAYLOAD, "items": [{"value": float("nan")}]},
    ],
)
def test_bad_manifest_rejected_before_db(payload):
    conn = AsyncMock()
    with pytest.raises(ValueError, match="TFT_DIAGRAM_MANIFEST_INVALID"):
        asyncio.run(store_diagrams(conn, uuid4(), "0" * 64, payload))
    conn.execute.assert_not_awaited()


def test_manifest_size_limit_and_source_identity_fail_before_db():
    with pytest.raises(ValueError, match="TFT_DIAGRAM_MANIFEST_TOO_LARGE"):
        validate_payload({**PAYLOAD, "items": [{"large": "x" * (4 * 1024 * 1024)}]})
    conn = AsyncMock()
    with pytest.raises(ValueError, match="TFT_DIAGRAM_SOURCE_INVALID"):
        asyncio.run(store_diagrams(conn, uuid4(), "not-a-sha", PAYLOAD))
    conn.execute.assert_not_awaited()


def test_worker_insert_identical_skip_conflict_and_app_read(diagrams_database, chain):
    async def insert(conn):
        return await store_diagrams(conn, chain["docver"], "0" * 64, PAYLOAD)

    assert run_as(diagrams_database, "tft_worker", insert) is True
    before = run_as(diagrams_database, "tft_app", lambda c: read_diagrams(c, chain["docver"]))
    assert before["payload"] == PAYLOAD and before["source_sha256"] == "0" * 64
    assert run_as(diagrams_database, "tft_worker", insert) is False
    altered = copy.deepcopy(PAYLOAD)
    altered["warnings"] = ["different candidate"]
    with pytest.raises(ValueError, match="TFT_DIAGRAM_MANIFEST_CONFLICT"):
        run_as(
            diagrams_database,
            "tft_worker",
            lambda c: store_diagrams(c, chain["docver"], "0" * 64, altered),
        )
    after = run_as(diagrams_database, "tft_app", lambda c: read_diagrams(c, chain["docver"]))
    assert after == before


def test_late_failure_rolls_back_manifest_and_source_mismatch_never_inserts(
    diagrams_database, chain
):
    async def interrupted(conn):
        assert await store_diagrams(conn, chain["docver"], "0" * 64, PAYLOAD)
        raise RuntimeError("AUTHORED_LATE_FAILURE")

    with pytest.raises(RuntimeError, match="AUTHORED_LATE_FAILURE"):
        run_as(diagrams_database, "tft_worker", interrupted)
    assert run_as(diagrams_database, "tft_app", lambda c: read_diagrams(c, chain["docver"])) is None
    for version, sha in [(chain["docver"], "1" * 64), (uuid4(), "0" * 64)]:
        with pytest.raises(ValueError, match="TFT_DIAGRAM_SOURCE_MISMATCH"):
            run_as(
                diagrams_database,
                "tft_worker",
                lambda c: store_diagrams(c, version, sha, PAYLOAD),
            )
    assert run_as(diagrams_database, "tft_app", lambda c: read_diagrams(c, chain["docver"])) is None


def test_permissions_and_worker_grant_reapply_preserve_append_only(diagrams_database, chain):
    with psycopg.connect(diagrams_database, autocommit=True) as conn:
        apply(conn, MIGRATION)
        apply(conn, WORKER_GRANT)
        for role, expected in [
            ("tft_app", [True, False, False, False]),
            ("tft_worker", [True, True, False, False]),
            ("cs_app", [False, False, False, False]),
        ]:
            actual = [
                conn.execute(
                    "SELECT has_table_privilege(%s,'tft.document_diagrams',%s)", (role, privilege)
                ).fetchone()[0]
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
            ]
            assert actual == expected
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        run_as(
            diagrams_database,
            "tft_app",
            lambda c: store_diagrams(c, chain["docver"], "0" * 64, PAYLOAD),
        )
    for sql in (
        "UPDATE tft.document_diagrams SET payload='{}'::jsonb",
        "DELETE FROM tft.document_diagrams",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            run_as(diagrams_database, "tft_worker", lambda c: c.execute(sql))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**PAYLOAD, "schema_version": False},
        {**PAYLOAD, "items": [None] * 17},
        {**PAYLOAD, "warnings": {}},
        {**PAYLOAD, "items": [{"large": "x" * (4 * 1024 * 1024)}]},
    ],
)
def test_database_envelope_constraints_apply_without_repository(diagrams_database, chain, payload):
    with psycopg.connect(diagrams_database) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO tft.document_diagrams(document_version_id,source_sha256,payload) "
                "VALUES(%s,%s,%s)",
                (chain["docver"], "0" * 64, Jsonb(payload)),
            )
