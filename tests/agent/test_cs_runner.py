"""KAN-194 2차 C/S 그래프 실행기. 실제 SDK 요청 경로를 쓰되 가짜 HTTP로 네트워크 없이 검사한다."""

import json
from dataclasses import asdict

import google.auth
import httpx
import pytest
from google.oauth2.credentials import Credentials
from langgraph.checkpoint.memory import InMemorySaver

from backend.agent.cs_runner import (
    DRAFT_DEFAULTS,
    InMemoryTicketSource,
    build_cs_runner,
    make_checkpointer,
    resume,
    run_ticket,
)
from backend.engine.cs.graph import thread_config
from backend.engine.cs.providers import GenerationKnobs
from backend.shared.ai import AISettings, create_client
from backend.shared.settings import Settings
from tests.cs.graph_fakes import FILTERS, SECRETS, TICKET, fts_searcher

KEY = "fixture-key-must-not-leak"
CLASSIFICATION = (
    '{"category_code":"WARRANTY","secondary_category":null,"urgency_flag":"NONE",'
    '"provided_info":[],"return_subtype":null}'
)
DRAFT = json.dumps(
    {
        "options": [
            {
                "label": label,
                "sentences": [
                    {
                        "text": "Synthetic answer.",
                        "kind": "EVIDENCE",
                        "evidence_chunk_ids": ["chunk-1"],
                    }
                ],
                "info_requests": (
                    [{"info_code": "TEST_PHOTO", "question": "Please send it."}]
                    if label == "INFO_FIRST"
                    else []
                ),
            }
            for label in ("CONCISE", "DETAILED", "INFO_FIRST")
        ]
    }
)


@pytest.fixture
def sdk_http(monkeypatch):
    """SDK의 httpx 전송을 가짜로 바꿔 분류·초안 응답을 차례로 돌려준다.

    Args:
        monkeypatch: httpx.Client.send 교체.

    Returns:
        list: 보낸 요청 목록.
    """
    return install_http(monkeypatch, [CLASSIFICATION, DRAFT])


def install_http(monkeypatch, texts):
    """SDK의 httpx 전송을 가짜로 바꿔 주어진 응답 본문을 차례로 돌려준다.

    Args:
        monkeypatch: httpx.Client.send 교체.
        texts: 차례로 돌려줄 모델 응답 텍스트.

    Returns:
        list: 보낸 요청 목록.
    """
    requests = []
    answers = iter(texts)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(
        google.auth, "default", lambda **_: (Credentials(token="fixture-token"), "fixture-project")
    )

    def send(_client, request, **kwargs):
        """가짜 응답.

        Args:
            _client: httpx 클라이언트.
            request: 요청.
            kwargs: 전송 옵션.

        Returns:
            httpx.Response: 합성 응답.
        """
        requests.append(request)
        body = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": next(answers)}]},
                    "finishReason": "STOP",
                }
            ],
            "modelVersion": "fixture-version",
            "usageMetadata": {
                "promptTokenCount": 7,
                "candidatesTokenCount": 4,
                "totalTokenCount": 11,
            },
        }
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    return requests


def runner(checkpointer, source, **kwargs):
    """가짜 HTTP 위의 실제 Gemini 공급자로 실행기를 만든다.

    Args:
        checkpointer: 체크포인터.
        source: TicketSource.
        kwargs: 바꿀 인자.

    Returns:
        CompiledStateGraph: 실행기.
    """
    settings = AISettings(gemini_api_key=KEY)
    return build_cs_runner(
        settings,
        client=create_client(settings),
        ticket_source=source,
        fts_searcher=fts_searcher,
        search_filters=FILTERS,
        checkpointer=checkpointer,
        model="gemini-3.6-flash",
        allowed_info_codes=frozenset({"TEST_PHOTO"}),
        **kwargs,
    )


def test_runner_pauses_resumes_and_returns_summary_only(sdk_http):
    """실제 공급자 경로로 사람 확인 직전에 멈추고 재개하며, 요약에 원문·raw·키가 없다.

    Args:
        sdk_http: 가짜 HTTP 요청 목록.

    Returns:
        None: 중단·재개·요약·호출 수를 확인한다.
    """
    source = InMemoryTicketSource({"t-1": TICKET})
    graph = runner(InMemorySaver(), source)
    paused = run_ticket(graph, "thread-1", "t-1")
    assert paused.stage == "DRAFTED" and paused.next_nodes == ("human_review",)
    assert paused.errors == [] and paused.attempt_ids == ["classification-1", "draft-2"]
    assert (paused.retried, paused.recovered) == (0, 0)
    assert len(sdk_http) == 2
    done = resume(graph, "thread-1")
    assert done.stage == "HUMAN_REVIEW" and done.next_nodes == ()
    assert len(sdk_http) == 2
    assert source.attempts == [("classification", "t-1"), ("draft", "t-1")]
    dumped = json.dumps([asdict(paused), asdict(done)])
    for secret in (*SECRETS, KEY, "Synthetic answer", "category_code"):
        assert secret not in dumped
    # 요청 본문에는 마스킹한 입력만 나간다.
    sent = b"".join(request.content for request in sdk_http).decode()
    assert "ZX-PRIVATE" not in sent and "person@example.test" not in sent


