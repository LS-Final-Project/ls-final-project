"""한국어 요약 응답(KAN-131 화면 요청)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Outcome = Literal["VALID", "INVALID_OUTPUT", "PROVIDER_ERROR"]


class SummaryRecord(BaseModel):
    id: UUID
    summary_ko: str = Field(description="AI가 쓴 한국어 요약. 판정이 아니며 원문이 기준이다.")
    created_at: datetime = Field(description="AI 작성 시각.")
    requested_model: str
    model_version: str | None
    prompt_version: str
    input_version: str
    stale: bool = Field(
        description="True면 요약 뒤 문의 원문이 바뀌었거나 프롬프트 판본이 바뀌었다. 다시 만들 수 있다."
    )


class SummaryAttempt(BaseModel):
    id: UUID
    outcome: Outcome
    error_code: str | None
    created_at: datetime


class TicketSummaryView(BaseModel):
    ticket_id: UUID
    summary: SummaryRecord | None = Field(description="가장 최근의 성공한 요약. 없으면 null.")
    latest_attempt: SummaryAttempt | None = Field(
        description="가장 최근 시도(실패 포함). 실패했으면 화면이 다시 만들기를 보여 줄 수 있다."
    )
    provenance: Literal["ai_draft"] = "ai_draft"
