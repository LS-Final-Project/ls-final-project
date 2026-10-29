"""KAN-194 C/S 처리 그래프. MemorySaver와 fake 공급자·검색기로 검사한다(모델·DB 없음)."""

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END

from backend.engine.cs.graph import (
    build_graph,
    initial_state,
    route_after_classify,
    thread_config,
)
from tests.cs.graph_fakes import (
    FILTERS,
    RAW_INDENT,
    SECRETS,
    TICKET,
    AttemptStore,
    broken_provider,
    classification_provider,
    draft_provider,
    fts_searcher,
)


def make(checkpointer, **overrides):
    """기본 fake로 그래프를 만든다.

    Args:
        checkpointer: 체크포인터.
        overrides: 바꿀 build_graph 인자.

    Returns:
        tuple: (컴파일한 그래프, 시도 보관소).
    """
    store = AttemptStore()
    kwargs = {
        "classification_provider": classification_provider,
        "draft_provider": draft_provider,
        "fts_searcher": fts_searcher,
        "search_filters": FILTERS,
        "allowed_info_codes": frozenset({"TEST_PHOTO"}),
        "load_ticket": lambda ticket_id: TICKET,
        "record_attempt": store,
        "checkpointer": checkpointer,
    } | overrides
    return build_graph(**kwargs), store


def dump_checkpoints(checkpointer, config):
    """한 스레드의 모든 체크포인트(상태 값·metadata·대기 쓰기)를 문자열로 모은다.

    Args:
        checkpointer: 체크포인터.
        config: thread config.

    Returns:
        str: 직렬화한 내용.
    """
    return json.dumps(
        [
            {"checkpoint": t.checkpoint, "metadata": t.metadata, "writes": t.pending_writes}
            for t in checkpointer.list(config)
        ],
        default=str,
        ensure_ascii=False,
    )


def test_runs_to_human_review_and_resumes_to_end():
    """사람 확인 직전에 멈추고 같은 thread로 재개하면 끝나며, 체크포인트에 원문·raw가 없다.

    Args:
        없음.

    Returns:
        None: 중단점·상태·재개·직렬화를 확인한다.
    """
    saver = InMemorySaver()
    graph, store = make(saver)
    config = thread_config("ticket-1")
    graph.invoke(initial_state("ticket-1"), config)
    paused = graph.get_state(config)
    assert paused.next == ("human_review",)
    values = paused.values
    assert values["stage"] == "DRAFTED" and values["errors"] == []
    assert values["classification"]["category_code"] == "WARRANTY"
    assert values["rules"]["priority"]["priority"] and values["rules"]["policy_version"]
    assert [e["chunk_id"] for e in values["evidence"]] == ["chunk-1", "chunk-2"]
    assert [o["label"] for o in values["draft"]["options"]] == ["CONCISE", "DETAILED", "INFO_FIRST"]
    assert values["attempt_ids"] == ["attempt-1", "attempt-2"]
    assert "[SERIAL_PROVIDED]" in values["record"]["body"]
    assert set(values["record"]) == {"channel", "body_language", "subject", "body"}
    assert [kind for kind, _, _ in store.attempts] == ["classification", "draft"]
    graph.invoke(None, config)
    done = graph.get_state(config)
    assert done.next == () and done.values["stage"] == "HUMAN_REVIEW"
    dumped = dump_checkpoints(saver, config)
    for secret in (*SECRETS, RAW_INDENT, "raw_response"):
        assert secret not in dumped
    assert "configurable" not in json.dumps([t.metadata for t in saver.list(config)])


def test_classification_failure_records_error_and_ends():
    """분류가 실패하면 오류 코드를 남기고 끝나며 규칙·검색·초안은 부르지 않는다.

    Args:
        없음.

    Returns:
        None: 종료 상태와 미호출을 확인한다.
    """
    searcher = Mock()
    drafter = Mock()
    graph, store = make(
        InMemorySaver(),
        classification_provider=broken_provider,
        fts_searcher=searcher,
        draft_provider=drafter,
    )
    config = thread_config("ticket-2")
    graph.invoke(initial_state("ticket-2"), config)
    state = graph.get_state(config)
    assert state.next == ()
    assert state.values["stage"] == "CLASSIFY_FAILED"
    assert state.values["errors"] == [{"node": "classify", "code": "CS_INVALID_OUTPUT"}]
    assert state.values["classification"] is None and state.values["rules"] is None
    assert state.values["record"] is None
    searcher.assert_not_called()
    drafter.assert_not_called()
    assert [kind for kind, _, _ in store.attempts] == ["classification"]


def test_rule_and_search_errors_continue_to_human_review():
    """정책 밖 제품 코드와 검색기 예외는 코드만 남기고 사람 확인까지 간다.

    Args:
        없음.

    Returns:
        None: 오류 코드와 중단점을 확인한다.
    """

    def failing(query, filters):
        """항상 실패하는 검색기.

        Args:
            query: 질의.
            filters: 검색 조건.

        Returns:
            list: 반환하지 않는다.
        """
        raise RuntimeError("PRIVATE_SEARCH_ERROR")

    graph, _ = make(
        InMemorySaver(),
        load_ticket=lambda ticket_id: replace(TICKET, product_code="UNKNOWN-1"),
        fts_searcher=failing,
    )
    config = thread_config("ticket-3")
    graph.invoke(initial_state("ticket-3"), config)
    state = graph.get_state(config)
    assert state.next == ("human_review",)
    codes = [(e["node"], e["code"]) for e in state.values["errors"]]
    assert ("rules", "CS_RULE_INPUT") in codes and ("retrieve", "CS_SEARCH_FAILED") in codes
    # 근거가 없어 fake 초안 공급자가 실패해도 오류 코드만 남기고 사람 확인으로 간다.
    assert ("draft", "CS_PROVIDER_ERROR") in codes and state.values["draft"] is None
    assert "PRIVATE_SEARCH_ERROR" not in json.dumps(state.values)


@pytest.mark.parametrize(
    "classification,expected",
    [({"category_code": "WARRANTY"}, "rules"), (None, END)],
)
def test_route_after_classify(classification, expected):
    """분류 결과 유무로 다음 노드를 고르는 순수 분기.

    Args:
        classification: 분류 결과.
        expected: 기대 다음 노드.

    Returns:
        None: 분기 값을 확인한다.
    """
    state = initial_state("t") | {"classification": classification}
    assert route_after_classify(state) == expected