@pytest.mark.parametrize(
    ("retries", "second", "stage", "calls", "retried", "recovered"),
    [
        (1, CLASSIFICATION, "DRAFTED", 3, 1, 1),
        (1, "still not json", "CLASSIFY_FAILED", 2, 1, 0),
        (0, CLASSIFICATION, "CLASSIFY_FAILED", 1, 0, 0),
    ],
    ids=["recovered", "failed-twice", "off"],
)
def test_runner_retries_invalid_output_and_counts_it(
    monkeypatch, retries, second, stage, calls, retried, recovered
):
    """분류 시도 1이 형식 위반이면 시도 2로 회복하고 요약에 재호출·회복 수를 내며, 끄면 다시 부르지 않는다.

    Args:
        monkeypatch: 가짜 HTTP 설치.
        retries: invalid_output_retries.
        second: 두 번째 응답.
        stage: 기대 단계.
        calls: 기대 HTTP 호출 수.
        retried: 기대 재호출 수.
        recovered: 기대 회복 수.

    Returns:
        None: 단계·호출 수·요약 수를 확인한다.
    """
    requests = install_http(monkeypatch, ["not json", second, DRAFT])
    source = InMemoryTicketSource({"t-1": TICKET})
    outcome = run_ticket(
        runner(InMemorySaver(), source, invalid_output_retries=retries), "thread-r", "t-1"
    )
    assert (outcome.stage, len(requests), outcome.retried, outcome.recovered) == (
        stage,
        calls,
        retried,
        recovered,
    )
    assert [a["attempt_number"] for a in outcome.attempts if a["kind"] == "classification"] == (
        [1, 2] if retries else [1]
    )


@pytest.mark.parametrize(
    ("kwargs", "tokens", "evidence"),
    [
        ({}, 2048, 2),
        ({"draft_knobs": GenerationKnobs(max_output_tokens=4096), "evidence_limit": 1}, 4096, 1),
    ],
    ids=["defaults", "override"],
)
def test_runner_draft_uses_measured_output_limit_and_evidence_count(
    sdk_http, kwargs, tokens, evidence
):
    """초안 요청은 DRAFT_DEFAULTS 출력 상한(2048)을 보내고 분류 요청은 설정 기본을 쓰며, 근거 수 상한이 적용된다.

    Args:
        sdk_http: 가짜 HTTP 요청 목록.
        kwargs: build_cs_runner에 더할 인자.
        tokens: 초안 요청의 기대 maxOutputTokens.
        evidence: 초안에 넘긴 기대 근거 수(가짜 검색기는 2건을 낸다).

    Returns:
        None: 요청 본문의 출력 상한·근거 수·시도 요약 finish_reasons를 확인한다.
    """
    assert DRAFT_DEFAULTS == {"max_output_tokens": 2048, "evidence_limit": 5}
    graph = runner(InMemorySaver(), InMemoryTicketSource({"t-1": TICKET}), **kwargs)
    outcome = run_ticket(graph, "thread-k", "t-1")
    classify, draft = (json.loads(request.content)["generationConfig"] for request in sdk_http)
    assert classify["maxOutputTokens"] == AISettings(gemini_api_key=KEY).gemini_max_output_tokens
    assert draft["maxOutputTokens"] == tokens
    assert len(graph.get_state(thread_config("thread-k")).values["evidence"]) == evidence
    assert [a["finish_reasons"] for a in outcome.attempts] == [["STOP"], ["STOP"]]


def test_runner_refuses_tracing_unless_allowed(sdk_http, monkeypatch):
    """추적 환경 변수가 있으면 allow_tracing 없이는 실행기를 만들지 않는다.

    Args:
        sdk_http: 가짜 HTTP(호출되지 않음).
        monkeypatch: 환경 변수 설정.

    Returns:
        None: 거부와 명시 허용을 확인한다.
    """
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    source = InMemoryTicketSource({"t-1": TICKET})
    with pytest.raises(ValueError, match="KAN-89"):
        runner(InMemorySaver(), source)
    assert runner(InMemorySaver(), source, allow_tracing=True) is not None
    assert sdk_http == []


def test_make_checkpointer_without_database_is_in_memory():
    """DB 주소가 없으면 메모리 체크포인터다.

    Args:
        없음.

    Returns:
        None: 종류를 확인한다.
    """
    with make_checkpointer(Settings(_env_file=None)) as saver:
        assert isinstance(saver, InMemorySaver)


def fake_graph(saver, source):
    """fake 공급자로 그래프를 만든다(네트워크 없음).

    Args:
        saver: 체크포인터.
        source: TicketSource.

    Returns:
        CompiledStateGraph: 그래프.
    """
    from backend.engine.cs.graph import build_graph
    from tests.cs.graph_fakes import classification_provider, draft_provider

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


def test_run_ticket_refuses_existing_thread():
    """같은 thread_id를 다시 실행하면 상태를 덮거나 공급자를 다시 부르지 않고 거부한다.

    Args:
        없음.

    Returns:
        None: 거부와 시도 수 유지를 확인한다.
    """
    source = InMemoryTicketSource({"t-1": TICKET})
    graph = fake_graph(InMemorySaver(), source)
    run_ticket(graph, "thread-1", "t-1")
    with pytest.raises(ValueError, match="^CS_GRAPH_THREAD_EXISTS$"):
        run_ticket(graph, "thread-1", "t-1")
    assert len(source.attempts) == 2


def test_resume_refuses_unknown_or_finished_thread():
    """체크포인트가 없거나 이미 끝난 thread는 재개하지 않는다.

    Args:
        없음.

    Returns:
        None: 오타 thread와 끝난 thread의 거부를 확인한다.
    """
    source = InMemoryTicketSource({"t-1": TICKET})
    graph = fake_graph(InMemorySaver(), source)
    with pytest.raises(ValueError, match="^CS_GRAPH_NOTHING_TO_RESUME$"):
        resume(graph, "typo-thread")
    run_ticket(graph, "thread-1", "t-1")
    assert resume(graph, "thread-1").stage == "HUMAN_REVIEW"
    with pytest.raises(ValueError, match="^CS_GRAPH_NOTHING_TO_RESUME$"):
        resume(graph, "thread-1")
