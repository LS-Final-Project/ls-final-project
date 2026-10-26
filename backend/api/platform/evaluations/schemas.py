"""화면이 주지표와 실행 잠금을 구별하기 위한 평가 정책 응답."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EvaluationPolicy(BaseModel):
    """실행 기반 연결 전에는 작업과 민감 자료 접근을 활성화하지 않는다."""

    model_config = ConfigDict(extra="forbid")

    primary_quality_view: Literal["first_attempt"] = "first_attempt"
    final_result_reported_separately: Literal[True] = True
    admission_available: bool = False
    local_smoke_execution_available: bool = False
    execution_available: Literal[False] = False
    execution_unavailable_reason: Literal["RUNNER_NOT_CONNECTED"] = "RUNNER_NOT_CONNECTED"
    final_test_available: Literal[False] = False
    raw_output_available: Literal[False] = False


class EvaluationRunRequest(BaseModel):
    """서버에 동결 등록된 실험만 참조하며 실행 설정 덮어쓰기를 금지한다."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: UUID


class EvaluationAdmission(BaseModel):
    """합성 접수의 저장 결과이며 실행 완료나 품질 점수가 아니다."""

    run_id: UUID
    job_id: UUID
    status: Literal["QUEUED"] = "QUEUED"
    status_url: str


class EvaluationRunStatus(BaseModel):
    """원시 출력·정답 없이 접수 상태만 제공한다."""

    run_id: UUID
    job_id: UUID
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "TIMED_OUT"]
    requested_count: int
    version: int
    needs_review: bool
    failure_code: str | None
