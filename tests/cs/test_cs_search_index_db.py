"""KAN-99 cs.document_chunks 저장과 FTS 검색. 실제 PostgreSQL과 cs_app 권한으로 검사한다."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from backend.api.cs.search.repository import (
    missing_embeddings,
    search_fts,
    search_vector,
    store_chunks,
    store_embeddings,
    vector_literal,
)
from backend.engine.documents.model import Block, CellState, Hints, Position, build_document
from backend.engine.search.chunking import INDEX_VERSION, chunk_document
from backend.engine.search.model import ChunkingResult, ChunkMetadata

T = TypeVar("T")
KNOWN = datetime(2026, 10, 29, tzinfo=UTC)
TITLE = "Table 1 Breaker ratings"


# 합성 추출 문서 헬퍼. 다른 테스트 패키지 import에 기대지 않도록 test_section_chunking.py에서 옮겨 왔다.
def para(block_id: str, text: str, index: int, **extra: Any) -> Block:
    fields: dict[str, Any] = {
        "block_id": block_id,
        "kind": "PARAGRAPH",
        "text": text,
        "position": Position(paragraph_index=index),
    }
    return Block.model_validate(fields | extra)


def numbered(block_id: str, text: str, index: int, list_text: str, level: int) -> Block:
    hints = Hints(
        style_name=None,
        numbering_level=level,
        list_text=list_text,
        is_heading=False,
        numbering_raw=None,
    )
    return para(block_id, text, index, hints=hints)


def cell(block_id: str, row: int, col: int, text: str, **extra: Any) -> Block:
    fields: dict[str, Any] = {
        "block_id": block_id,
        "kind": "TABLE_CELL",
        "text": text,
        "position": Position(table_index=0, row=row, col=col),
        "cell_state": CellState(col_span=1, merged_with=None, is_empty=not text),
    }
    return Block.model_validate(fields | extra)


def doc(*blocks: Block, data: bytes):
    return build_document("DOCX", data, list(blocks), [], tables=1)


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
def runtime(cs_database):
    """cs_app 연결 문자열을 주고, 검사가 만든 청크를 지운다.

    Args:
        cs_database: 마이그레이션을 적용한 검사 DB 관리자 DSN.

    Returns:
        str: cs_app DSN.
    """
    yield make_conninfo(cs_database, user="cs_app", password="test-only")
    with psycopg.connect(cs_database, autocommit=True) as conn:
        conn.execute("DELETE FROM cs.document_chunks WHERE known_at = %s", (KNOWN,))


def run(dsn: str, call: Callable[[psycopg.AsyncConnection[dict[str, Any]]], Awaitable[T]]) -> T:
    """cs_app 비동기 연결 하나로 저장소 함수를 실행한다.

    Args:
        dsn: cs_app DSN.
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


def test_store_creates_search_vectors_and_reindex_replaces_rows(cs_database, runtime) -> None:
    result = spec_a()
    sha256 = result.chunks[0].document_sha256
    assert store(runtime, result) == len(result.chunks)
    assert store(runtime, result) == len(result.chunks)
    with psycopg.connect(cs_database) as conn:
        rows = conn.execute(
            "SELECT count(*), count(DISTINCT chunk_id), count(*) FILTER (WHERE tsv <> ''::tsvector), "
            "min(derivation_kind), min(document_kind) FROM cs.document_chunks WHERE document_sha256 = %s",
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
    assert hits[0]["text"] == {c.chunk_id: c.text for c in a.chunks}[hits[0]["chunk_id"]]

    [table] = search(runtime, "breaker ratings", [a_sha])
    assert (table["kind"], table["table_title"]) == ("TABLE", TITLE)

    assert {hit["document_sha256"] for hit in search(runtime, "UL 1066", [b_sha])} == {b_sha}
    assert {hit["document_sha256"] for hit in search(runtime, "UL 1066", None)} >= {a_sha, b_sha}


def test_fts_filters_document_kinds(cs_database, runtime) -> None:
    """document_kinds는 청크 메타데이터 document_kind가 그 중 하나인 청크만 찾는다(KAN-194 10차 정책 예약)."""
    a, b = spec_a(), spec_b()
    store(runtime, a)
    store(runtime, b)
    a_sha, b_sha = a.chunks[0].document_sha256, b.chunks[0].document_sha256
    with psycopg.connect(cs_database, autocommit=True) as conn:
        conn.execute(
            "UPDATE cs.document_chunks SET document_kind = 'POL' WHERE document_sha256 = %s",
            (b_sha,),
        )

    def kinds(values: list[str] | None) -> set[str]:
        hits = run(
            runtime,
            lambda conn: search_fts(
                conn,
                "UL 1066",
                document_sha256s=[a_sha, b_sha],
                index_version=INDEX_VERSION,
                document_kinds=values,
            ),
        )
        return {hit["document_sha256"] for hit in hits}

    assert kinds(["POL"]) == {b_sha}
    assert kinds(["RFP_SPEC"]) == {a_sha}
    assert kinds(["POL", "RFP_SPEC"]) == kinds(None) == {a_sha, b_sha}
    assert kinds(["FAQ"]) == set()


def test_runtime_role_cannot_update_and_inputs_are_checked(runtime) -> None:
    result = spec_a()
    store(runtime, result)
    with psycopg.connect(runtime) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE cs.document_chunks SET text = 'x' WHERE known_at = %s", (KNOWN,))
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


def test_reindex_of_same_document_waits_for_advisory_lock(cs_database, runtime) -> None:
    """다른 연결이 같은 문서·판본 키의 잠금을 쥔 동안 재색인은 기다렸다가 해제 후 끝난다."""
    result = spec_a()
    key = f"{result.chunks[0].document_sha256}:{INDEX_VERSION}"
    with (
        psycopg.connect(cs_database) as holder,
        psycopg.connect(cs_database, autocommit=True) as observer,
        ThreadPoolExecutor(1) as pool,
    ):
        holder.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
        pending = pool.submit(store, runtime, result)
        waiting = 0
        for _ in range(100):
            waiting = observer.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
            ).fetchone()[0]
            if waiting:
                break
            time.sleep(0.05)
        assert waiting == 1 and not pending.done()
        holder.commit()
        assert pending.result(timeout=10) == len(result.chunks)


