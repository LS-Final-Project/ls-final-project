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
    """분류가 두 번 모두 형식 위반이면 오류 코드 하나를 남기고 끝나며 규칙·검색·초안은 부르지 않는다.

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
    assert [(kind, a.attempt_number) for kind, _, a in store.attempts] == [
        ("classification", 1),
        ("classification", 2),
    ]
    assert [a["is_final"] for a in state.values["attempts"]] == [False, True]


class Sequence:
    """정해진 순서로 응답하는 공급자. 호출 수를 센다."""

    def __init__(self, *providers):
        """순서를 받는다(마지막 공급자를 이후에도 쓴다).

        Args:
            providers: 호출마다 쓸 공급자.
        """
        self.providers = list(providers)
        self.calls = 0

    def __call__(self, value):
        """다음 공급자로 응답한다.

        Args:
            value: 정제 입력 또는 프롬프트.

        Returns:
            Generation: 그 공급자의 응답.
        """
        provider = self.providers[min(self.calls, len(self.providers) - 1)]
        self.calls += 1
        return provider(value)


def broken_text(prompt):
    """깨진 초안 응답.

    Args:
        prompt: 초안 프롬프트.

    Returns:
        Generation: JSON이 아닌 응답.
    """
    return broken_provider(prompt)


def forbidden_draft(prompt):
    """형식은 맞지만 금지 약속(RMA 승인)이 든 초안.

    Args:
        prompt: 초안 프롬프트.

    Returns:
        Generation: 금지 약속 응답.
    """
    generation = draft_provider(prompt)
    return replace(
        generation, text=generation.text.replace("Synthetic answer.", "Your RMA is approved.")
    )


def test_invalid_output_is_retried_once_and_resume_does_not_call_again():
    """분류·초안 시도 1이 형식 위반이면 시도 2로 회복하고, 두 시도를 모두 남기며, 재개는 다시 부르지 않는다.

    Args:
        없음.

    Returns:
        None: 시도 기록·상태·재개 뒤 호출 수를 확인한다.
    """
    classifier = Sequence(broken_provider, classification_provider)
    drafter = Sequence(broken_text, draft_provider)
    saver = InMemorySaver()
    graph, store = make(saver, classification_provider=classifier, draft_provider=drafter)
    config = thread_config("ticket-r")
    graph.invoke(initial_state("ticket-r"), config)
    values = graph.get_state(config).values
    assert values["stage"] == "DRAFTED" and values["errors"] == []
    assert values["draft"] is not None and values["classification"] is not None
    assert values["attempt_ids"] == [f"attempt-{i}" for i in range(1, 5)]
    assert [
        (a["kind"], a["attempt_number"], a["outcome"], a["error_code"], a["is_final"])
        for a in values["attempts"]
    ] == [
        ("classification", 1, "SCHEMA_VIOLATION", "CS_INVALID_OUTPUT", False),
        ("classification", 2, "VALID", None, True),
        ("draft", 1, "SCHEMA_VIOLATION", "CS_INVALID_OUTPUT", False),
        ("draft", 2, "VALID", None, True),
    ]
    assert (classifier.calls, drafter.calls) == (2, 2)
    graph.invoke(None, config)
    assert graph.get_state(config).values["stage"] == "HUMAN_REVIEW"
    assert (classifier.calls, drafter.calls, len(store.attempts)) == (2, 2, 4)
    dumped = dump_checkpoints(saver, config)
    for secret in (*SECRETS, "RAW_SECRET", "raw_response"):
        assert secret not in dumped


@pytest.mark.parametrize(
    ("drafter", "code", "calls"),
    [
        (forbidden_draft, "CS_FORBIDDEN_COMMITMENT", 1),
        (Mock(side_effect=TimeoutError()), "TIMEOUT", 1),
        (broken_text, "CS_INVALID_OUTPUT", 2),
    ],
    ids=["forbidden", "timeout", "twice-invalid"],
)
def test_only_invalid_output_is_retried(drafter, code, calls):
    """금지 약속·타임아웃은 다시 부르지 않고, 형식 위반은 두 번까지 부른 뒤 사람 확인으로 넘긴다.

    Args:
        drafter: 초안 공급자.
        code: 남는 오류 코드(타임아웃은 outcome 또는 코드).
        calls: 기대 초안 호출 수.

    Returns:
        None: 호출 수·오류·단계를 확인한다.
    """
    counted = Sequence(drafter)
    graph, store = make(InMemorySaver(), draft_provider=counted)
    config = thread_config("ticket-o")
    graph.invoke(initial_state("ticket-o"), config)
    state = graph.get_state(config)
    assert state.next == ("human_review",)
    assert counted.calls == calls
    [error] = state.values["errors"]
    assert error["node"] == "draft" and code in error["code"]
    drafts = [a for a in state.values["attempts"] if a["kind"] == "draft"]
    assert [a["attempt_number"] for a in drafts] == list(range(1, calls + 1))
    assert [a["is_final"] for a in drafts][-1] is True


def test_retry_can_be_turned_off_and_rejects_other_values():
    """invalid_output_retries=0이면 형식 위반을 다시 부르지 않고, 0·1 밖의 값은 그래프를 만들지 않는다.

    Args:
        없음.

    Returns:
        None: 호출 수와 오류를 확인한다.
    """
    classifier = Sequence(broken_provider, classification_provider)
    graph, _ = make(InMemorySaver(), classification_provider=classifier, invalid_output_retries=0)
    config = thread_config("ticket-0")
    # 이 키가 생기기 전 체크포인트처럼 attempts 없이 시작해도 동작한다.
    legacy = {k: v for k, v in initial_state("ticket-0").items() if k != "attempts"}
    graph.invoke(legacy, config)
    values = graph.get_state(config).values
    assert classifier.calls == 1 and values["stage"] == "CLASSIFY_FAILED"
    assert [a["attempt_number"] for a in values["attempts"]] == [1]
    for bad in (2, -1, True, "1"):
        with pytest.raises(ValueError, match="^CS_GRAPH_INVALID_RETRIES$"):
            make(InMemorySaver(), invalid_output_retries=bad)


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


def test_build_graph_refuses_tracing_unless_allowed(monkeypatch):
    """추적 환경 변수가 있으면 allow_tracing 없이는 그래프를 만들지 않는다(Settings 밖 진입점 대비).

    Args:
        monkeypatch: pytest monkeypatch.

    Returns:
        None: 기본 거부와 명시 허용을 확인한다.
    """
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    with pytest.raises(ValueError, match="KAN-89"):
        make(InMemorySaver())
    graph, _ = make(InMemorySaver(), allow_tracing=True)
    assert graph is not None
