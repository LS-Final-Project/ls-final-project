"""C/S 문의 처리 Cloud Run Job 진입점(KAN-194 9차 3/3). 지정 작업(CS_JOB_ID) 하나만 처리한다.

플랫폼 전송(2/3)이 cs-worker Job을 `CS_JOB_ID`=작업 ID로 실행한다. 이 모듈은 C/S 이미지(SERVICE_NAME=cs)에서
돌므로 `backend.api.platform`을 import하지 않는다.

- 게이트: local/test이고 `CS_WORKER_ENABLED`(기본 꺼짐)일 때만. 로그인 역할은 `cs_worker` 멤버여야 한다.
- 소유권: `runtime.job_control`의 generation·holder·lease. 인수할 때 RUNNING·job.started를 남기고, 노드마다 소유권을
  재검사·연장하며 job.progress를 남긴다. 소유권을 잃으면 아무것도 쓰지 않고 멈춘다(LEASE_LOST).
- 취소 요청(`cancel_requested_at`)이 보이면 다음 노드 전에 멈추고 CANCELLED로 끝낸다.
- 그래프는 thread_id=작업 ID로 돈다. 사람 확인 직전에 멈추거나 끝나면 SUCCEEDED(그래프 안 오류 코드는 상태에 남음),
  worker 예외는 FAILED와 고정 failure_code다. 이벤트에는 원문·오류 코드를 넣지 않는다.
- 재실행: 이미 사람 확인에서 멈춘 스레드는 호출 없이 끝내고, 중간 체크포인트가 있으면 그 뒤부터 잇는다.
"""

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any
from uuid import UUID

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.agent.cs_runner import (
    CLASSIFICATION_DEFAULTS,
    build_cs_runner,
    make_checkpointer,
    outcome,
)
from backend.agent.cs_search import (
    CsVectorSettings,
    fts_searcher_from_dsn,
    vector_searcher_from_settings,
)
from backend.agent.job_runtime import (
    Lease,
    LeaseLost,
    check_role,
    claim,
    connect,
    failure,
    finish,
    parse_job_id,
    progress,
)
from backend.agent.ticket_source_registry import WorkerParts, load_parts
from backend.engine.cs.graph import HUMAN_REVIEW, initial_state, thread_config
from backend.engine.cs.providers import GenerationKnobs
from backend.shared.ai import AISettings, create_client
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

__all__ = ["CsWorkerSettings", "execute_job", "main", "parse_job_id"]

# 진행 이벤트를 남기는 노드(EventPayload.step 값과 같다).
NODES = ("classify", "rules", "retrieve", "draft", "verify", "rewrite", "reverify")
# 입력 load 구간에서 worker가 그대로 인정하는 고정 실패 코드. 그 밖은 CS_WORKER_GRAPH_FAILED로 저장한다.
LOAD_CODES = frozenset({"CS_WORKER_SOURCE_MISSING"})


class CsWorkerSettings(BaseSettings):
    """worker 실행 허용, lease 길이, 분류 전용 모델·출력 상한. 기본 비활성이다."""

    model_config = SettingsConfigDict(env_prefix="CS_WORKER_", extra="ignore")
    enabled: bool = False
    # 노드 하나(재호출 포함 모델 호출)가 이 안에 끝나야 한다. 노드마다 연장한다.
    lease_seconds: int = Field(default=120, ge=10, le=900)
    # 없으면 공통 GEMINI_MODEL을 분류·초안에 함께 쓴다. 바꾸려면 채택 기록이 필요하다(eval/promotion.py).
    classification_model: str | None = None
    classification_max_output_tokens: int = Field(
        default=CLASSIFICATION_DEFAULTS["max_output_tokens"], ge=1, le=65536
    )


def run_graph(graph: Any, lease: Lease, on_node: Callable[[dict[str, Any]], bool]) -> bool:
    """체크포인트를 보고 처음부터·이어서 실행하거나 이미 멈춘 스레드는 그대로 둔다.

    Args:
        graph: 컴파일한 C/S 그래프.
        lease: 소유권(thread_id=작업 ID, 문의 ID).
        on_node: 노드가 끝날 때마다 {step, stage}로 부른다. False면 멈춘다.

    Returns:
        bool: 끝까지(사람 확인 직전) 갔으면 True, on_node가 멈췄으면 False.
    """
    config = thread_config(str(lease.job_id))
    current = graph.get_state(config)
    if current.values and tuple(current.next) == (HUMAN_REVIEW,):
        return True
    inputs = None if current.values else initial_state(str(lease.subject_id))
    for update in graph.stream(inputs, config, stream_mode="updates"):
        for node in update:
            if node in NODES:
                stage = graph.get_state(config).values.get("stage")
                if not on_node(
                    {
                        "step": "draft" if node in {"verify", "rewrite", "reverify"} else node,
                        "stage": stage,
                    }
                ):
                    return False
    return True


def summary(graph: Any, lease: Lease) -> dict[str, Any]:
    """종료 이벤트에 넣을 상태 요약(원문·오류 코드 없음).

    Args:
        graph: 컴파일한 그래프.
        lease: 소유권.

    Returns:
        dict: stage·retried·recovered와 있으면 분류·초안 최종 outcome.
    """
    result = outcome(graph, str(lease.job_id))
    values: dict[str, Any] = {
        "stage": result.stage,
        "retried": result.retried,
        "recovered": result.recovered,
    }
    for kind in ("classification", "draft"):
        tries = [a for a in result.attempts if a["kind"] == kind]
        if tries:
            values[f"{kind}_outcome"] = tries[-1]["outcome"]
    return values


