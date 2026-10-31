"""KAN-194 2차 C/S 그래프 실행기. 실제 SDK 요청 경로를 쓰되 가짜 HTTP로 네트워크 없이 검사한다."""

import json
from dataclasses import asdict
from types import SimpleNamespace

import google.auth
import httpx
import pytest
from google.oauth2.credentials import Credentials
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr, ValidationError

from backend.agent import cs_runner, cs_search, cs_worker
from backend.agent.cs_runner import (
    CLASSIFICATION_DEFAULTS,
    DRAFT_DEFAULTS,
    InMemoryTicketSource,
    build_cs_runner,
    draft_ai_settings,
    make_checkpointer,
    resume,
    run_ticket,
)
from backend.agent.ticket_source_registry import WorkerParts
from backend.engine.cs.graph import thread_config
from backend.engine.cs.providers import GenerationKnobs
from backend.shared import ai
from backend.shared.ai import AISettings, check_lease, create_client
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

VERIFICATION = json.dumps(
    {
        "sentences": [
            {
                "label": label,
                "sentence_index": 0,
                "verdict": "SUPPORTED",
                "reason": "Synthetic only.",
                "evidence_chunk_ids": ["chunk-1"],
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
    return install_http(monkeypatch, [CLASSIFICATION, DRAFT, VERIFICATION])


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
    assert paused.errors == [] and paused.attempt_ids == [
        "classification-1",
        "draft-2",
        "verification-3",
    ]
    assert (paused.retried, paused.recovered) == (0, 0)
    assert len(sdk_http) == 3
    done = resume(graph, "thread-1")
    assert done.stage == "HUMAN_REVIEW" and done.next_nodes == ()
    assert len(sdk_http) == 3
    assert source.attempts == [("classification", "t-1"), ("draft", "t-1"), ("verification", "t-1")]
    dumped = json.dumps([asdict(paused), asdict(done)])
    for secret in (*SECRETS, KEY, "Synthetic answer", "category_code"):
        assert secret not in dumped
    # 요청 본문에는 마스킹한 입력만 나간다.
    sent = b"".join(request.content for request in sdk_http).decode()
    assert "ZX-PRIVATE" not in sent and "person@example.test" not in sent


def test_runner_stores_the_classification_prompt_version(sdk_http):
    """분류 시도는 판본을 모르므로 실행기가 공급자에 넘긴 판본을 붙여 저장한다(KAN-107).

    Args:
        sdk_http: 가짜 HTTP 요청 목록.

    Returns:
        None: 저장 경계로 넘어간 시도의 판본을 확인한다.
    """
    seen = []

    class Recording(InMemoryTicketSource):
        def record_attempt(self, kind, ticket_id, attempt):
            seen.append((kind, attempt.prompt_version))
            return super().record_attempt(kind, ticket_id, attempt)

    run_ticket(runner(InMemorySaver(), Recording({"t-1": TICKET})), "thread-v", "t-1")
    assert seen == [("classification", "v3"), ("draft", "v3"), ("verification", "v1")]
    seen.clear()
    graph = runner(InMemorySaver(), Recording({"t-1": TICKET}), prompt_version="v2")
    run_ticket(graph, "thread-v2", "t-1")
    assert seen[0] == ("classification", "v2")


@pytest.mark.parametrize(
    ("retries", "second", "stage", "calls", "retried", "recovered"),
    [
        (1, CLASSIFICATION, "DRAFTED", 4, 1, 1),
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
    requests = install_http(monkeypatch, ["not json", second, DRAFT, VERIFICATION])
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
        ({}, 6000, 2),
        ({"draft_knobs": GenerationKnobs(max_output_tokens=4096), "evidence_limit": 1}, 4096, 1),
    ],
    ids=["defaults", "override"],
)
def test_runner_draft_uses_measured_output_limit_and_evidence_count(
    sdk_http, kwargs, tokens, evidence
):
    """초안 요청은 DRAFT_DEFAULTS 출력 상한(6000)을 보내고 분류 요청은 설정 기본을 쓰며, 근거 수 상한이 적용된다.

    Args:
        sdk_http: 가짜 HTTP 요청 목록.
        kwargs: build_cs_runner에 더할 인자.
        tokens: 초안 요청의 기대 maxOutputTokens.
        evidence: 초안에 넘긴 기대 근거 수(가짜 검색기는 2건을 낸다).

    Returns:
        None: 요청 본문의 출력 상한·근거 수·시도 요약 finish_reasons를 확인한다.
    """
    assert DRAFT_DEFAULTS == {"max_output_tokens": 6000, "evidence_limit": 5}
    graph = runner(InMemorySaver(), InMemoryTicketSource({"t-1": TICKET}), **kwargs)
    outcome = run_ticket(graph, "thread-k", "t-1")
    classify, draft, verification = (
        json.loads(request.content)["generationConfig"] for request in sdk_http
    )
    assert classify["maxOutputTokens"] == CLASSIFICATION_DEFAULTS["max_output_tokens"]
    assert draft["maxOutputTokens"] == verification["maxOutputTokens"] == tokens
    assert len(graph.get_state(thread_config("thread-k")).values["evidence"]) == evidence
    assert [a["finish_reasons"] for a in outcome.attempts] == [["STOP"], ["STOP"], ["STOP"]]


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


def test_runner_and_worker_pass_the_policy_mapping_to_the_graph(monkeypatch):
    """build_cs_runner와 cs worker의 build_runner가 정책 문서 매핑을 그래프까지 넘긴다(KAN-194 11차)."""
    seen = []
    monkeypatch.setattr(cs_runner, "build_graph", lambda **kwargs: seen.append(kwargs))
    mapping = {"POL-RETURN": "a" * 64}
    runner(InMemorySaver(), InMemoryTicketSource({}), policy_documents=mapping)
    assert seen[-1]["policy_documents"] == mapping
    monkeypatch.setattr(cs_worker, "create_client", lambda settings: object())
    monkeypatch.setattr(cs_worker, "fts_searcher_from_dsn", lambda dsn, version: fts_searcher)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    parts = WorkerParts(
        ticket_source=SimpleNamespace(load_ticket=None, record_attempt=None),
        allowed_info_codes=frozenset({"TEST_PHOTO"}),
        search_filters=FILTERS,
        policy_documents=mapping,
    )
    settings = Settings(
        _env_file=None, environment="test", database_url=SecretStr("postgresql://x@localhost/y")
    )
    cs_worker.build_runner(settings, parts, InMemorySaver())
    assert seen[-1]["policy_documents"] == mapping


def test_the_worker_gives_classification_its_own_model_and_limit(monkeypatch):
    """분류만 전용 값을 쓴다. 기본 상한은 4096이고 모델은 공통이며, 초안 공급자는 공통 모델 그대로다."""
    classification, text = [], []
    monkeypatch.setattr(cs_runner, "build_graph", lambda **kwargs: None)
    monkeypatch.setattr(
        cs_runner,
        "gemini_classification_provider",
        lambda *args, **kwargs: classification.append(kwargs),
    )
    monkeypatch.setattr(
        cs_runner, "gemini_text_provider", lambda *args, **kwargs: text.append(kwargs)
    )
    monkeypatch.setattr(cs_worker, "create_client", lambda settings, embedding=False: None)
    monkeypatch.setattr(cs_worker, "fts_searcher_from_dsn", lambda dsn, version: fts_searcher)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("GEMINI_MODEL", "m-common")
    monkeypatch.setenv("GEMINI_MAX_OUTPUT_TOKENS", "1024")
    monkeypatch.delenv("CS_VECTOR_SEARCH_ENABLED", raising=False)
    monkeypatch.delenv("CS_WORKER_CLASSIFICATION_MODEL", raising=False)
    monkeypatch.delenv("CS_WORKER_CLASSIFICATION_MAX_OUTPUT_TOKENS", raising=False)
    parts = WorkerParts(
        ticket_source=SimpleNamespace(load_ticket=None, record_attempt=None),
        allowed_info_codes=frozenset({"TEST_PHOTO"}),
        search_filters=FILTERS,
    )
    settings = Settings(
        _env_file=None, environment="test", database_url=SecretStr("postgresql://x@localhost/y")
    )
    cs_worker.build_runner(settings, parts, InMemorySaver())
    assert classification[-1]["model"] == "m-common"
    assert classification[-1]["generation"].max_output_tokens == 4096
    monkeypatch.setenv("CS_WORKER_CLASSIFICATION_MODEL", "m-lite")
    monkeypatch.setenv("CS_WORKER_CLASSIFICATION_MAX_OUTPUT_TOKENS", "2048")
    cs_worker.build_runner(settings, parts, InMemorySaver())
    assert classification[-1]["model"] == "m-lite"
    assert classification[-1]["generation"].max_output_tokens == 2048
    assert {call["model"] for call in text} == {"m-common"}


def test_the_runner_defaults_classification_to_its_own_limit(monkeypatch):
    """worker를 거치지 않는 호출자(로컬 실행기)도 분류 상한은 공통 값이 아니다."""
    seen = []
    monkeypatch.setattr(cs_runner, "build_graph", lambda **kwargs: None)
    monkeypatch.setattr(
        cs_runner, "gemini_classification_provider", lambda *args, **kwargs: seen.append(kwargs)
    )
    monkeypatch.setattr(cs_runner, "gemini_text_provider", lambda *args, **kwargs: None)
    build_cs_runner(
        SimpleNamespace(),
        client=None,
        ticket_source=SimpleNamespace(load_ticket=None, record_attempt=None),
        fts_searcher=fts_searcher,
        search_filters=FILTERS,
        checkpointer=None,
        model="m-common",
        allowed_info_codes=frozenset({"TEST_PHOTO"}),
    )
    assert (seen[-1]["model"], seen[-1]["generation"].max_output_tokens) == ("m-common", 4096)


def test_worker_builds_a_vector_searcher_only_when_enabled(monkeypatch):
    """CS_VECTOR_SEARCH_ENABLED가 없으면 벡터 검색기를 넘기지 않고, 켜면 임베딩 클라이언트로 만들어 그래프까지 넘긴다."""
    seen = []
    clients = []
    monkeypatch.setattr(cs_runner, "build_graph", lambda **kwargs: seen.append(kwargs))
    monkeypatch.setattr(
        cs_worker, "create_client", lambda settings, embedding=False: clients.append(embedding)
    )
    monkeypatch.setattr(cs_worker, "fts_searcher_from_dsn", lambda dsn, version: fts_searcher)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.delenv("CS_VECTOR_SEARCH_ENABLED", raising=False)
    parts = WorkerParts(
        ticket_source=SimpleNamespace(load_ticket=None, record_attempt=None),
        allowed_info_codes=frozenset({"TEST_PHOTO"}),
        search_filters=FILTERS,
    )
    settings = Settings(
        _env_file=None, environment="test", database_url=SecretStr("postgresql://x@localhost/y")
    )
    cs_worker.build_runner(settings, parts, InMemorySaver())
    assert seen[-1]["vector_searcher"] is None and clients == [False, False]
    monkeypatch.setenv("CS_VECTOR_SEARCH_ENABLED", "true")
    cs_worker.build_runner(settings, parts, InMemorySaver())
    searcher = seen[-1]["vector_searcher"]
    assert isinstance(searcher, cs_search.VectorSearcher) and clients[2:] == [False, False, True]
    assert searcher.embedding_version == "gemini-embedding-2:768:RETRIEVAL_DOCUMENT"
    assert searcher.index_version == FILTERS.index_version


def test_vector_settings_accept_the_dimension_as_an_env_string(monkeypatch):
    """환경 변수 CS_EMBEDDING_DIMENSION='768'(문자열)로 만들 수 있고, 768이 아니면 거부한다(.env.example 값)."""
    monkeypatch.setenv("CS_EMBEDDING_DIMENSION", "768")
    assert cs_search.CsVectorSettings().embedding_dimension == 768
    monkeypatch.setenv("CS_EMBEDDING_DIMENSION", "1536")
    with pytest.raises(ValidationError):
        cs_search.CsVectorSettings()


def test_runner_verification_can_be_disabled_without_a_call(monkeypatch):
    requests = install_http(monkeypatch, [CLASSIFICATION, DRAFT])
    graph = runner(
        InMemorySaver(), InMemoryTicketSource({"t-1": TICKET}), verification_enabled=False
    )
    run_ticket(graph, "off", "t-1")
    assert len(requests) == 2
    state = graph.get_state(thread_config("off")).values
    assert state["verification"]["status"] == "NOT_VERIFIED"
    assert state["verification"]["reason"] == "CS_VERIFICATION_DISABLED"


def test_verification_setting_defaults_true_and_reads_environment(monkeypatch):
    monkeypatch.delenv("CS_DRAFT_VERIFICATION_ENABLED", raising=False)
    assert Settings(_env_file=None).cs_draft_verification_enabled is True
    monkeypatch.setenv("CS_DRAFT_VERIFICATION_ENABLED", "false")
    assert Settings(_env_file=None).cs_draft_verification_enabled is False


def test_only_draft_and_verification_get_the_long_client(monkeypatch):
    """분류는 공용 30초 클라이언트, 초안·검증은 60초 클라이언트를 쓴다."""
    seen = []
    monkeypatch.setattr(cs_runner, "build_graph", lambda **kwargs: None)
    monkeypatch.setattr(
        cs_runner,
        "gemini_classification_provider",
        lambda client, *args, **kwargs: seen.append(("classification", client)),
    )
    monkeypatch.setattr(
        cs_runner,
        "gemini_text_provider",
        lambda client, *args, **kwargs: seen.append(("text", client)),
    )
    settings = AISettings(gemini_api_key=KEY)
    build_cs_runner(
        settings,
        client="short",
        draft_client="long",
        ticket_source=InMemoryTicketSource({}),
        fts_searcher=fts_searcher,
        search_filters=FILTERS,
        checkpointer=InMemorySaver(),
        model="gemini-3.6-flash",
        allowed_info_codes=frozenset({"TEST_PHOTO"}),
    )
    assert seen == [("classification", "short"), ("text", "long"), ("text", "long")]
    assert draft_ai_settings(settings).gemini_timeout_ms == 60000
    assert (
        settings.gemini_timeout_ms == AISettings.model_fields["gemini_timeout_ms"].default == 30000
    )


@pytest.mark.parametrize(
    ("lease", "calls", "allowed"),
    [(121, 1, False), (122, 1, True), (242, 2, False), (243, 2, True), (300, 2, True)],
)
def test_lease_must_outlast_the_worst_draft_call(monkeypatch, lease, calls, allowed):
    """호출 하나는 60초 2번과 재시도 대기 1초로 121초, 형식 재호출까지 242초다."""
    monkeypatch.setattr(ai, "TRANSIENT_BACKOFF_SECONDS", 1.0)
    settings = draft_ai_settings(AISettings(gemini_api_key=KEY, gemini_transient_retries=1))
    if allowed:
        check_lease(settings, lease, calls=calls)
    else:
        with pytest.raises(ValueError, match="INVALID_WORKER_CONFIGURATION"):
            check_lease(settings, lease, calls=calls)


def test_the_default_lease_passes_and_a_short_one_stops_the_worker_at_start(monkeypatch):
    called = []
    monkeypatch.setenv("CS_JOB_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.delenv("GEMINI_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("CS_WORKER_LEASE_SECONDS", raising=False)
    monkeypatch.setattr(cs_worker, "execute_job", lambda *args: called.append(args) or "DONE")
    monkeypatch.setattr(cs_worker, "Settings", lambda: None)
    cs_worker.main()
    assert called[0][1].lease_seconds == 300
    # 호출 하나는 덮어도 형식 재호출까지 덮지 못하는 값이다.
    monkeypatch.setenv("CS_WORKER_LEASE_SECONDS", "180")
    with pytest.raises(SystemExit, match="INVALID_WORKER_CONFIGURATION"):
        cs_worker.main()
    assert len(called) == 1
    # 처리 중 난 값 오류는 설정 오류로 바꾸지 않는다.
    monkeypatch.delenv("CS_WORKER_LEASE_SECONDS")

    def broken(*_args):
        raise ValueError("CS_SOMETHING_ELSE")

    monkeypatch.setattr(cs_worker, "execute_job", broken)
    with pytest.raises(ValueError, match="CS_SOMETHING_ELSE"):
        cs_worker.main()
