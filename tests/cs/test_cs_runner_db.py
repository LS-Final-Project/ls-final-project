"""KAN-194 2차 실행기의 PostgresSaver 경로. 실제 PostgreSQL의 checkpoint_cs와 cs_app 권한으로 검사한다."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo

from backend.agent.cs_runner import InMemoryTicketSource, make_checkpointer, resume, run_ticket
from backend.agent.cs_search import fts_searcher_from_dsn
from backend.api.cs.search.repository import store_chunks
from backend.engine.cs.graph import build_graph
from backend.engine.documents.model import Block, Position, build_document
from backend.engine.search.chunking import INDEX_VERSION, chunk_document
from backend.engine.search.model import ChunkMetadata, SearchFilters
from backend.shared.settings import Settings
from tests.cs.graph_fakes import (
    FILTERS,
    TICKET,
    classification_provider,
    draft_provider,
    fts_searcher,
)

THREAD = "kan194-runner-db-thread"


@pytest.fixture
def settings(cs_database):
    """cs_app DSN을 database_url로 둔 설정을 주고, 검사가 만든 체크포인트를 지운다.

    Args:
        cs_database: 마이그레이션을 적용한 전용 DB의 관리자 DSN.

    Returns:
        Settings: database_url이 cs_app인 설정.
    """
    dsn = make_conninfo(cs_database, user="cs_app", password="test-only")
    yield Settings(_env_file=None, database_url=dsn)
    with psycopg.connect(cs_database, autocommit=True) as conn:
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            conn.execute(f"DELETE FROM checkpoint_cs.{table} WHERE thread_id=%s", (THREAD,))


def graph_for(saver, source):
    """fake 공급자로 그래프를 만든다.

    Args:
        saver: 체크포인터.
        source: TicketSource.

    Returns:
        CompiledStateGraph: 그래프.
    """
    return build_graph(
        classification_provider=classification_provider,
        draft_provider=draft_provider,
        fts_searcher=fts_searcher,
        search_filters=FILTERS,
        allowed_info_codes=frozenset({"TEST_PHOTO"}),
        load_ticket=source.load_ticket,
        record_attempt=source.record_attempt,
        checkpointer=saver,
    )


def test_make_checkpointer_keeps_state_between_contexts(settings):
    """make_checkpointer가 연 PostgresSaver로 멈춘 상태를 다음 컨텍스트(새 연결)가 이어 끝낸다.

    Args:
        settings: cs_app 설정.

    Returns:
        None: 종류·중단·재개를 확인한다.
    """
    source = InMemoryTicketSource({"t-1": TICKET})
    with make_checkpointer(settings) as saver:
        assert isinstance(saver, PostgresSaver)
        paused = run_ticket(graph_for(saver, source), THREAD, "t-1")
    assert paused.next_nodes == ("human_review",)
    with make_checkpointer(settings) as saver:
        done = resume(graph_for(saver, source), THREAD)
    assert done.stage == "HUMAN_REVIEW" and done.next_nodes == ()


KNOWN = datetime(2026, 10, 29, 9, 11, tzinfo=UTC)


def test_fts_searcher_from_dsn_round_trip(cs_database):
    """cs_app DSN 어댑터가 저장한 청크를 FTS로 찾아 hybrid 후보로 돌려준다.

    Args:
        cs_database: 관리자 DSN.

    Returns:
        None: 후보 순서·출처와 판본 불일치 거부를 확인한다.
    """
    dsn = make_conninfo(cs_database, user="cs_app", password="test-only")
    blocks = [
        Block(
            block_id="p1",
            kind="PARAGRAPH",
            text="Breakers shall be listed to UL 1066 and tested per UL 1066.",
            position=Position(paragraph_index=0),
        )
    ]
    result = chunk_document(
        build_document("DOCX", f"kan194-{uuid4()}".encode(), blocks, [], tables=0)
    )
    sha256 = result.chunks[0].document_sha256

    async def store():
        """청크를 저장한다.

        Returns:
            int: 저장 행 수.
        """
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            return await store_chunks(
                conn,
                result,
                document_sha256=sha256,
                metadata=ChunkMetadata(document_kind="CS_MANUAL"),
                data_origin="synthetic",
                known_at=KNOWN,
            )

    try:
        assert asyncio.run(store()) == len(result.chunks)
        searcher = fts_searcher_from_dsn(dsn, INDEX_VERSION)
        filters = SearchFilters(document_sha256s=[sha256], index_version=INDEX_VERSION)
        [candidate] = searcher("UL 1066", filters)
        assert candidate.chunk_id == result.chunks[0].chunk_id
        assert candidate.source == "FTS" and candidate.position == 1
        with pytest.raises(ValueError, match="^CS_SEARCH_INDEX_MISMATCH$"):
            searcher("UL 1066", SearchFilters(document_sha256s=None, index_version="other-v9"))
    finally:
        with psycopg.connect(cs_database, autocommit=True) as conn:
            conn.execute("DELETE FROM cs.document_chunks WHERE known_at = %s", (KNOWN,))


def test_fts_searcher_refuses_running_event_loop():
    """실행 중인 이벤트 루프 안에서는 asyncio.run을 쓸 수 없으므로 명확한 코드로 거부한다.

    Args:
        없음.

    Returns:
        None: CS_SEARCH_IN_EVENT_LOOP를 확인한다(연결을 열지 않는다).
    """
    searcher = fts_searcher_from_dsn("postgresql://unused.invalid/x", INDEX_VERSION)
    filters = SearchFilters(document_sha256s=None, index_version=INDEX_VERSION)

    async def inside():
        """루프 안에서 부른다.

        Returns:
            None: 예외를 확인한다.
        """
        with pytest.raises(RuntimeError, match="^CS_SEARCH_IN_EVENT_LOOP$"):
            searcher("UL 1066", filters)

    asyncio.run(inside())