def unit(index: int) -> list[float]:
    """index 축에 1인 768차원 단위 벡터.

    Args:
        index: 1을 둘 축.

    Returns:
        list[float]: 단위 벡터.
    """
    return [1.0 if i == index else 0.0 for i in range(768)]


def test_chunk_embeddings_store_search_isolate_and_cascade(cs_database, runtime) -> None:
    """C20: 없는 것만 고르고, 넣고(재실행 0건), 같은 필터로 코사인 순으로 찾고, 판본을 섞지 않으며 재적재면 지운다(KAN-99)."""
    a, b = spec_a(), spec_b()
    store(runtime, a)
    store(runtime, b)
    a_sha, b_sha = a.chunks[0].document_sha256, b.chunks[0].document_sha256
    version, other = (
        "gemini-embedding-2:768:RETRIEVAL_DOCUMENT",
        "gemini-embedding-001:768:RETRIEVAL_DOCUMENT",
    )
    scope = [a_sha, b_sha]
    missing = [
        r
        for r in run(
            runtime,
            lambda c: missing_embeddings(c, index_version=INDEX_VERSION, embedding_version=version),
        )
        if r["id"]
    ]
    ours = [r for r in missing if r["chunk_id"] in {c.chunk_id for c in (*a.chunks, *b.chunks)}]
    assert len(ours) == len(a.chunks) + len(b.chunks)
    vectors = [(row["id"], unit(n)) for n, row in enumerate(ours)]
    assert run(runtime, lambda c: store_embeddings(c, vectors, embedding_version=version)) == len(
        vectors
    )
    assert run(runtime, lambda c: store_embeddings(c, vectors, embedding_version=version)) == 0
    left = {
        r["chunk_id"]
        for r in run(
            runtime,
            lambda c: missing_embeddings(c, index_version=INDEX_VERSION, embedding_version=version),
        )
    }
    assert not left & {row["chunk_id"] for row in ours}

    def search(vector, **extra):
        return run(
            runtime,
            lambda c: search_vector(
                c,
                vector,
                embedding_version=version,
                index_version=INDEX_VERSION,
                document_sha256s=scope,
                **extra,
            ),
        )

    hits = search(unit(1))
    assert hits[0]["chunk_id"] == ours[1]["chunk_id"] and hits[0]["distance"] < 1e-6
    assert [h["distance"] for h in hits] == sorted(h["distance"] for h in hits)
    assert {h["document_sha256"] for h in search(unit(1), document_kinds=["NONE"])} == set()
    only_b = run(
        runtime,
        lambda c: search_vector(
            c,
            unit(1),
            embedding_version=version,
            index_version=INDEX_VERSION,
            document_sha256s=[b_sha],
        ),
    )
    assert {h["document_sha256"] for h in only_b} == {b_sha}
    assert (
        run(
            runtime,
            lambda c: search_vector(
                c,
                unit(1),
                embedding_version=other,
                index_version=INDEX_VERSION,
                document_sha256s=scope,
            ),
        )
        == []
    )
    with pytest.raises(ValueError):
        vector_literal([1.0] * 767)
    with pytest.raises(ValueError):
        vector_literal([float("nan")] + [0.0] * 767)
    with psycopg.connect(cs_database, autocommit=True) as conn:
        grants = conn.execute(
            "SELECT has_table_privilege('cs_app','cs.chunk_embeddings','UPDATE'), "
            "has_table_privilege('cs_worker','cs.chunk_embeddings','SELECT'), "
            "has_table_privilege('cs_worker','cs.chunk_embeddings','INSERT')"
        ).fetchone()
    assert grants == (False, True, False)
    store(runtime, a)  # 재적재는 청크 행을 바꾸므로 a의 임베딩은 CASCADE로 지워진다.
    with psycopg.connect(cs_database) as conn:
        remaining = conn.execute(
            "SELECT c.document_sha256, count(*) FROM cs.chunk_embeddings e JOIN cs.document_chunks c ON c.id = e.chunk_row_id "
            "WHERE c.document_sha256 = ANY(%s) GROUP BY 1",
            (scope,),
        ).fetchall()
    assert dict(remaining) == {b_sha: len(b.chunks)}
