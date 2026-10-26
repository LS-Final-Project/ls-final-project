"""평가 작업의 제한된 조회 스냅샷 계약."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
