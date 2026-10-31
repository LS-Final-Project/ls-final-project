"""AI 분석 작업 접수와 발송 뒤 재개(KAN-107). worker 실행은 플랫폼(KAN-194)이 한다.

- 접수: cs 서비스가 문의 접근·역할·상태를 검사한 뒤 플랫폼 `POST /internal/v1/platform/jobs/cs`를 C/S 실행 계정의
  ID 토큰으로 부른다(KAN-110 메일 발송과 같은 방식). 같은 문의에 진행 중 작업이 있으면 409 `CS_JOB_IN_PROGRESS`,
  같은 멱등 키 재전송은 기존 작업이다(플랫폼 규칙). 상태·SSE는 플랫폼 `GET /api/v1/platform/jobs/{id}`·`/events`다.
- 재개: 담당자의 고객 발송이 플랫폼에 접수(ACCEPTED)돼 업무 상태에 반영되면, 그 문의의 마지막 작업 그래프를 사람
  확인 다음으로 이어 끝낸다(2026-10-30 결정). 사람 확인 노드는 표시만 남기므로 모델·검색을 부르지 않는다. 재개
  실패는 발송 결과를 바꾸지 않고 기록만 남긴다(체크포인트 정리 성격).
- 기본 비활성: `CS_ANALYSIS_ENABLED`가 꺼져 있으면 접수는 404이고 재개도 하지 않는다.
"""

import asyncio
import hashlib
import json
import logging
from typing import Any, Self
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, Request
from pydantic_settings import SettingsConfigDict

from backend.api.cs.workflow.mail import CustomerMailSettings, PlatformMail
from backend.shared.service_auth import metadata_token
from backend.shared.settings import Settings

CS_JOBS = "/internal/v1/platform/jobs/cs"
LATEST_JOB = (
    "SELECT job_id FROM cs.ticket_analyses WHERE ticket_id = %s AND job_id IS NOT NULL "
    "ORDER BY run_seq DESC LIMIT 1"
)
logger = logging.getLogger(__name__)
# 재개 실패 시 기다렸다 다시 시도하는 간격(초). 체크포인트 DB의 일시 오류(연결 끊김·재시작)를 넘기려는 것이라
# 짧게 두 번만 한다. 그래도 실패하면 작업 ID를 로그로 남기고, 사람 확인 직전에 멈춘 그래프로 남는다.
RESUME_RETRY_DELAYS = (0.5, 2.0)
# 재개 한 번의 체크포인트 연결 대기 상한(초). 앱 DB 연결(backend/shared/database.py)과 같다.
RESUME_CONNECT_TIMEOUT_S = 3
# 재개 한 번의 체크포인트 문장 실행 상한(밀리초). 연결된 뒤 잠금·과부하로 멈춘 DB에 응답이 묶이지 않게 한다.
# 플랫폼 작업 접수(backend/api/platform/jobs/cs.py)의 statement_timeout 5초와 같다.
RESUME_STATEMENT_TIMEOUT_MS = 5000
# 다시 시도할 오류. 대기가 발송 갱신 응답 안에서 일어나므로 기다려서 나아질 수 있는 것만 둔다.
# psycopg.OperationalError는 연결 실패·관리자 종료·연결 수 초과·교착·직렬화 실패를 포함한다.
# 권한·표 없음(ProgrammingError)이나 그래프·코드 오류는 기다리지 않고 바로 포기한다. 잘못된 DSN·비밀번호도
# psycopg에서는 OperationalError라 재시도되지만, 배포 전체 장애라 로그로 드러난다.
RESUME_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (psycopg.OperationalError,)
# 일시 오류 가운데 다시 하지 않는 것. 문장 상한 초과(QueryCanceled)를 다시 하면 상한이 시도 수만큼 늘어난다.
RESUME_NOT_RETRIED: tuple[type[Exception], ...] = (psycopg.errors.QueryCanceled,)


