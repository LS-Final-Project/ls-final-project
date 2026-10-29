"""KAN-194 C/S 그래프 체크포인트. 실제 PostgreSQL의 checkpoint_cs 스키마와 cs_app 권한으로 검사한다."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from backend.agent.cs_search import fts_searcher_from_dsn
from backend.api.cs.search.repository import store_chunks
from backend.engine.cs.graph import build_graph, initial_state, thread_config
from backend.engine.documents.model import Block, Position, build_document
from backend.engine.search.chunking import INDEX_VERSION, chunk_document
from backend.engine.search.model import ChunkMetadata
from tests.cs.graph_fakes import (
    FILTERS,
    RAW_INDENT,
    SECRETS,
    TICKET,
    AttemptStore,
    classification_provider,
    draft_provider,
    fts_searcher,
)

THREAD = "kan194-db-thread"


@pytest.fixture
def app_dsn(cs_database):
    """cs_app이 checkpoint_cs 스키마를 쓰는 연결 문자열을 주고, 검사가 만든 체크포인트를 지운다.

    Args:
        cs_database: 마이그레이션을 적용한 전용 DB의 관리자 DSN.

    Returns:
        str: cs_app DSN(search_path=checkpoint_cs).
    """
    yield make_conninfo(
        cs_database, user="cs_app", password="test-only", options="-c search_path=checkpoint_cs"
    )
    with psycopg.connect(cs_database, autocommit=True) as conn:
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            conn.execute(f"DELETE FROM checkpoint_cs.{table} WHERE thread_id=%s", (THREAD,))


def saver_graph(conn):
    """한 연결의 PostgresSaver로 그래프를 만든다. setup()은 부르지 않는다(운영자 마이그레이션).

    Args:
        conn: cs_app 연결.

    Returns:
        CompiledStateGraph: 그래프.
    """
    return build_graph(
        classification_provider=classification_provider,
        draft_provider=draft_provider,
        fts_searcher=fts_searcher,
        search_filters=FILTERS,
        allowed_info_codes=frozenset({"TEST_PHOTO"}),
        load_ticket=lambda ticket_id: TICKET,
        record_attempt=AttemptStore(),
        checkpointer=PostgresSaver(conn),
    )


def connect(dsn):
    """PostgresSaver.from_conn_string과 같은 옵션으로 연결한다.

    Args:
        dsn: cs_app DSN.

    Returns:
        psycopg.Connection: autocommit·prepare_threshold=0·dict_row 연결.
    """
    return psycopg.connect(dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row)


def test_checkpoint_survives_new_connection_and_graph(app_dsn, cs_database):
    """사람 확인에서 멈춘 상태를 새 연결·새 그래프가 같은 thread로 이어 끝내고, 저장 행에 원문이 없다.

    Args:
        app_dsn: cs_app DSN.
        cs_database: 관리자 DSN.

    Returns:
        None: 프로세스 밖 보관·재개·직렬화·권한을 확인한다.
    """
    config = thread_config(THREAD)
    with connect(app_dsn) as conn:
        graph = saver_graph(conn)
        graph.invoke(initial_state(THREAD), config)
        assert graph.get_state(config).next == ("human_review",)
    with connect(app_dsn) as conn:
        graph = saver_graph(conn)
        paused = graph.get_state(config)
        assert paused.next == ("human_review",) and paused.values["stage"] == "DRAFTED"
        graph.invoke(None, config)
        done = graph.get_state(config)
        assert done.next == () and done.values["stage"] == "HUMAN_REVIEW"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM checkpoints WHERE thread_id=%s", (THREAD,))
    with psycopg.connect(cs_database) as admin:
        rows = admin.execute(
            "SELECT checkpoint::text || metadata::text FROM checkpoint_cs.checkpoints "
            "WHERE thread_id=%s UNION ALL "
            "SELECT encode(coalesce(blob, ''), 'escape') FROM checkpoint_cs.checkpoint_blobs "
            "WHERE thread_id=%s UNION ALL "
            "SELECT encode(blob, 'escape') FROM checkpoint_cs.checkpoint_writes WHERE thread_id=%s",
            (THREAD, THREAD, THREAD),
        ).fetchall()
    stored = "\n".join(row[0] for row in rows)
    assert "chunk-1" in stored  # 상태가 실제로 저장됐다.
    for secret in (*SECRETS, RAW_INDENT, "raw_response"):
        assert secret not in stored


def test_retrieve_puts_product_family_chunks_first(cs_database):
    """실제 cs.document_chunks에서 문의 제품 계열(TRANSFORMER) 청크가 다른 계열보다 먼저 근거가 되고, 다른 계열은
    전체 검색으로 보충되며, 질의는 문의 전문 AND가 아니라 토큰 OR라서 일부 어휘만 맞는 청크도 찾는다.

    Args:
        cs_database: 관리자 DSN.

    Returns:
        None: 근거 순서를 확인한다.
    """
    dsn = make_conninfo(cs_database, user="cs_app", password="test-only")
    known = datetime(2026, 10, 29, 21, 0, tzinfo=UTC)
    # 다른 계열 문서가 질의 어휘를 더 많이 담아 전체 순위로는 앞선다.
    docs = (
        ("LV_BREAKER", "Breaker trips at 300 V. Breaker trips repeatedly; check the trip unit."),
        ("TRANSFORMER", "Transformer protection trips at overload."),
    )
    results = [
        chunk_document(
            build_document(
                "DOCX",
                f"kan194-retrieve-{family}-{uuid4()}".encode(),
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
        for family, text in docs
    ]
    shas = [result.chunks[0].document_sha256 for result in results]

    async def store():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            for (family, _), result, sha in zip(docs, results, shas, strict=True):
                await store_chunks(
                    conn,
                    result,
                    document_sha256=sha,
                    metadata=ChunkMetadata(document_kind="CS_MANUAL", product_family=family),
                    data_origin="synthetic",
                    known_at=known,
                )

    try:
        asyncio.run(store())
        graph = build_graph(
            classification_provider=classification_provider,
            draft_provider=draft_provider,
            fts_searcher=fts_searcher_from_dsn(dsn, INDEX_VERSION),
            search_filters=FILTERS.model_copy(
                update={"document_sha256s": shas, "index_version": INDEX_VERSION}
            ),
            allowed_info_codes=frozenset({"TEST_PHOTO"}),
            load_ticket=lambda ticket_id: TICKET,
            record_attempt=AttemptStore(),
            checkpointer=InMemorySaver(),
        )
        config = thread_config("kan194-retrieve-db")
        graph.invoke(initial_state("kan194-retrieve-db"), config)
        evidence = graph.get_state(config).values["evidence"]
        assert [item["document_sha256"] for item in evidence] == [shas[1], shas[0]]
        assert "<b>" not in evidence[0]["snippet"]
    finally:
        with psycopg.connect(cs_database, autocommit=True) as conn:
            conn.execute("DELETE FROM cs.document_chunks WHERE document_sha256 = ANY(%s)", (shas,))
