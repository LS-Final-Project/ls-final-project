"""KAN-99 tft.document_chunks 저장과 FTS 검색. 실제 PostgreSQL과 tft_app 권한으로 검사한다."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from backend.api.tft.search.repository import search_fts, store_chunks
from backend.engine.search.chunking import INDEX_VERSION, chunk_document
from backend.engine.search.model import ChunkingResult, ChunkMetadata
from tests.tft.test_section_chunking import cell, doc, numbered, para

T = TypeVar("T")
KNOWN = datetime(2026, 10, 29, tzinfo=UTC)
TITLE = "Table 1 Breaker ratings"


def spec_a() -> ChunkingResult:
    """UL 1066을 여러 번 말하는 절, 표제가 있는 표를 가진 합성 규격서."""
    return chunk_document(
        doc(
            numbered("h1", "Switchgear", 0, "2.6", 1),
            para("p1", "Breakers shall be listed to UL 1066 and tested per UL 1066.", 1),
            numbered("h2", "Meters", 2, "2.7", 1),
            para("p2", "Meters shall comply with UL 1066 where applicable.", 3),
            cell("c1", 0, 0, "Item", table_title=TITLE),
            cell("c2", 0, 1, "Rating", table_title=TITLE),
            cell("c3", 1, 0, "Main", table_title=TITLE),
            cell("c4", 1, 1, "4000 A", table_title=TITLE),
            data=f"spec-a-{uuid4()}".encode(),
        )
    )


def spec_b() -> ChunkingResult:
    return chunk_document(
        doc(
            para("q1", "Switchboards per UL 891 and UL 1066.", 0),
            data=f"spec-b-{uuid4()}".encode(),
        )
    )


@pytest.fixture
def runtime(tft_database):
    """tft_app 연결 문자열을 주고, 검사가 만든 청크를 지운다.

    Args:
        tft_database: 마이그레이션을 적용한 검사 DB 관리자 DSN.

    Returns:
        str: tft_app DSN.
    """
    yield make_conninfo(tft_database, user="tft_app", password="test-only")
    with psycopg.connect(tft_database, autocommit=True) as conn:
        conn.execute("DELETE FROM tft.document_chunks WHERE known_at = %s", (KNOWN,))


def run(dsn: str, call: Callable[[psycopg.AsyncConnection[dict[str, Any]]], Awaitable[T]]) -> T:
    """tft_app 비동기 연결 하나로 저장소 함수를 실행한다.

    Args:
        dsn: tft_app DSN.
        call: 연결을 받는 코루틴 함수.

    Returns:
        코루틴 결과.
    """

    async def main() -> T:
        async with await psycopg.AsyncConnection.connect(
            dsn, row_factory=dict_row, autocommit=True
        ) as conn:
            return await call(conn)

    return asyncio.run(main())


def store(dsn: str, result: ChunkingResult) -> int:
    sha256 = result.chunks[0].document_sha256
    return run(
        dsn,
        lambda conn: store_chunks(
            conn,
            result,
            document_sha256=sha256,
            metadata=ChunkMetadata(document_kind="RFP_SPEC"),
            data_origin="synthetic",
            known_at=KNOWN,
        ),
    )


def search(dsn: str, query: str, sha256s: list[str] | None) -> list[dict[str, Any]]:
    return run(
        dsn,
        lambda conn: search_fts(conn, query, document_sha256s=sha256s, index_version=INDEX_VERSION),
    )


def test_store_creates_search_vectors_and_reindex_replaces_rows(tft_database, runtime) -> None:
    result = spec_a()
    sha256 = result.chunks[0].document_sha256
    assert store(runtime, result) == len(result.chunks)
    assert store(runtime, result) == len(result.chunks)
    with psycopg.connect(tft_database) as conn:
        rows = conn.execute(
            "SELECT count(*), count(DISTINCT chunk_id), count(*) FILTER (WHERE tsv <> ''::tsvector), "
            "min(derivation_kind), min(document_kind) FROM tft.document_chunks WHERE document_sha256 = %s",
            (sha256,),
        ).fetchone()
    assert rows == (
        len(result.chunks),
        len(result.chunks),
        len(result.chunks),
        "DERIVED",
        "RFP_SPEC",
    )


def test_fts_ranks_words_titles_and_filters_documents(runtime) -> None:
    a, b = spec_a(), spec_b()
    store(runtime, a)
    store(runtime, b)
    a_sha, b_sha = a.chunks[0].document_sha256, b.chunks[0].document_sha256
    by_block = {block: chunk.chunk_id for chunk in a.chunks for block in chunk.block_ids}

    hits = search(runtime, "UL 1066", [a_sha])
    assert [hit["chunk_id"] for hit in hits] == [by_block["p1"], by_block["p2"]]
    assert hits[0]["rank"] > hits[1]["rank"] and "<b>" in hits[0]["snippet"]
    assert hits[0]["section_path"] == ["2.6 Switchgear"]

    [table] = search(runtime, "breaker ratings", [a_sha])
    assert (table["kind"], table["table_title"]) == ("TABLE", TITLE)

    assert {hit["document_sha256"] for hit in search(runtime, "UL 1066", [b_sha])} == {b_sha}
    assert {hit["document_sha256"] for hit in search(runtime, "UL 1066", None)} >= {a_sha, b_sha}


def test_runtime_role_cannot_update_and_inputs_are_checked(runtime) -> None:
    result = spec_a()
    store(runtime, result)
    with psycopg.connect(runtime) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE tft.document_chunks SET text = 'x' WHERE known_at = %s", (KNOWN,))
    with pytest.raises(ValueError, match="문서 해시"):
        run(
            runtime,
            lambda conn: store_chunks(
                conn,
                result,
                document_sha256="0" * 64,
                metadata=ChunkMetadata(),
                data_origin="synthetic",
                known_at=KNOWN,
            ),
        )
    with pytest.raises(ValueError, match="검색어"):
        search(runtime, "   ", None)
    with pytest.raises(ValueError, match="limit"):
        run(
            runtime,
            lambda conn: search_fts(
                conn, "UL", document_sha256s=None, index_version=INDEX_VERSION, limit=0
            ),
        )
