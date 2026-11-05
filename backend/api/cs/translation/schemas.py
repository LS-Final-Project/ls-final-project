"""한국어 번역 응답(문의 처리 화면의 원문/한국어 전환)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Outcome = Literal["VALID", "INVALID_OUTPUT", "PROVIDER_ERROR"]


class TranslationRecord(BaseModel):
    id: UUID
    subject_ko: str = Field(
        description="AI가 옮긴 한국어 제목. 원문에 제목이 없으면 빈 문자열. 원문이 기준이다."
    )
    body_ko: str = Field(description="AI가 옮긴 한국어 본문 전체. 판정이 아니며 원문이 기준이다.")
    created_at: datetime = Field(description="AI 작성 시각.")
    requested_model: str
    model_version: str | None
    prompt_version: str
    input_version: str
    stale: bool = Field(
        description="True면 번역 뒤 문의 원문이 바뀌었거나 프롬프트 판본이 바뀌었다. 다시 만들 수 있다."
    )


class TranslationAttempt(BaseModel):
    id: UUID
    outcome: Outcome
    error_code: str | None
    created_at: datetime


class TicketTranslationView(BaseModel):
    ticket_id: UUID
    translation: TranslationRecord | None = Field(
        description="가장 최근의 성공한 번역. 없으면 null."
    )
    latest_attempt: TranslationAttempt | None = Field(
        description="가장 최근 시도(실패 포함). 실패했으면 화면이 다시 만들기를 보여 줄 수 있다."
    )
    provenance: Literal["ai_draft"] = "ai_draft"
