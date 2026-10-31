"""C/S 처리 그래프 실행기(KAN-194 2차). 실제 공급자·체크포인터를 build_graph에 꽂고 실행·재개한다.

문의 원문과 시도 저장은 C/S 쪽이 제공하는 TicketSource가 맡는다(KAN-107 범위). 여기에는 인터페이스와 테스트·로컬
시연용 메모리 구현만 두고 DB에 저장하지 않는다. 실행 결과는 상태 요약(단계·다음 노드·오류 코드·시도 ID)만
돌려주며 원문·raw_response는 담지 않는다.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

import psycopg
from google import genai
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from backend.engine.cs.classification_call import ClassificationAttempt
from backend.engine.cs.drafting import DraftOutput
from backend.engine.cs.drafting_call import DraftAttempt
from backend.engine.cs.graph import TicketInputs, build_graph, initial_state, thread_config
from backend.engine.cs.prompt_registry import DEFAULT_VERSIONS
from backend.engine.cs.providers import (
    GenerationKnobs,
    gemini_classification_provider,
    gemini_text_provider,
)
from backend.engine.cs.verification import VerificationOutput
from backend.engine.cs.verification_call import VerificationAttempt
from backend.engine.search.hybrid import Searcher
from backend.engine.search.model import SearchFilters
from backend.shared.ai import AISettings
from backend.shared.settings import Settings


class TicketSource(Protocol):
    """그래프가 노드 안에서 쓰는 문의 입력·시도 저장 경계. C/S 쪽(KAN-107)이 DB로 구현한다."""

    def load_ticket(self, ticket_id: str) -> TicketInputs:
        """문의 입력을 돌려준다.

        Args:
            ticket_id: 문의 ID.

        Returns:
            TicketInputs: 원문 레코드를 포함한 사례 입력. 상태에 넣지 않는다.
        """
        ...

    def record_attempt(
        self,
        kind: str,
        ticket_id: str,
        attempt: ClassificationAttempt | DraftAttempt | VerificationAttempt,
    ) -> str:
        """시도를 저장하고 ID를 돌려준다.

        Args:
            kind: "classification", "draft", "verification".
            ticket_id: 문의 ID.
            attempt: 시도 기록(raw_response 포함).

        Returns:
            str: 시도 ID.
        """
        ...


@dataclass
class InMemoryTicketSource:
    """테스트·로컬 시연용 TicketSource. 시도는 종류만 남기고 raw_response는 버린다."""

    tickets: Mapping[str, TicketInputs]
    attempts: list[tuple[str, str]] = field(default_factory=list)

    def load_ticket(self, ticket_id: str) -> TicketInputs:
        """메모리의 문의 입력을 돌려준다.

        Args:
            ticket_id: 문의 ID.

        Returns:
            TicketInputs: 사례 입력. 없으면 KeyError다.
        """
        return self.tickets[ticket_id]

    def record_attempt(
        self,
        kind: str,
        ticket_id: str,
        attempt: ClassificationAttempt | DraftAttempt | VerificationAttempt,
    ) -> str:
        """시도의 종류와 문의 ID만 남기고 ID를 돌려준다.

        Args:
            kind: 시도 종류.
            ticket_id: 문의 ID.
            attempt: 시도 기록. 저장하지 않는다.

        Returns:
            str: 순번 시도 ID.
        """
        self.attempts.append((kind, ticket_id))
        return f"{kind}-{len(self.attempts)}"


# 제품 경로 초안 기본 설정: 초안 실측 PROPOSED 설정(2026-10-29, adoption draft-proposed-product: flash-lite·
# max_output_tokens 2048·근거 5건). 출력 상한 기본(AISettings 1024)과 근거 20건으로는 초안 JSON이 잘렸다.
# 2026-10-31 50건 측정: 4096에서도 초안 4.3%·검증 9.1%가 잘려 초안·검증 모두 6000으로 올렸다(채택 전).
DRAFT_DEFAULTS: dict[str, int] = {"max_output_tokens": 6000, "evidence_limit": 5}
# 초안·검증 호출 시간 제한. 4096 실측 p95 20.9초·최대 23.8초라 6000이면 약 35초다.
# gemini_timeout_ms 기본(30초)은 공용이라 두고 이 두 호출의 클라이언트만 올린다.
DRAFT_TIMEOUT_MS = 60000
# 분류 출력 상한은 thinking을 포함한다. 공통 기본 1024로는 3.6-flash 50건 중 13건이 잘렸고 4096에서는 0건이었다
# (2026-10-29 실측, classification-remeasure). 공통 기본값은 다른 호출이 쓰므로 그대로 둔다.
CLASSIFICATION_DEFAULTS: dict[str, int] = {"max_output_tokens": 4096}


@dataclass(frozen=True)
class RunOutcome:
    """한 번 실행·재개한 뒤의 상태 요약. 원문·raw_response는 없다."""

    stage: str
    next_nodes: tuple[str, ...]
    errors: list[dict[str, str]]
    attempt_ids: list[str]
    # 시도 요약(원문 없음)과 형식 위반 재호출 수·그중 시도 2가 VALID인 수(KAN-194 4차).
    # retried·recovered는 분류·초안 합산이다. 단계별은 attempts의 kind로 나눈다.
    attempts: list[dict[str, Any]]
    retried: int
    recovered: int


def draft_ai_settings(ai: AISettings) -> AISettings:
    """초안·검증 클라이언트 설정. 시간 제한만 다르다.

    Args:
        ai: 공통 AI 설정.

    Returns:
        AISettings: 시간 제한을 DRAFT_TIMEOUT_MS로 바꾼 사본.
    """
    return ai.model_copy(update={"gemini_timeout_ms": DRAFT_TIMEOUT_MS})


def build_cs_runner(
    ai_settings: AISettings,
    *,
    client: genai.Client,
    ticket_source: TicketSource,
    fts_searcher: Searcher,
    search_filters: SearchFilters,
    checkpointer: BaseCheckpointSaver,
    model: str,
    allowed_info_codes: frozenset[str],
    prompt_version: str = DEFAULT_VERSIONS["classification"],
    allow_tracing: bool = False,
    invalid_output_retries: int = 1,
    draft_client: genai.Client | None = None,
    classification_model: str | None = None,
    classification_knobs: GenerationKnobs | None = None,
    draft_knobs: GenerationKnobs | None = None,
    evidence_limit: int = DRAFT_DEFAULTS["evidence_limit"],
    policy_documents: Mapping[str, str] | None = None,
    verification_enabled: bool = True,
    vector_searcher: Searcher | None = None,
) -> Any:
    """Gemini 분류·초안 공급자를 만들어 C/S 그래프에 꽂는다.

    Args:
        ai_settings: 공급자에 넘길 AI 설정.
        client: create_client로 만든 SDK 클라이언트.
        ticket_source: 문의 입력·시도 저장 경계.
        fts_searcher: hybrid.Searcher 콜러블(cs 검색 저장소를 감싼 것은 호출자가 넘긴다).
        search_filters: 검색 조건.
        checkpointer: make_checkpointer가 연 체크포인터.
        model: 호출할 모델 ID.
        allowed_info_codes: 검수된 정보 코드 집합.
        prompt_version: 분류 프롬프트 판본. 기본은 prompt_registry.DEFAULT_VERSIONS(채택 판본 v3)다.
        allow_tracing: 호출자가 넘기는 Settings.ai_tracing_allowed(KAN-89).
        invalid_output_retries: 형식 위반(CS_INVALID_OUTPUT) 시도 2 재호출 횟수(0|1). build_graph에 넘긴다.
        classification_model: 분류 전용 모델 ID. None이면 model을 쓴다.
        classification_knobs: 분류 공급자의 생성 조절 항목. None이면 CLASSIFICATION_DEFAULTS의 출력 상한이다.
        draft_client: 초안·검증 호출 클라이언트(draft_ai_settings로 만든다). None이면 client.
        draft_knobs: 초안 공급자의 생성 조절 항목. None이면 GenerationKnobs(max_output_tokens=DRAFT_DEFAULTS 값)다.
        evidence_limit: 초안에 넘길 근거 상위 개수(기본 DRAFT_DEFAULTS). build_graph에 넘긴다.
        policy_documents: 정책 문서 doc_id → document_sha256(색인 manifest). 있으면 정책 유형 문의에 정책 문서
            1자리를 예약한다. None이면 예약하지 않는다. build_graph에 넘긴다.
        verification_enabled: 기본 True. 같은 명시 모델의 검증 공급자를 연결한다.
        vector_searcher: 벡터 검색기(cs_search.vector_searcher_from_dsn). None이면 FTS만 쓴다. build_graph에 넘긴다.

    Returns:
        CompiledStateGraph: 사람 확인 직전에 멈추는 그래프. 추적이 요청됐는데 허용되지 않으면 ValueError다.
    """

    def record_attempt(
        kind: str,
        ticket_id: str,
        attempt: ClassificationAttempt | DraftAttempt | VerificationAttempt,
    ) -> str:
        # 분류 시도는 판본을 모른다(공급자가 고름). 같은 공급자에 넘긴 판본을 붙여 저장해 실행 행만 보고도
        # 어떤 프롬프트였는지 알 수 있게 한다. 초안 시도는 자기 판본을 이미 갖고 있다.
        if isinstance(attempt, ClassificationAttempt) and attempt.prompt_version is None:
            attempt = replace(attempt, prompt_version=prompt_version)
        return ticket_source.record_attempt(kind, ticket_id, attempt)

    return build_graph(
        classification_provider=gemini_classification_provider(
            client,
            ai_settings,
            model=classification_model or model,
            allowed_info_codes=allowed_info_codes,
            prompt_version=prompt_version,
            generation=classification_knobs
            or GenerationKnobs(max_output_tokens=CLASSIFICATION_DEFAULTS["max_output_tokens"]),
        ),
        draft_provider=gemini_text_provider(
            draft_client or client,
            ai_settings,
            model=model,
            response_schema=DraftOutput,
            generation=draft_knobs
            or GenerationKnobs(max_output_tokens=DRAFT_DEFAULTS["max_output_tokens"]),
        ),
        verification_provider=gemini_text_provider(
            draft_client or client,
            ai_settings,
            model=model,
            response_schema=VerificationOutput,
            generation=draft_knobs
            or GenerationKnobs(max_output_tokens=DRAFT_DEFAULTS["max_output_tokens"]),
        )
        if verification_enabled
        else None,
        verification_enabled=verification_enabled,
        fts_searcher=fts_searcher,
        vector_searcher=vector_searcher,
        search_filters=search_filters,
        allowed_info_codes=allowed_info_codes,
        load_ticket=ticket_source.load_ticket,
        record_attempt=record_attempt,
        checkpointer=checkpointer,
        allow_tracing=allow_tracing,
        invalid_output_retries=invalid_output_retries,
        evidence_limit=evidence_limit,
        policy_documents=policy_documents,
    )


@contextmanager
def make_checkpointer(settings: Settings) -> Iterator[BaseCheckpointSaver]:
    """설정에 맞는 체크포인터를 열고 끝나면 연결을 닫는다.

    Args:
        settings: database_url이 있으면 그 DSN(cs_app)으로 checkpoint_cs 스키마의 PostgresSaver를 연다.

    Returns:
        Iterator[BaseCheckpointSaver]: PostgresSaver 또는(DSN이 없으면) InMemorySaver. 연결 옵션은
            from_conn_string과 같은 autocommit·prepare_threshold=0·dict_row에 search_path=checkpoint_cs를 더한다.
            setup()은 부르지 않는다(운영자 마이그레이션 C12).
    """
    if settings.database_url is None:
        yield InMemorySaver()
        return
    dsn = make_conninfo(
        settings.database_url.get_secret_value(), options="-c search_path=checkpoint_cs"
    )
    with psycopg.connect(dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row) as conn:
        yield PostgresSaver(conn)


def outcome(graph: Any, thread_id: str) -> RunOutcome:
    """체크포인트의 현재 상태를 요약한다.

    Args:
        graph: 컴파일한 그래프.
        thread_id: 스레드 ID.

    Returns:
        RunOutcome: 단계·다음 노드·오류 코드·시도 ID·시도 요약과 재호출·회복 수.
    """
    state = graph.get_state(thread_config(thread_id))
    values = state.values
    attempts = list(values.get("attempts", []))
    second = [
        a for a in attempts if a["kind"] in {"classification", "draft"} and a["attempt_number"] == 2
    ]
    return RunOutcome(
        stage=values.get("stage", "RECEIVED"),
        next_nodes=tuple(state.next),
        errors=list(values.get("errors", [])),
        attempt_ids=list(values.get("attempt_ids", [])),
        attempts=attempts,
        retried=len(second),
        recovered=sum(a["outcome"] == "VALID" for a in second),
    )


def run_ticket(graph: Any, thread_id: str, ticket_id: str) -> RunOutcome:
    """문의 하나를 처음부터 실행한다. 사람 확인 직전에 멈춘다.

    Args:
        graph: build_cs_runner 결과.
        thread_id: 체크포인트 스레드 ID.
        ticket_id: 문의 ID.

    Returns:
        RunOutcome: 실행 뒤 상태 요약. 같은 thread_id에 체크포인트가 이미 있으면 다시 호출하지 않고
            ValueError("CS_GRAPH_THREAD_EXISTS")다(상태 덮어쓰기·중복 유료 호출 방지).
    """
    if graph.get_state(thread_config(thread_id)).values:
        raise ValueError("CS_GRAPH_THREAD_EXISTS")
    graph.invoke(initial_state(ticket_id), thread_config(thread_id))
    return outcome(graph, thread_id)


def resume(graph: Any, thread_id: str) -> RunOutcome:
    """사람 확인에서 멈춘 스레드를 이어 끝낸다.

    Args:
        graph: build_cs_runner 결과(새 프로세스의 새 인스턴스여도 된다).
        thread_id: 체크포인트 스레드 ID.

    Returns:
        RunOutcome: 재개 뒤 상태 요약. 체크포인트가 없거나 멈춘 곳(next)이 없으면(없는 thread·오타·이미
            끝남) ValueError("CS_GRAPH_NOTHING_TO_RESUME")다.
    """
    if not graph.get_state(thread_config(thread_id)).next:
        raise ValueError("CS_GRAPH_NOTHING_TO_RESUME")
    graph.invoke(None, thread_config(thread_id))
    return outcome(graph, thread_id)
