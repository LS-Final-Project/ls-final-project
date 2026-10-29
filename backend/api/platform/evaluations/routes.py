"""평가 기능의 접근 경계와 현재 실행 가능 여부를 제공한다."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from backend.api.platform.evaluations.access import require_evaluation_write, require_evaluator
from backend.api.platform.evaluations.admission import (
    accept_run,
    admission_available,
    get_run,
    list_experiments,
    list_runs,
    register_experiment,
)
from backend.api.platform.evaluations.controls import control_run
from backend.api.platform.evaluations.schemas import (
    EvaluationAdmission,
    EvaluationExperiments,
    EvaluationPolicy,
    EvaluationRunRequest,
    EvaluationRuns,
    EvaluationRunStatus,
    ExperimentCreate,
    ExperimentRegistered,
    Stage,
)
from backend.api.platform.evaluations.summary import EvaluationRunSummary, get_run_summary
from backend.shared.access import Principal
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

router = APIRouter(
    prefix="/api/v1/platform/evaluations",
    tags=["evaluations"],
    dependencies=[Depends(require_evaluator)],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 503)},
)


@router.get(
    "/policy",
    response_model=SuccessResponse[EvaluationPolicy],
    operation_id="platform_get_evaluation_policy",
)
async def evaluation_policy(request: Request) -> SuccessResponse[EvaluationPolicy]:
    """지정 평가자에게 확정된 주지표 선택과 미구현 실행 잠금을 반환한다.

    Args:
        request: 평가자 검사를 통과하고 요청 ID가 부여된 요청.

    Returns:
        SuccessResponse[EvaluationPolicy]: 원문·정답·사용자 허용 목록이 없는 정책.
    """
    return SuccessResponse(
        data=EvaluationPolicy(
            local_smoke_execution_available=admission_available(
                request.app.state.settings, request.app.state.evaluation_access
            ),
            admission_available=admission_available(
                request.app.state.settings, request.app.state.evaluation_access
            ),
        ),
        meta=ResponseMeta(request_id=request.state.request_id),
    )


@router.get(
    "/experiments",
    response_model=SuccessResponse[EvaluationExperiments],
    operation_id="platform_list_evaluation_experiments",
)
async def evaluation_experiments(
    request: Request,
    principal: Annotated[Principal, Depends(require_evaluator)],
    stage: Stage | None = None,
) -> SuccessResponse[EvaluationExperiments]:
    """본인 실험 최근 50개와 접수 가능 여부·막힌 사유를 반환한다.

    Args:
        request: 서버 설정·허용 목록·요청 ID를 가진 요청.
        principal: 현재 지정 평가자 세션.
        stage: 주어지면 그 단계(classification·search·draft·rfp)의 실험만.

    Returns:
        SuccessResponse[EvaluationExperiments]: 구성 원문·manifest·hash·대상 ID 없는 목록.
    """
    items = await list_experiments(
        request.app.state.settings,
        request.app.state.evaluation_access,
        principal.user_id,
        stage,
    )
    return SuccessResponse(
        data=EvaluationExperiments(items=items),
        meta=ResponseMeta(request_id=request.state.request_id),
    )


@router.post(
    "/experiments",
    status_code=201,
    response_model=SuccessResponse[ExperimentRegistered],
    operation_id="platform_register_evaluation_experiment",
    dependencies=[Depends(require_evaluation_write)],
    responses={code: {"model": ErrorResponse} for code in (409, 422, 503)},
    description=(
        "실험 정의와 대상 집합을 동결 등록한다. 등록한 실험은 바꾸지 않으며 승인은 운영자 설정이다. "
        "최종 시험(test) 분할은 등록할 수 없다."
    ),
)
async def register_evaluation_experiment(
    request: Request,
    body: ExperimentCreate,
    principal: Annotated[Principal, Depends(require_evaluator)],
) -> SuccessResponse[ExperimentRegistered]:
    """서버가 hash를 계산해 실험과 대상을 한 트랜잭션으로 등록한다.

    Args:
        request: 서버 설정·평가 허용 설정·요청 ID를 가진 요청.
        body: 등록 요청.
        principal: 현재 지정 평가자 세션.

    Returns:
        SuccessResponse[ExperimentRegistered]: 실험 ID·hash·대상 수·접수 가능 여부.
    """
    registered = await register_experiment(
        request.app.state.settings,
        request.app.state.evaluation_access,
        principal.user_id,
        body,
    )
    return SuccessResponse(data=registered, meta=ResponseMeta(request_id=request.state.request_id))


@router.get(
    "/runs",
    response_model=SuccessResponse[EvaluationRuns],
    operation_id="platform_list_evaluation_runs",
)
async def evaluation_runs(
    request: Request, principal: Annotated[Principal, Depends(require_evaluator)]
) -> SuccessResponse[EvaluationRuns]:
    """본인 접수 실행 최근 50개를 반환한다(최종 시험 실행 제외).

    Args:
        request: 서버 설정·요청 ID를 가진 요청.
        principal: 현재 지정 평가자 세션.

    Returns:
        SuccessResponse[EvaluationRuns]: 원시 출력·정답 없는 실행 목록.
    """
    items = await list_runs(request.app.state.settings, principal.user_id)
    return SuccessResponse(
        data=EvaluationRuns(items=items), meta=ResponseMeta(request_id=request.state.request_id)
    )


@router.post(
    "/runs",
    status_code=202,
    response_model=SuccessResponse[EvaluationAdmission],
    operation_id="platform_request_evaluation_run",
    dependencies=[Depends(require_evaluation_write)],
    responses={code: {"model": ErrorResponse} for code in (404, 409)},
    description="명시적으로 켠 local/test 환경의 승인된 합성 smoke만 저장 접수한다. 실행기는 미연결이다.",
)
async def request_evaluation_run(
    request: Request,
    response: Response,
    body: EvaluationRunRequest,
    principal: Annotated[Principal, Depends(require_evaluator)],
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
    if_match: Annotated[str | None, Header()] = None,
) -> SuccessResponse[EvaluationAdmission]:
    """합성 접수의 DB commit 후에만202를 반환하며 동일 요청은 최초 응답을 재생한다.

    Args:
        request: 현재 환경·허용 목록·요청 ID를 가진 요청.
        response: 저장된 상태 URL을 Location으로 반환할 응답.
        body: 서버 실험 UUID만 포함한 요청.
        principal: 현재 세션과 평가자 검사를 통과한 사용자.
        idempotency_key: 공통 계약의 8~128자 요청 식별 키.
        if_match: 멱등 hash에 포함하는 선택 조건 헤더.

    Returns:
        SuccessResponse: 원자적 저장이 완료된 접수 결과. 모델 실행 완료가 아니다.
    """
    result = await accept_run(
        request.app.state.settings,
        request.app.state.evaluation_access,
        principal.user_id,
        body.experiment_id,
        idempotency_key,
        request.state.request_id,
        if_match,
    )
    response.headers["Location"] = result.data.status_url
    response.headers["Retry-After"] = "5"
    return result


@router.get(
    "/runs/{run_id}",
    response_model=SuccessResponse[EvaluationRunStatus],
    operation_id="platform_get_evaluation_run",
    responses={404: {"model": ErrorResponse}},
)
async def evaluation_run_status(
    request: Request,
    run_id: UUID,
    response: Response,
    principal: Annotated[Principal, Depends(require_evaluator)],
) -> SuccessResponse[EvaluationRunStatus]:
    """현재 권한으로 자기 합성 접수의 상태만 조회한다.

    Args:
        request: 서버 설정과 요청 ID.
        run_id: 조회할 실행 UUID.
        response: 현재 판본을 ETag로 전달할 응답.
        principal: 현재 지정 평가자 세션.

    Returns:
        SuccessResponse: 원시 출력과 정답을 제외한 실행 상태.
    """
    data = await get_run(
        request.app.state.settings, request.app.state.evaluation_access, principal.user_id, run_id
    )
    response.headers["ETag"] = f'"{data.version}"'
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))


@router.post(
    "/runs/{run_id}/{action}",
    response_model=SuccessResponse[EvaluationRunStatus],
    operation_id="platform_control_evaluation_run",
    dependencies=[Depends(require_evaluation_write)],
    responses={code: {"model": ErrorResponse} for code in (404, 409, 412, 428)},
)
async def evaluation_run_control(
    request: Request,
    response: Response,
    run_id: UUID,
    action: Literal["cancel", "resume"],
    principal: Annotated[Principal, Depends(require_evaluator)],
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
    if_match: Annotated[str | None, Header()] = None,
) -> SuccessResponse[EvaluationRunStatus]:
    """현재 세션·CSRF·판본·멱등 키를 검증하여 자기 합성 실행을 제어한다.

    Args:
        request: 서버 설정과 현재 요청 ID.
        response: 최초 결과 판본 ETag를 반환할 응답.
        run_id: 실행 UUID.
        action: cancel 또는 resume.
        principal: 현재 지정 평가자 세션.
        idempotency_key: 요청 재전송 식별 키.
        if_match: 조회한 실행 판본.

    Returns:
        SuccessResponse: 실제 저장되거나 재생된 제어 결과.
    """
    result = await control_run(
        request.app.state.settings,
        request.app.state.evaluation_access,
        principal.user_id,
        run_id,
        action,
        idempotency_key,
        if_match,
        request.state.request_id,
    )
    response.headers["ETag"] = f'"{result.data.version}"'
    return result


@router.get(
    "/runs/{run_id}/summary",
    response_model=SuccessResponse[EvaluationRunSummary],
    operation_id="platform_get_evaluation_run_summary",
    responses={code: {"model": ErrorResponse} for code in (404, 409)},
)
def evaluation_run_summary(
    request: Request,
    run_id: UUID,
    principal: Annotated[Principal, Depends(require_evaluator)],
) -> SuccessResponse[EvaluationRunSummary]:
    """현재 세션의 합성 사용량·감사 집계를 스레드 풀에서 조회한다.

    Args:
        request: 서버 환경·승인 목록과 요청 ID.
        run_id: 조회 대상 실행.
        principal: 현재 세션과 평가자 검사를 통과한 사용자.

    Returns:
        SuccessResponse: 정답·예측·원시 내용 없는 합성 집계.
    """
    data = get_run_summary(
        request.app.state.settings, request.app.state.evaluation_access, principal.user_id, run_id
    )
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))
