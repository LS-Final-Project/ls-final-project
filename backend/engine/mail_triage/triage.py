"""메일 업무 유형 분류의 출력 검증과 호출 경계. 제공자 생성·재시도·DB 저장은 하지 않는다."""

import json
from collections.abc import Callable
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.engine.mail_triage.extract import INPUT_VERSION, TriageInput

OUTPUT_VERSION = "mail-triage-output-v1"
# 메일이 가는 업무: TFT 신규 RFP·기존 프로젝트 변경·후보 회신, C/S 문의, 그 밖.
MailType = Literal["NEW_RFP", "EXISTING_CHANGE", "CANDIDATE_REPLY", "CS", "OTHER"]
# 연결 추천(후보일 뿐 확정 아님). 유형마다 허용하는 추천이 정해져 있다.
LinkSuggestion = Literal["NEW_PROJECT", "EXISTING_THREAD", "NEW_CS_TICKET"]
ALLOWED_LINKS: dict[str, frozenset[str]] = {
    "NEW_RFP": frozenset({"NEW_PROJECT"}),
    "EXISTING_CHANGE": frozenset({"EXISTING_THREAD"}),
    "CANDIDATE_REPLY": frozenset({"EXISTING_THREAD"}),
    "CS": frozenset({"NEW_CS_TICKET", "EXISTING_THREAD"}),
    "OTHER": frozenset(),
}


class Evidence(BaseModel):
    """텍스트 없는 근거 위치. 제목·본문은 마스킹 입력의 글자 구간, 헤더는 회신 여부다."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source: Literal["subject", "body", "reply_header"]
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def span_rules(self) -> Self:
        """제목·본문은 start<end 구간이 있고 헤더는 구간이 없는지 확인한다.

        Returns:
            Self: 검사를 통과한 근거.
        """
        if self.source == "reply_header":
            if self.start is not None or self.end is not None:
                raise ValueError("a header has no span")
        elif self.start is None or self.end is None or self.start >= self.end:
            raise ValueError("text evidence needs start < end")
        return self


class TriageOutput(BaseModel):
    """모델 원시 출력. 정확히 네 필드이며 null 허용 필드도 빠질 수 없다."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    mail_type: MailType
    needs_review: bool
    evidence: list[Evidence] = Field(max_length=10)
    link_suggestion: LinkSuggestion | None


class TriageResult(BaseModel):
    """저장할 분류 결과. 첨부 대기와 최종 검토 필요는 엔진이 정한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mail_type: MailType
    needs_review: bool
    attachments_pending: bool
    evidence: tuple[Evidence, ...]
    link_suggestion: LinkSuggestion | None
    input_version: str = INPUT_VERSION
    output_version: str = OUTPUT_VERSION


def validate_output(raw: str, triage_input: TriageInput) -> TriageOutput:
    """원시 JSON을 엄격히 검사하고 근거 구간·연결 추천이 입력과 유형에 맞는지 확인한다.

    Args:
        raw: 제공자가 돌려준 JSON 문자열.
        triage_input: 같은 호출의 입력(근거 구간 길이 확인용).

    Returns:
        TriageOutput: 검증한 출력. 어기면 ValueError("MAIL_TRIAGE_INVALID_OUTPUT").
    """
    try:
        output = TriageOutput.model_validate(json.loads(raw))
    except (ValueError, ValidationError, TypeError):
        raise ValueError("MAIL_TRIAGE_INVALID_OUTPUT") from None
    lengths = {"subject": len(triage_input.subject), "body": len(triage_input.body)}
    for item in output.evidence:
        if item.source == "reply_header" and not triage_input.is_reply:
            raise ValueError("MAIL_TRIAGE_INVALID_OUTPUT")
        if item.end is not None and item.end > lengths.get(item.source, 0):
            raise ValueError("MAIL_TRIAGE_INVALID_OUTPUT")
    if output.link_suggestion and output.link_suggestion not in ALLOWED_LINKS[output.mail_type]:
        raise ValueError("MAIL_TRIAGE_INVALID_OUTPUT")
    if output.link_suggestion == "EXISTING_THREAD" and not triage_input.is_reply:
        raise ValueError("MAIL_TRIAGE_INVALID_OUTPUT")
    return output


def triage(triage_input: TriageInput, provider: Callable[[TriageInput], str]) -> TriageResult:
    """제공자를 한 번 불러 출력을 검증하고 저장할 결과를 만든다.

    첨부(링크형 제외)가 있으면 내용을 아직 처리하지 않았으므로 첨부 처리 대기이며, 그때나
    근거가 없을 때·기타 유형이면 사람 검토가 필요하다. 모델이 검토 불필요라고 해도 덮어쓴다.

    Args:
        triage_input: 마스킹한 분류 입력.
        provider: 입력을 받아 JSON 문자열을 돌려주는 주입 제공자(검사에서는 합성).

    Returns:
        TriageResult: 유형·검토 필요·첨부 대기·근거·연결 추천과 입출력 판본.
    """
    output = validate_output(provider(triage_input), triage_input)
    pending = any(a.kind != "reference" and not a.is_inline for a in triage_input.attachments)
    return TriageResult(
        mail_type=output.mail_type,
        needs_review=output.needs_review
        or pending
        or not output.evidence
        or output.mail_type == "OTHER",
        attachments_pending=pending,
        evidence=tuple(output.evidence),
        link_suggestion=output.link_suggestion,
    )
