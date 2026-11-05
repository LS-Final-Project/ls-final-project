"""KAN-194 7차 그래프의 근거 수 상한과 시도 요약 finish_reasons. MemorySaver와 fake로 검사한다(모델·DB 없음)."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.engine.cs.graph import build_graph, initial_state, thread_config
from tests.cs.graph_fakes import (
    FILTERS,
    TICKET,
    AttemptStore,
    classification_provider,
    draft_provider,
    fts_searcher,
)


def make(**overrides):
    """기본 fake로 그래프를 만든다.

    Args:
        overrides: 바꿀 build_graph 인자.

    Returns:
        CompiledStateGraph: 컴파일한 그래프.
    """
    kwargs = {
        "classification_provider": classification_provider,
        "draft_provider": draft_provider,
        "fts_searcher": fts_searcher,
        "search_filters": FILTERS,
        "allowed_info_codes": frozenset({"TEST_PHOTO"}),
        "load_ticket": lambda ticket_id: TICKET,
        "record_attempt": AttemptStore(),
        "checkpointer": InMemorySaver(),
    } | overrides
    return build_graph(**kwargs)


@pytest.mark.parametrize(("limit", "expected"), [(None, 2), (1, 1), (5, 2)])
def test_evidence_limit_keeps_only_the_top_snippets(limit, expected):
    """evidence_limit은 검색 결과의 앞 N건만 초안 근거로 남기고, None이면 전부 남긴다.

    Args:
        limit: 근거 상한.
        expected: 남는 근거 수.

    Returns:
        None: 근거 순서·개수와 시도 요약의 finish_reasons를 확인한다.
    """
    graph = make(evidence_limit=limit)
    config = thread_config("ticket-k")
    graph.invoke(initial_state("ticket-k"), config)
    values = graph.get_state(config).values
    assert [e["chunk_id"] for e in values["evidence"]] == ["chunk-1", "chunk-2"][:expected]
    assert [a["finish_reasons"] for a in values["attempts"]] == [["STOP"], ["STOP"]]


@pytest.mark.parametrize("bad", [0, -1, True, "5", 2.0])
def test_evidence_limit_rejects_non_positive_integers(bad):
    """evidence_limit은 1 이상 정수(또는 None)만 받는다.

    Args:
        bad: 잘못된 값.

    Returns:
        None: CS_GRAPH_INVALID_EVIDENCE_LIMIT을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_GRAPH_INVALID_EVIDENCE_LIMIT$"):
        make(evidence_limit=bad)
