"""화면이 주지표와 실행 잠금을 구별하기 위한 평가 정책 응답."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 실험이 바꾼 단계. 화면은 이 값으로 실험을 묶어 보여준다.
Stage = Literal["classification", "search", "draft", "rfp"]
KnobValue = str | int | float | bool


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


BlockedReason = Literal[
    "ADMISSION_UNAVAILABLE",
    "FINAL_TEST_LOCKED",
    "EXPERIMENT_NOT_APPROVED",
    "LOCAL_SMOKE_ONLY",
    "EXPERIMENT_HASH_MISMATCH",
]


class EvaluationExperiment(BaseModel):
    """본인이 만든 실험 하나. 구성 원문·manifest·hash·대상 ID는 담지 않는다."""

    experiment_id: UUID
    name: str
    purpose: Literal["smoke", "rule_replay", "pipeline"]
    data_split: Literal["train", "valid", "test"] | None
    data_origin: Literal["synthetic", "demo", "real"]
    model_id: str
    stage: Stage | None
    # 비밀 아닌 조절 항목(예 prompt_version, temperature). config_hash에 포함된다.
    knobs: dict[str, KnobValue] = {}
    target_count: int
    created_at: datetime
    # 접수 규칙(validate_smoke_experiment)을 그대로 적용한 결과. 대상 집합 검사는 접수 때 한다.
    admissible: bool
    blocked_reason: BlockedReason | None


class EvaluationExperiments(BaseModel):
    """본인 실험 최근 50개."""

    items: list[EvaluationExperiment]


class EvaluationRunItem(BaseModel):
    """본인 접수 실행 하나. 원시 출력·정답은 담지 않는다."""

    run_id: UUID
    job_id: UUID
    experiment_id: UUID
    stage: Stage | None
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "TIMED_OUT"]
    requested_count: int
    needs_review: bool
    failure_code: str | None
    created_at: datetime


class EvaluationRuns(BaseModel):
    """본인 접수 실행 최근 50개."""

    items: list[EvaluationRunItem]


DataOrigin = Literal["synthetic", "demo", "real"]


class ManifestSource(BaseModel):
    """manifest의 원천 하나. 파일 원문이 아니라 hash·건수·출처 구분만 담는다."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=200)
    source_instance: str = Field(min_length=1, max_length=200)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    count: int = Field(ge=0)
    data_origin: DataOrigin


class DatasetManifest(BaseModel):
    """{} 또는 {"sources": [...]}. 그 밖의 키는 받지 않는다."""

    model_config = ConfigDict(extra="forbid")

    sources: list[ManifestSource] | None = Field(default=None, min_length=1)


class TargetCreate(BaseModel):
    """동결할 대상 하나. attributes는 보고 분리·제외 플래그이며 원문·정답을 담지 않는다."""

    model_config = ConfigDict(extra="forbid")

    target_ref: str = Field(min_length=1, max_length=128)
    target_kind: str = Field(min_length=1, max_length=64)
    data_origin: DataOrigin
    attributes: dict[str, Any] = {}


class ExperimentCreate(BaseModel):
    """새 실험 등록 요청. 등록한 실험은 바꾸지 않으며 구성을 바꾸려면 새로 등록한다."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    purpose: Literal["smoke", "rule_replay", "pipeline"]
    data_origin: DataOrigin
    # test는 받되 등록 함수가 EVALUATION_TEST_SPLIT_REGISTRATION_BLOCKED로 거부한다.
    data_split: Literal["train", "valid", "test"] | None = None
    gold_version: str | None = Field(default=None, min_length=1, max_length=200)
    policy_version: str | None = Field(default=None, min_length=1, max_length=200)
    prompt_version: str | None = Field(default=None, min_length=1, max_length=200)
    index_version: str | None = Field(default=None, min_length=1, max_length=200)
    input_builder_version: str | None = Field(default=None, min_length=1, max_length=200)
    model_id: str = Field(min_length=1, max_length=200)
    configuration: dict[str, Any] = {}
    stage: Stage | None = None
    knobs: dict[str, KnobValue] = {}
    dataset_manifest: DatasetManifest = DatasetManifest()
    targets: list[TargetCreate] = Field(min_length=1, max_length=5000)


class ExperimentRegistered(BaseModel):
    """등록 결과. hash는 운영자가 승인 목록에 넣을 값이라 등록자에게 돌려준다."""

    experiment_id: UUID
    config_hash: str
    dataset_hash: str
    target_set_hash: str
    target_count: int
    admissible: bool
    blocked_reason: BlockedReason | None
