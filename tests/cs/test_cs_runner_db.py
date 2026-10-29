"""KAN-194 2차 실행기의 PostgresSaver 경로. 실제 PostgreSQL의 checkpoint_cs와 cs_app 권한으로 검사한다."""

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo

from backend.agent.cs_runner import InMemoryTicketSource, make_checkpointer, resume, run_ticket
from backend.engine.cs.graph import build_graph
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
