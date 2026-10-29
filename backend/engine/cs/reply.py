"""추가 정보 요청 뒤 고객 회신의 입력 정제·원시 출력 검사·초안 입력 갱신(KAN-109). 모델 호출은 하지 않는다.

회신 본문은 분류와 같은 build_input으로 마스킹한다. 같은 장비인지는 모델이 아니라 여기서 시리얼 원값을
정확히 대조해(대소문자 무시, 앞뒤 공백 제거) reply_serial_match로만 넘긴다. 대조 대상은 sensitive_values의
SERIAL 값과 라벨이 붙은 시리얼뿐이며 임의 토큰을 시리얼로 추측하지 않는다. 확정 장비 시리얼 원값은 정제
입력·예외 메시지 어디에도 넣지 않는다.

이 모듈은 상태 전이(KAN-110)를 하지 않고, 과거 문의·해결 사례·장비 이력을 바꾸거나 새로 만들지 않는다.
다른 요청이나 다른 장비로 보이면 사람에게 넘기며 새 문의·장비 연결을 자동으로 만들지 않는다.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from backend.engine.cs.classification import (
    _SERIAL,
    _SERIAL_ABSENCE,
    _unique_object,
    build_input,
)
from backend.engine.cs.drafting import DraftOutput, InfoRequest, _Strict, find_forbidden_commitment

REPLY_INPUT_VERSION = "cs-reply-input-v1"
RequestMatch = Literal["APPLIES", "DIFFERENT_REQUEST", "UNCLEAR"]
# NOT_MENTIONED는 회신이 장비·판본을 말하지 않은 경우, UNCLEAR는 말했지만 모호한 경우다.
Sameness = Literal["SAME", "DIFFERENT", "NOT_MENTIONED", "UNCLEAR"]
SerialMatch = Literal["NOT_MENTIONED", "MATCHES", "DIFFERS", "NO_CONFIRMED_UNIT"]
NextAction = Literal["ESCALATE_TO_AGENT", "CONFIRM_WITH_CUSTOMER", "DRAFT_ANSWER", "REQUEST_AGAIN"]
MASKS = {"[PERSONAL_PROVIDED]", "[SERIAL_PROVIDED]"}
EDGE = "A-Za-z0-9-"


def _unique_codes(codes: list[str]) -> bool:
    return all(codes) and len(set(codes)) == len(codes)


class ReplyContext(_Strict):
    """회신을 해석할 문의 맥락. 확정 장비 시리얼 원값은 여기 두지 않고 build_reply_input 인자로 받는다."""

    ticket_product_code: str
    fw_version_reported: str | None
    requested_info_codes: list[str]
    already_provided_codes: list[str]
    prior_options: DraftOutput | None

    @model_validator(mode="after")
    def validate_codes(self) -> Self:
        """정보 코드가 비지 않고 중복되지 않는지 검사한다.

        Args:
            self: 타입 검사를 통과한 맥락.

        Returns:
            Self: 유효한 맥락.
        """
        if not _unique_codes(self.requested_info_codes) or not _unique_codes(
            self.already_provided_codes
        ):
            raise ValueError("CS_DUPLICATE_INFO")
        return self


class CustomerReply(_Strict):
    """고객 회신 원문. received_at은 감사용이며 모델 입력에 넣지 않는다."""

    channel: Literal["EMAIL", "PARTNER_PORTAL", "WEB_FORM", "PHONE", "CHAT"]
    body: str
    body_language: str
    received_at: datetime


class ReplyInterpretationInput(_Strict):
    """모델에 전달할 회신 해석 입력. 정답·사후 기록·시리얼 원값은 들어가지 않는다."""

    ticket_product_code: str
    fw_version_reported: str | None
    requested_info_codes: list[str]
    already_provided_codes: list[str]
    prior_options: DraftOutput | None
    reply_serial_match: SerialMatch
    body_language: str
    body: str


@dataclass(frozen=True)
class PreparedReplyInput:
    """모델용 회신 입력과 처리 판본·PHONE 주석 제거 건수."""

    payload: ReplyInterpretationInput
    version: str
    removed_annotations: int


def _serial_match(
    body: str,
    confirmed_unit_serial: str | None,
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]],
) -> SerialMatch:
    """회신 원문의 시리얼을 확정 장비 시리얼과 정확히 대조한다.

    Args:
        body: 마스킹 전 회신 본문.
        confirmed_unit_serial: 확정 장비 시리얼 원값. 없으면 None.
        sensitive_values: 어댑터가 식별한 원값과 마스킹 종류.

    Returns:
        SerialMatch: 시리얼이 없으면 NOT_MENTIONED, 확정 장비가 없으면 NO_CONFIRMED_UNIT, 모두 같으면
            MATCHES, 하나라도 다르면 DIFFERS. 부분 일치·유사도는 쓰지 않는다.
    """
    # 어댑터 SERIAL 값도 토큰 경계로만 찾아, 더 긴 다른 시리얼 안의 부분 문자열을 일치로 보지 않는다.
    found = {
        value
        for value, kind in sensitive_values.items()
        if kind == "SERIAL"
        and re.search(rf"(?<![{EDGE}]){re.escape(value)}(?![{EDGE}])", body, re.IGNORECASE)
    }
    for match in _SERIAL.finditer(body):
        value = match["value"].rstrip(".!?:)")
        if value not in MASKS and not _SERIAL_ABSENCE.match(body, match.start("value")):
            found.add(value)
    if not found:
        return "NOT_MENTIONED"
    if confirmed_unit_serial is None:
        return "NO_CONFIRMED_UNIT"
    confirmed = confirmed_unit_serial.strip().casefold()
    return "MATCHES" if {v.strip().casefold() for v in found} == {confirmed} else "DIFFERS"


def build_reply_input(
    context: ReplyContext,
    reply: CustomerReply,
    *,
    confirmed_unit_serial: str | None,
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]],
) -> PreparedReplyInput:
    """회신을 분류와 같은 규칙으로 정제하고 시리얼 대조 결과를 붙인다.

    Args:
        context: 회신을 해석할 문의 맥락.
        reply: 고객 회신 원문.
        confirmed_unit_serial: 확정 장비 시리얼 원값(어댑터 제공). 대조에만 쓰고 정제 입력에 넣지 않으며,
            회신 본문에서 이 값을 포함한 토큰 전체를 대소문자와 무관하게 가린다.
        sensitive_values: 어댑터가 식별한 원값과 마스킹 종류.

    Returns:
        PreparedReplyInput: 정제 입력. 잘못된 입력은 원값을 담지 않는 CS_INVALID_INPUT ValueError다.
    """
    if (
        not isinstance(context, ReplyContext)
        or not isinstance(reply, CustomerReply)
        or not (
            confirmed_unit_serial is None
            or (isinstance(confirmed_unit_serial, str) and confirmed_unit_serial.strip())
        )
    ):
        raise ValueError("CS_INVALID_INPUT")
    body = reply.body
    if confirmed_unit_serial is not None:
        # 경계를 두면 더 긴 시리얼에 든 원값이 그대로 남으므로, 원값을 포함한 토큰 전체를 가린다.
        body = re.sub(
            rf"[{EDGE}]*{re.escape(confirmed_unit_serial.strip())}[{EDGE}]*",
            "[SERIAL_PROVIDED]",
            body,
            flags=re.IGNORECASE,
        )
    prepared = build_input(
        {
            "channel": reply.channel,
            "body_language": reply.body_language,
            "subject": None,
            "body": body,
        },
        sensitive_values=sensitive_values,
    )
    payload = ReplyInterpretationInput(
        ticket_product_code=context.ticket_product_code,
        fw_version_reported=context.fw_version_reported,
        requested_info_codes=list(context.requested_info_codes),
        already_provided_codes=list(context.already_provided_codes),
        prior_options=context.prior_options,
        reply_serial_match=_serial_match(reply.body, confirmed_unit_serial, sensitive_values),
        body_language=prepared.payload.body_language,
        body=prepared.payload.body,
    )
    return PreparedReplyInput(payload, REPLY_INPUT_VERSION, prepared.removed_annotations)


class Applicability(_Strict):
    """같은 요청·장비·판본에 대한 회신인지와 각 판정 이유."""

    same_request: RequestMatch
    same_request_reason: str = Field(pattern=r"\S")
    same_unit: Sameness
    same_unit_reason: str = Field(pattern=r"\S")
    same_version: Sameness
    same_version_reason: str = Field(pattern=r"\S")


class NewFact(_Strict):
    text: str = Field(pattern=r"\S")
    kind: Literal["STATED", "UNCONFIRMED"]


class ReplyInterpretation(_Strict):
    """회신 해석 원시 출력. 코드가 요청 범위 안인지는 validate_output이 본다."""

    applicability: Applicability
    answered_codes: list[str]
    new_facts: list[NewFact]
    remaining_questions: list[InfoRequest]

    @model_validator(mode="after")
    def validate_codes(self) -> Self:
        """답한 코드·남은 질문 코드의 중복과 겹침을 거부한다.

        Args:
            self: 타입 검사를 통과한 출력.

        Returns:
            Self: 유효한 출력.
        """
        remaining = [question.info_code for question in self.remaining_questions]
        if (
            not _unique_codes(self.answered_codes)
            or not _unique_codes(remaining)
            or set(remaining) & set(self.answered_codes)
        ):
            raise ValueError("CS_DUPLICATE_INFO")
        return self


REPLY_PROMPT = (
    "당신은 전력기기 C/S 담당자가 고객 회신을 해석하도록 돕는 보조자다. 아래 입력 JSON만 사용한다.\n"
    "- same_request: 회신이 앞서 요청한 정보(requested_info_codes, prior_options)에 대한 답이면 "
    "APPLIES, 다른 문의면 DIFFERENT_REQUEST, 판단할 수 없으면 UNCLEAR다.\n"
    "- same_unit: reply_serial_match가 DIFFERS면 same_unit은 DIFFERENT다. MATCHES면 DIFFERENT를 쓰지 "
    "않는다. NO_CONFIRMED_UNIT면 SAME을 쓰지 않는다. "
    "same_version: fw_version_reported와 회신의 펌웨어 판본을 비교한다.\n"
    "- 회신이 장비·판본을 말하지 않으면 NOT_MENTIONED, 말했지만 모호하면 UNCLEAR다. "
    "추측으로 SAME을 판정하지 않는다. 판정마다 reason을 쓴다.\n"
    "- answered_codes에는 requested_info_codes 중 회신이 실제로 답한 코드만 쓴다. "
    "remaining_questions에는 requested_info_codes 중 답하지 않은 코드만 쓴다.\n"
    "- new_facts는 고객이 새로 말한 사실이다. 고객이 단정하면 STATED, 고객도 확실하지 않으면 "
    "UNCONFIRMED다.\n"
    "- RMA·반품 승인, 현장 방문 확정, 보증 승인·무상 처리 확정을 약속하지 않는다.\n"
    "- 마스킹 표식([SERIAL_PROVIDED] 등)은 원값을 추측하지 않는다.\n"
    "- 출력은 다음 JSON 스키마를 따르는 JSON 하나다. 다른 키를 더하지 않는다.\n"
    + json.dumps(ReplyInterpretation.model_json_schema(), ensure_ascii=False, sort_keys=True)
)


def render_prompt(payload: ReplyInterpretationInput) -> str:
    """고정 지시문 뒤에 정제 입력 JSON을 붙인다.

    Args:
        payload: build_reply_input의 정제 입력.

    Returns:
        str: 공급자에 그대로 보낼 프롬프트.
    """
    return REPLY_PROMPT + "\n\n입력:\n" + payload.model_dump_json()


def validate_output(
    raw: str, *, requested_info_codes: frozenset[str], serial_match: SerialMatch
) -> ReplyInterpretation:
    """원시 JSON을 폴백 없이 검사하고 금지 약속을 거부한다.

    Args:
        raw: 모델 원시 응답.
        requested_info_codes: 입력에 준 요청 정보 코드 집합.
        serial_match: 입력에 준 reply_serial_match.

    Returns:
        ReplyInterpretation: 유효한 해석. answered는 requested 안, 남은 질문은 requested − answered 안이어야
            한다. 코드가 계산한 시리얼 대조가 모델 판정보다 우선해 DIFFERS면 same_unit은 DIFFERENT여야
            하고, 확정 장비가 없는(NO_CONFIRMED_UNIT) 회신은 SAME일 수 없다. 형식·코드·시리얼 대조 모순은 CS_INVALID_OUTPUT, 형식은 맞지만 승인·확정 약속이 있으면
            CS_FORBIDDEN_COMMITMENT ValueError다.
    """
    try:
        output = ReplyInterpretation.model_validate(
            json.loads(raw, object_pairs_hook=_unique_object)
        )
        answered = set(output.answered_codes)
        same_unit = output.applicability.same_unit
        if (
            not answered <= requested_info_codes
            or any(q.info_code not in requested_info_codes for q in output.remaining_questions)
            or (serial_match == "DIFFERS" and same_unit != "DIFFERENT")
            or (serial_match == "MATCHES" and same_unit == "DIFFERENT")
            or (serial_match == "NO_CONFIRMED_UNIT" and same_unit == "SAME")
        ):
            raise ValueError("CS_UNKNOWN_REFERENCE")
    except (ValueError, RecursionError):
        raise ValueError("CS_INVALID_OUTPUT") from None
    applicability = output.applicability
    texts = [
        applicability.same_request_reason,
        applicability.same_unit_reason,
        applicability.same_version_reason,
        *(fact.text for fact in output.new_facts),
        *(question.question for question in output.remaining_questions),
    ]
    if any(find_forbidden_commitment(text) for text in texts):
        raise ValueError("CS_FORBIDDEN_COMMITMENT")
    return output


class ReplyUpdate(_Strict):
    """초안 입력 갱신값. 이력·장비 연결·상태 필드는 두지 않으며 호출자가 이력 변경에 쓸 값도 내지 않는다."""

    provided_codes: list[str]
    still_missing: list[str]
    next_action: NextAction
    applicability: Applicability
    new_facts: list[NewFact]
    remaining_questions: list[InfoRequest]


def apply_reply(context: ReplyContext, interpretation: ReplyInterpretation) -> ReplyUpdate:
    """해석 결과로 제공 코드·부족 코드·다음 행동을 계산한다(순수 함수).

    Args:
        context: build_reply_input에 준 문의 맥락.
        interpretation: validate_output을 통과한 해석.

    Returns:
        ReplyUpdate: provided = already_provided ∪ answered(정렬), still_missing = requested − provided.
            다른 장비·요청·불명확 회신의 답은 제공 코드로 합치지 않는다(answered는 해석에만 남는다).
            다른 요청·장비·판본이면 ESCALATE_TO_AGENT, UNCLEAR가 있으면 CONFIRM_WITH_CUSTOMER, 부족 코드가
            있으면 REQUEST_AGAIN, 없으면 DRAFT_ANSWER다. NOT_MENTIONED는 막지 않고 applicability에 남긴다.
            맥락의 요청 코드 밖 답은 CS_INVALID_INPUT ValueError다.
    """
    if not set(interpretation.answered_codes) <= set(context.requested_info_codes):
        raise ValueError("CS_INVALID_INPUT")
    a = interpretation.applicability
    action: NextAction | None = None
    if a.same_request == "DIFFERENT_REQUEST" or "DIFFERENT" in (a.same_unit, a.same_version):
        action = "ESCALATE_TO_AGENT"
    elif "UNCLEAR" in (a.same_request, a.same_unit, a.same_version):
        action = "CONFIRM_WITH_CUSTOMER"
    provided = set(context.already_provided_codes)
    if action is None:
        provided |= set(interpretation.answered_codes)
    still_missing = sorted(set(context.requested_info_codes) - provided)
    if action is None:
        action = "REQUEST_AGAIN" if still_missing else "DRAFT_ANSWER"
    return ReplyUpdate(
        provided_codes=sorted(provided),
        still_missing=still_missing,
        next_action=action,
        applicability=a,
        new_facts=list(interpretation.new_facts),
        remaining_questions=list(interpretation.remaining_questions),
    )