def build_runner(settings: Settings, parts: WorkerParts, checkpointer: BaseCheckpointSaver) -> Any:
    """Gemini 공급자와 cs FTS 검색기(CS_VECTOR_SEARCH_ENABLED면 벡터 검색기도)로 그래프를 만든다.

    Args:
        settings: worker 설정(DSN·추적 허용).
        parts: 문의 출처와 정책 입력.
        checkpointer: 열린 체크포인터.

    Returns:
        CompiledStateGraph: 사람 확인 직전에 멈추는 그래프.
    """
    assert settings.database_url is not None
    assert parts.allowed_info_codes is not None and parts.search_filters is not None
    ai = AISettings()
    worker = CsWorkerSettings()
    dsn = settings.database_url.get_secret_value()
    index_version = parts.search_filters.index_version
    vector = CsVectorSettings()
    return build_cs_runner(
        ai,
        client=create_client(ai),
        ticket_source=parts.ticket_source,
        fts_searcher=fts_searcher_from_dsn(dsn, index_version),
        vector_searcher=vector_searcher_from_settings(
            dsn, index_version, vector, create_client(ai, embedding=True)
        )
        if vector.vector_search_enabled
        else None,
        search_filters=parts.search_filters,
        checkpointer=checkpointer,
        model=ai.gemini_model,
        classification_model=worker.classification_model,
        classification_knobs=GenerationKnobs(
            max_output_tokens=worker.classification_max_output_tokens
        ),
        allowed_info_codes=parts.allowed_info_codes,
        allow_tracing=settings.ai_tracing_allowed,
        verification_enabled=settings.cs_draft_verification_enabled,
        policy_documents=parts.policy_documents,
    )


def execute_job(
    settings: Settings,
    worker: CsWorkerSettings,
    job_id: UUID,
    *,
    parts_loader: Callable[[Settings], WorkerParts] = load_parts,
    build: Callable[[Settings, WorkerParts, BaseCheckpointSaver], Any] = build_runner,
    checkpointer: Callable[
        [Settings], AbstractContextManager[BaseCheckpointSaver]
    ] = make_checkpointer,
) -> str:
    """지정 작업 하나를 인수해 그래프를 돌리고 상태를 확정한다.

    Args:
        settings: worker DB·실행 환경.
        worker: 실행 허용과 lease 길이.
        job_id: CS_JOB_ID.
        parts_loader: 문의 출처·정책 입력(검사에서 바꿔 끼운다).
        build: 그래프 생성(검사에서 가짜 공급자로 바꿔 끼운다).
        checkpointer: 체크포인터 열기.

    Returns:
        str: CS_JOB_SUCCEEDED·CS_JOB_CANCELLED·NOT_CLAIMED·LEASE_LOST. 실패는 작업을 FAILED로 남기고
            ApplicationError(고정 코드)를 올린다.
    """
    if settings.environment not in {"local", "test"} or not worker.enabled:
        raise ApplicationError(503, "CS_WORKER_DISABLED", "C/S worker is disabled.")
    with connect(settings) as conn:
        check_role(conn, "cs_worker")
    lease = claim(settings, "cs", worker.lease_seconds, job_id)
    if lease is None:
        return "NOT_CLAIMED"
    if lease == "CANCELLED":
        return "CS_JOB_CANCELLED"
    # 입력 load 구간에서만 CS_WORKER_SOURCE_MISSING을 인정한다. 처리 구간의 같은 코드는 GRAPH_FAILED다.
    try:
        parts = parts_loader(settings)
    except Exception as error:
        raise failure(settings, lease, error, LOAD_CODES, "CS_WORKER_GRAPH_FAILED") from None
    try:
        with checkpointer(settings) as saver:
            graph = build(settings, parts, saver)
            if not run_graph(
                graph, lease, lambda p: progress(settings, worker.lease_seconds, lease, p)
            ):
                finish(settings, lease, "CANCELLED", {})
                return "CS_JOB_CANCELLED"
            finish(settings, lease, "SUCCEEDED", summary(graph, lease))
            return "CS_JOB_SUCCEEDED"
    except LeaseLost:
        return "LEASE_LOST"
    except Exception as error:
        # 문의 출처(KAN-107) 등 다른 코드가 올린 코드는 그대로 저장하지 않는다(신뢰 경계).
        raise failure(settings, lease, error, frozenset(), "CS_WORKER_GRAPH_FAILED") from None


def main() -> None:
    """Jobs override로 받은 작업만 처리하고 실패를 고정 코드로 종료한다.

    Args:
        없음. 작업 ID와 설정은 서버 환경에서 읽는다.

    Returns:
        None: 성공·미인수·취소·소유권 상실은 정상 종료, 입력·권한·처리 실패는 비정상 종료다.
    """
    try:
        job_id = parse_job_id(os.getenv("CS_JOB_ID"))
        result = execute_job(Settings(), CsWorkerSettings(), job_id)
    except ApplicationError as error:
        raise SystemExit(error.code) from None
    except ValidationError:
        raise SystemExit("INVALID_WORKER_CONFIGURATION") from None
    except psycopg.Error:
        raise SystemExit("DEPENDENCY_UNAVAILABLE") from None
    print(result)


if __name__ == "__main__":
    main()
