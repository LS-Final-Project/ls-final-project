"""저장 이벤트를 제한된 크기로 재생하고 연결 중 접근 권한을 재검사한다."""

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.api.platform.evaluations.access import require_evaluator
from backend.api.platform.evaluations.admission import check_actor
from backend.api.platform.jobs.status import read_evaluation_job
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0
BATCH_SIZE = 100
TERMINAL_STATES = frozenset({"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"})


class EventPayload(BaseModel):
    """내부 generation·오류 코드·임의 문자열을 제외한 합성 이벤트 허용 필드."""

    model_config = ConfigDict(extra="ignore", strict=True)
    state: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"] | None = None
    step: Literal["smoke"] | None = None
    action: Literal["cancel", "resume"] | None = None
    resumed: bool | None = None
    synthetic: bool | None = None
    needs_review: bool | None = None


class JobEvent(BaseModel):
    """SSE의 JSON 본문이며 자유형 원문을 허용하지 않는다."""

    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    sequence: int = Field(gt=0)
    type: Literal["job.queued", "job.started", "job.progress", "job.completed", "job.failed"]
    occurred_at: datetime
    payload: EventPayload


@dataclass(frozen=True)
class EventBatch:
    """동일 읽기 스냅샷의 이벤트와 종료 위치."""

    events: list[JobEvent]
    terminal: bool
    last_sequence: int


def event_cursor(last_event_id: str | None, after: str | None) -> int:
    """헤더 우선 규칙으로 PostgreSQL bigint 범위의 비음수 커서를 해석한다.

    Args:
        last_event_id: Last-Event-ID 헤더. 빈 헤더도 잘못된 입력이다.
        after: 헤더가 없을 때 사용할 쿼리 값.

    Returns:
        int: 첫 연결은0, 잘못된 값은400으로 거부한다.
    """
    value = last_event_id if last_event_id is not None else after
    if value is None:
        return 0
    if not re.fullmatch(r"[0-9]{1,19}", value) or int(value) > 9223372036854775807:
        raise ApplicationError(400, "INVALID_EVENT_CURSOR", "Event cursor is invalid.")
    return int(value)


async def read_event_batch(request: Request, job_id: UUID, after: int) -> EventBatch:
    """매 조회의 권한과 상태·커서를 확인한 뒤 같은 스냅샷의 이벤트만 읽는다.

    Args:
        request: 최초 요청의 쿠키와 현재 앱 설정.
        job_id: 구독할 작업 UUID.
        after: 마지막 전달 sequence.

    Returns:
        EventBatch: 최대100개의 오름차순 이벤트와 현재 종료 여부.
    """
    principal = await require_evaluator(request)
    settings = request.app.state.settings
    check_actor(settings, request.app.state.evaluation_access, principal.user_id)
    async with connection(settings) as conn:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            await conn.execute("SET LOCAL statement_timeout = '5s'")
            status = await read_evaluation_job(conn, principal.user_id, job_id)
            if after > status.last_event_sequence:
                raise ApplicationError(400, "INVALID_EVENT_CURSOR", "Event cursor is invalid.")
            cursor = await conn.execute(
                "SELECT min(sequence) AS first FROM runtime.job_events WHERE job_id=%s",
                (job_id,),
            )
            bounds = await cursor.fetchone()
            if bounds and bounds["first"] is not None and after < bounds["first"] - 1:
                raise ApplicationError(410, "EVENT_CURSOR_EXPIRED", "Reload the job snapshot.")
            cursor = await conn.execute(
                "SELECT job_id,sequence,type,occurred_at,payload FROM runtime.job_events "
                "WHERE job_id=%s AND sequence>%s ORDER BY sequence LIMIT %s",
                (job_id, after, BATCH_SIZE),
            )
            rows = await cursor.fetchall()
            try:
                events = [JobEvent.model_validate(row) for row in rows]
            except ValidationError:
                raise ApplicationError(
                    503, "EVENT_DATA_UNAVAILABLE", "Event data is unavailable."
                ) from None
    return EventBatch(events, status.status in TERMINAL_STATES, status.last_event_sequence)


async def stream_events(request: Request, job_id: UUID, after: int) -> AsyncIterator[str]:
    """짧은 DB 조회 사이 연결을 반환하고 인증 실패·장애·종료 시 스트림을 닫는다.

    Args:
        request: 최초 세션 쿠키와 연결 종료 감지에 사용할 요청.
        job_id: 접근이 허용된 작업 UUID.
        after: 마지막 수신 또는 전달 sequence.

    Returns:
        AsyncIterator[str]: 허용 필드 JSON 이벤트 또는15초 간격 주석 heartbeat.
    """
    heartbeat_at = monotonic()
    while not await request.is_disconnected():
        try:
            # 헤더 전 검사가 끝난 뒤에도 새 배치마다 세션·소유권을 다시 확인한다.
            batch = await read_event_batch(request, job_id, after)
        except (HTTPException, ApplicationError):
            # 헤더 이후에는 JSON 오류나 합성 완료 이벤트를 보내지 않는다.
            return
        if batch.events:
            yield "".join(
                f"id: {event.sequence}\nevent: {event.type}\n"
                f"data: {event.model_dump_json(exclude_none=True)}\n\n"
                for event in batch.events
            )
            after = batch.events[-1].sequence
            heartbeat_at = monotonic()
        if batch.terminal and after >= batch.last_sequence:
            return
        if after < batch.last_sequence:
            continue
        if monotonic() - heartbeat_at >= HEARTBEAT_SECONDS:
            yield ": heartbeat\n\n"
            heartbeat_at = monotonic()
        await asyncio.sleep(POLL_SECONDS)
