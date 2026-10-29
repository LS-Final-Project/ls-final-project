"""플랫폼 작업(평가·C/S)의 제한된 조회 스냅샷 계약."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.shared.models import SuccessResponse


class EvaluationJobStatus(BaseModel):
    """실행 상태와 같은 스냅샷의 이벤트 위치만 공개한다."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    run_id: UUID
    owner: Literal["platform"] = "platform"
    kind: Literal["evaluation.run"] = "evaluation.run"
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]
    run_status: Literal[
        "QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "TIMED_OUT"
    ]
    version: int = Field(ge=1)
    last_event_sequence: int = Field(ge=0)
    needs_review: bool


class CsJobStatus(BaseModel):
    """C/S 처리 작업 상태와 같은 스냅샷의 이벤트 위치만 공개한다(KAN-194). 원문·오류 코드는 없다."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    ticket_id: UUID
    owner: Literal["cs"] = "cs"
    kind: Literal["cs.analyze"] = "cs.analyze"
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]
    version: int = Field(ge=1)
    last_event_sequence: int = Field(ge=0)


class TftJobStatus(BaseModel):
    """RFP 처리 작업 상태와 같은 스냅샷의 이벤트 위치만 공개한다(KAN-199). 원문·오류 코드는 없다."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    project_id: UUID
    owner: Literal["tft"] = "tft"
    kind: Literal["rfp.process", "rfp.export", "rfp.annotate"]
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]
    version: int = Field(ge=1)
    last_event_sequence: int = Field(ge=0)


class JobStatusResponse(
    SuccessResponse[
        Annotated[EvaluationJobStatus | CsJobStatus | TftJobStatus, Field(discriminator="owner")]
    ]
):
    """상태 조회 응답. data는 owner(platform·cs·tft)로 구분한다."""
