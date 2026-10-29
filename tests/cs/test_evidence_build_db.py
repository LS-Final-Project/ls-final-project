"""KAN-144 근거 스니펫 생성기의 DB 왕복. 실제 cs.document_chunks에 소형 청크를 넣고 cs_app으로 검색한다."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from backend.agent.cs_search import fts_searcher_from_dsn
from backend.api.cs.search.repository import store_chunks
from backend.engine.documents.model import Block, Position, build_document
from backend.engine.search.chunking import INDEX_VERSION, chunk_document
from backend.engine.search.model import ChunkMetadata

# 백엔드 검사 이미지는 scripts/check_backend.py만 복사하므로 거기서는 건너뛰고, 전체 checkout인 auth-database
# 작업에서 실행한다.
build_evidence = pytest.importorskip("scripts.cs_evidence_build").build_evidence

KNOWN = datetime(2026, 10, 29, 9, 30, tzinfo=UTC)
TEXTS = (
    "Breaker warranty covers trip unit defects for 18 months.",
    "Breaker trip reset follows bulletin FLT-042 steps.",
)


def test_build_evidence_round_trip_excludes_blocked_chunks(cs_database):
    """저장한 두 문서 중 FLT-042 청크는 빼고 나머지를 초안 근거 형식으로 돌려준다.

    Args:
        cs_database: 관리자 DSN.

    Returns:
        None: 근거 청크·차단 건수를 확인한다.
    """
    dsn = make_conninfo(cs_database, user="cs_app", password="test-only")
    results = [
        chunk_document(
            build_document(
                "DOCX",
                f"kan144-evidence-{uuid4()}".encode(),
                [
                    Block(
                        block_id="p1",
                        kind="PARAGRAPH",
                        text=text,
                        position=Position(paragraph_index=0),
                    )
                ],
                [],
                tables=0,
            )
        )
        for text in TEXTS
    ]
    shas = [result.chunks[0].document_sha256 for result in results]

    async def store():
        """두 문서의 청크를 저장한다.

        Returns:
            None: 저장만 한다.
        """
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            for result, sha in zip(results, shas, strict=True):
                await store_chunks(
                    conn,
                    result,
                    document_sha256=sha,
                    metadata=ChunkMetadata(document_kind="CS_MANUAL"),
                    data_origin="synthetic",
                    known_at=KNOWN,
                )

    try:
        asyncio.run(store())
        evidence, meta = build_evidence(
            {
                "T1": {
                    "channel": "EMAIL",
                    "body_language": "en",
                    "subject": "Breaker trip",
                    "body": "Our breaker trips after reset, is warranty valid?",
                }
            },
            fts_searcher_from_dsn(dsn, INDEX_VERSION),
            index_version=INDEX_VERSION,
            document_sha256s=shas,
            limit=5,
            snippet_chars=800,
            sensitive_values={},
            query_from="subject+body",
        )
        assert evidence == {
            "T1": [
                {
                    "chunk_id": results[0].chunks[0].chunk_id,
                    "snippet": results[0].chunks[0].text,
                    "section_path": results[0].chunks[0].section_path,
                    "document_sha256": shas[0],
                }
            ]
        }
        assert (meta["with_evidence"], meta["dropped_blocked"]) == (1, 1)
    finally:
        with psycopg.connect(cs_database, autocommit=True) as conn:
            conn.execute("DELETE FROM cs.document_chunks WHERE document_sha256 = ANY(%s)", (shas,))


def test_product_documents_come_before_better_global_matches(cs_database):
    """전역 FTS 순위가 더 높은 다른 문서가 있어도 제품 문서가 근거 1순위다.

    Args:
        cs_database: 관리자 DSN.

    Returns:
        None: 필터 끔/켬의 1순위 문서와 meta 통계를 확인한다.
    """
    dsn = make_conninfo(cs_database, user="cs_app", password="test-only")
    texts = (
        "Breaker trip unit manual.",
        "Breaker warranty trip reset policy for every breaker warranty trip claim.",
    )
    results = [
        chunk_document(
            build_document(
                "DOCX",
                f"kan144-product-{uuid4()}".encode(),
                [Block(block_id="p1", kind="PARAGRAPH", text=text, position=Position())],
                [],
                tables=0,
            )
        )
        for text in texts
    ]
    product, other = (result.chunks[0].document_sha256 for result in results)

    async def store():
        """두 문서의 청크를 저장한다.

        Returns:
            None: 저장만 한다.
        """
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            for result in results:
                await store_chunks(
                    conn,
                    result,
                    document_sha256=result.chunks[0].document_sha256,
                    metadata=ChunkMetadata(document_kind="CS_MANUAL"),
                    data_origin="synthetic",
                    known_at=KNOWN,
                )

    record = {
        "channel": "EMAIL",
        "body_language": "en",
        "subject": "Breaker warranty trip",
        "body": "Breaker trip warranty claim reset",
    }
    try:
        asyncio.run(store())
        firsts = {}
        for product_filter in (False, True):
            evidence, meta = build_evidence(
                {"T1": record},
                fts_searcher_from_dsn(dsn, INDEX_VERSION),
                index_version=INDEX_VERSION,
                document_sha256s=[product, other],
                limit=5,
                snippet_chars=800,
                sensitive_values={},
                query_from="subject+body",
                product_documents={"T1": frozenset({product})},
                product_filter=product_filter,
            )
            firsts[product_filter] = [row["document_sha256"] for row in evidence["T1"]]
            assert (meta["product_hits"], meta["product_top1"]) == (1, int(product_filter))
        assert firsts == {False: [other, product], True: [product, other]}
    finally:
        with psycopg.connect(cs_database, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM cs.document_chunks WHERE document_sha256 = ANY(%s)",
                ([product, other],),
            )
