"""플랫폼 소유 평가 작업의 인증된 상태 조회 경로."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse

from backend.api.platform.evaluations.access import require_evaluator
from backend.api.platform.jobs.events import event_cursor, read_event_batch, stream_events
from backend.api.platform.jobs.schemas import EvaluationJobStatus
from backend.api.platform.jobs.status import get_evaluation_job
from backend.shared.access import Principal
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

router = APIRouter(prefix="/api/v1/platform/jobs", tags=["jobs"])


@router.get(
    "/{job_id}",
    response_model=SuccessResponse[EvaluationJobStatus],
    operation_id="platform_get_evaluation_job",
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 503)},
    description="명시적으로 허용된 local/test 합성 평가 작업의 제한 상태를 조회한다.",
)
async def evaluation_job_status(
    request: Request,
    response: Response,
    job_id: UUID,
    principal: Annotated[Principal, Depends(require_evaluator)],
) -> SuccessResponse[EvaluationJobStatus]:
    """현재 세션·평가자·소유권으로 작업을 조회하고 캐시 저장을 금지한다.

    Args:
        request: 현재 서버 설정과 요청 ID를 가진 요청.
        response: 판본과 캐시 방지 헤더를 기록할 응답.
        job_id: 조회할 플랫폼 작업 UUID.
        principal: 현재 세션에서 검증한 지정 평가자.

    Returns:
        SuccessResponse: 상태와 같은 DB 스냅샷의 마지막 이벤트 sequence.
    """
    data = await get_evaluation_job(
        request.app.state.settings, request.app.state.evaluation_access, principal.user_id, job_id
    )
    response.headers["ETag"] = f'"{data.version}"'
    response.headers["Cache-Control"] = "no-store"
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))


@router.get(
    "/{job_id}/events",
    response_class=StreamingResponse,
    operation_id="platform_stream_evaluation_job_events",
    responses={
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
        204: {"description": "종료 위치까지 수신했으므로 재연결을 중단한다."},
        **{code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 410, 503)},
    },
)
async def evaluation_job_events(
    request: Request,
    job_id: UUID,
    last_event_id: Annotated[str | None, Header()] = None,
    after: str | None = None,
) -> Response:
    """스트림 헤더 전에 현재 권한·커서를 검사하고 저장 이벤트를 재생한다.

    Args:
        request: 현재 설정·세션 쿠키를 가진 요청.
        job_id: 플랫폼 평가 작업 UUID.
        last_event_id: 쿼리보다 우선하는 마지막 수신 sequence.
        after: 헤더가 없는 경우의 마지막 수신 sequence.

    Returns:
        Response: SSE 응답 또는 종료 커서의204. 검증 실패는 공통 오류 응답.
    """
    # 잘못된 커서의 상세를 응답하기 전에도 세션·평가자 검사를 적용한다.
    await require_evaluator(request)
    position = event_cursor(last_event_id, after)
    batch = await read_event_batch(request, job_id, position)
    headers = {"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"}
    if batch.terminal and position == batch.last_sequence:
        return Response(status_code=204, headers=headers)
    return StreamingResponse(
        stream_events(request, job_id, position), media_type="text/event-stream", headers=headers
    )