class CsAnalysisSettings(CustomerMailSettings):
    """플랫폼 작업 접수 주소. 메일 설정과 같은 검사(HTTPS origin)를 쓰고 접두사만 다르다."""

    model_config = SettingsConfigDict(
        env_prefix="CS_ANALYSIS_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    def check(self) -> Self:
        try:
            return super().check()
        except ValueError:
            raise ValueError(
                "CS_ANALYSIS_PLATFORM_URL must be an HTTPS origin without a path"
            ) from None


class PlatformJobs(PlatformMail):
    """플랫폼 내부 작업 접수 클라이언트. 인증·오류 분류는 메일 클라이언트와 같다."""

    # 처리기 미연결 503은 저장하지 않고 거절한 것이다(플랫폼 jobs/cs.py).
    known_server_refusals = frozenset({"CS_PROCESSOR_NOT_CONNECTED"})

    async def create_cs_job(self, body: dict[str, Any]) -> dict[str, Any]:
        """작업을 등록하거나(같은 멱등 키면) 기존 작업을 돌려받는다."""
        return await self._call("POST", CS_JOBS, body)


def configure_analysis_jobs(app: FastAPI, value: CsAnalysisSettings, **overrides: Any) -> None:
    """설정과 플랫폼 클라이언트를 앱에 둔다. 꺼져 있으면 클라이언트가 없다.

    Args:
        app: C/S 앱.
        value: 설정. 켰을 때만 주소를 검사한다.
        **overrides: 검사 주입 `token`(audience → ID 토큰), `transport`, `resume`(재개 함수).
    """
    app.state.analysis_jobs = None
    app.state.analysis_resume = overrides.get("resume") or resume_after_send
    if not value.check().enabled:
        return
    app.state.analysis_jobs = PlatformJobs(
        value.platform_url, overrides.get("token") or metadata_token, overrides.get("transport")
    )


def platform_jobs(request: Request) -> PlatformJobs:
    """플랫폼 작업 클라이언트. 꺼져 있으면 404."""
    client = getattr(request.app.state, "analysis_jobs", None)
    if client is None:
        raise HTTPException(404)
    return client


def input_version(ticket: dict[str, Any]) -> str:
    """처리할 문의 판본의 SHA-256. 그래프 입력이 되는 칸만 넣는다(문의를 고치면 바뀐다).

    Args:
        ticket: 문의 행(channel·body_language·subject·body·product_code).

    Returns:
        str: 정렬 JSON의 hex SHA-256. 플랫폼은 멱등 비교에 이 값을 쓴다.
    """
    frozen = {k: ticket[k] for k in ("channel", "body_language", "subject", "body", "product_code")}
    encoded = json.dumps(frozen, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _no_model(*_args: Any) -> Any:
    raise RuntimeError("CS_RESUME_MUST_NOT_CALL_MODEL")


def resume_graph(settings: Settings, job_id: UUID) -> str:
    """사람 확인 직전에 멈춘 작업 그래프를 이어 끝낸다(동기, 스레드에서 부른다).

    Args:
        settings: C/S 설정(cs_app DSN, checkpoint_cs 권한).
        job_id: 그래프 thread_id인 작업 ID.

    Returns:
        str: RESUMED, 이미 끝났거나 사람 확인에 멈추지 않았으면 NOTHING_TO_RESUME.
    """
    from backend.agent.cs_runner import make_checkpointer
    from backend.engine.cs.graph import HUMAN_REVIEW, build_graph, thread_config
    from backend.engine.search.chunking import INDEX_VERSION
    from backend.engine.search.model import SearchFilters

    with make_checkpointer(
        settings,
        connect_timeout=RESUME_CONNECT_TIMEOUT_S,
        statement_timeout_ms=RESUME_STATEMENT_TIMEOUT_MS,
    ) as saver:
        # 사람 확인 노드는 상태 표시만 남긴다. 모델·검색·입력 함수를 부르면 오류로 드러나게 막아 둔다.
        graph = build_graph(
            classification_provider=_no_model,
            draft_provider=_no_model,
            fts_searcher=_no_model,
            search_filters=SearchFilters(document_sha256s=None, index_version=INDEX_VERSION),
            allowed_info_codes=frozenset({"none"}),
            load_ticket=_no_model,
            record_attempt=_no_model,
            checkpointer=saver,
            allow_tracing=settings.ai_tracing_allowed,
        )
        config = thread_config(str(job_id))
        # cs_runner.resume은 멈춘 곳이 어디든 잇는다. 여기서는 사람 확인 직전에 멈춘 경우만 잇는다(중간에
        # 끊긴 작업을 API 프로세스가 모델 없이 이어 가지 않게).
        if graph.get_state(config).next != (HUMAN_REVIEW,):
            return "NOTHING_TO_RESUME"
        graph.invoke(None, config)
        return "RESUMED"


async def resume_after_send(request: Request, ticket_id: UUID) -> str | None:
    """발송 접수가 업무 상태에 반영된 뒤(트랜잭션 밖) 부른다. 실패해도 발송 결과는 그대로다.

    Args:
        request: 앱 설정·DB.
        ticket_id: 발송한 문의.

    Returns:
        str | None: 재개 결과. 분석 작업이 꺼져 있거나 작업 기록이 없으면 None. 일시 오류
            (RESUME_TRANSIENT_ERRORS, 문장 상한 초과 제외)로 실패하면 RESUME_RETRY_DELAYS만큼 기다려 다시 하고,
            모두 실패하거나 일시 오류가 아니거나 작업 조회부터 실패하면 바로 RESUME_FAILED다.
    """
    if getattr(request.app.state, "analysis_jobs", None) is None:
        return None
    from backend.shared.database import connection

    settings = request.app.state.settings
    try:
        async with connection(settings) as conn:
            row = await (await conn.execute(LATEST_JOB, (ticket_id,))).fetchone()
    except HTTPException:
        # connection()은 DB 오류를 503으로 바꾼다. 발송 상태는 이미 저장됐으므로 갱신 응답을 실패로 만들지 않는다.
        logger.warning(
            "cs analysis resume failed",
            extra={"ticket_id": str(ticket_id), "error_type": "LatestJobLookupFailed"},
        )
        return "RESUME_FAILED"
    if row is None:
        return None
    for delay in (*RESUME_RETRY_DELAYS, None):
        try:
            return await asyncio.to_thread(resume_graph, settings, row["job_id"])
        except Exception as error:
            # 원문·상태를 남기지 않고 작업 ID와 예외 종류만 기록한다.
            extra = {"job_id": str(row["job_id"]), "error_type": type(error).__name__}
            transient = isinstance(error, RESUME_TRANSIENT_ERRORS) and not isinstance(
                error, RESUME_NOT_RETRIED
            )
            if delay is None or not transient:
                logger.warning("cs analysis resume failed", extra=extra)
                return "RESUME_FAILED"
            logger.info("cs analysis resume retry", extra={**extra, "delay_s": delay})
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
