"""고객 회신 해석 요청·응답(문의 처리 화면의 회신 반영 분석, KAN-230)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.engine.cs.reply import ReplyInterpretation, ReplyUpdate

Outcome = Literal["VALID", "INVALID_OUTPUT", "PROVIDER_ERROR"]


class ReplyAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: UUID | None = Field(
        default=None,
        description="해석할 연결된 회신. 없으면 본문이 있는 가장 최근 회신이다.",
    )


class ReplyAnalysisRecord(BaseModel):
    id: UUID
    message_id: UUID = Field(description="해석한 회신(cs.ticket_customer_replies).")
    interpretation: ReplyInterpretation = Field(
        description="AI 해석: 같은 요청·장비·판본인지와 이유, 답한 요청 코드, 새 사실, 남은 질문."
    )
    update: ReplyUpdate = Field(
        description="해석을 반영하면 초안 입력이 어떻게 바뀌는지(제공·부족 코드, 다음 행동). "
        "제공 정보에 실제로 반영하는 것은 사람이 정정 API로 한다."
    )
    created_at: datetime = Field(description="AI 작성 시각.")
    requested_model: str
    model_version: str | None
    prompt_version: str
    input_version: str
    stale: bool = Field(
        description="True면 해석 뒤 요청한 정보·제공 정보·확정 장비·이전 초안·프롬프트 판본이 "
        "바뀌었다. 다시 만들 수 있다."
    )


class ReplyAnalysisAttempt(BaseModel):
    id: UUID
    message_id: UUID
    outcome: Outcome
    error_code: str | None
    created_at: datetime


class TicketReplyAnalysisView(BaseModel):
    ticket_id: UUID
    message_id: UUID | None = Field(
        description="대상 회신. 요청한 회신, 없으면 본문이 있는 가장 최근 회신. 그런 회신이 없으면 null."
    )
    analysis: ReplyAnalysisRecord | None = Field(
        description="대상 회신의 가장 최근 성공한 해석(지금 입력과 맞는 것 우선). 없으면 null."
    )
    latest_attempt: ReplyAnalysisAttempt | None = Field(
        description="대상 회신의 가장 최근 시도(실패 포함)."
    )
    provenance: Literal["ai_draft"] = "ai_draft"
