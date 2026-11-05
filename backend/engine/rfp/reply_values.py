"""고객 회신에서 새 요구값 후보를 읽는다(KAN-224).

규칙이 먼저 읽는다. 질의가 기대하는 단위의 값이 정확히 하나이고 정정·여러 질의 표지가 없을 때만 규칙 결과를 쓴다.
그 밖(값 없음·여러 값·숫자 없는 답·정정·여러 질의)은 AI 공급자가 있으면 AI로, 없으면 none이다. AI 결과도
요구 추출과 같은 근거 검사를 거친다: 근거 문장이 회신 원문에 있고 값이 그 문장 안에 있어야 한다. 어느 경우든
후보일 뿐이며 요구값을 바꾸지 않는다(사람이 "값 반영하고 다시 비교"를 누른다).
"""

import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from backend.shared.ai import Generation

Method = Literal["rule", "ai", "none"]
Reason = Literal[
    "NO_VALUE",
    "MULTIPLE_VALUES",
    "CORRECTION",
    "CONDITIONAL",
    "MULTIPLE_QUESTIONS",
    "AI_UNGROUNDED",
    "AI_FAILED",
]
Provider = Callable[[str], Generation]

NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
# 실효값 표기(28 kVrms)는 같은 값이다.
UNIT_SUFFIX = r"(?:\s*(?:rms|r\.m\.s\.))?(?![A-Za-z])"
DURATION = re.compile(
    rf"({NUMBER})\s*(분간|분|초간|초|시간|minutes?|mins?|seconds?|secs?|hours?|hrs?|min|sec|h|s)"
    r"(?![A-Za-z])",
    re.I,
)
DURATION_UNITS = {"분": "min", "초": "s", "시간": "h", "m": "min", "s": "s", "h": "h"}
# 앞 값을 뒤에서 뒤집는 표현. 규칙은 어느 값이 답인지 모른다.
CORRECTION = re.compile(r"아니라|아닌|정정|잘못|not\s|instead|correct(?:ion|ed)", re.I)
# 값이 조건에 따라 갈리는 답("실내 설치 시 28 kV, 옥외는 별도 협의").
CONDITIONAL = re.compile(
    r"경우|\S\s*시[,\s]|단[,\s]|다만|별도\s*협의|\bif\s|unless|depending", re.I
)
QUESTION_REF = re.compile(r"\b(?:TQ|Q)\s*-?\s*\d+(?:-\d+)?", re.I)
SENTENCE = re.compile(r"(?<=[.!?。])\s+|\n+")
# 괄호 안 부연("정격전압 12 kV 표준값")은 답이 아니라 설명이다. ponytail: 한 단계 괄호만 본다.
ASIDE = re.compile(r"\([^()]*\)|（[^（）]*）")


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: str
    unit: str | None = None


class ReplyValue(BaseModel):
    """회신에서 읽은 새 요구값 후보. method가 none이면 value는 없다."""

    model_config = ConfigDict(extra="forbid")
    method: Method
    value: str | None = None
    unit: str | None = None
    conditions: list[Condition] = []
    quote: str | None = None
    reason: Reason | None = None
    # AI 읽기 작업 상태(KAN-224 worker). 규칙만 쓴 후보는 None이다.
    ai: Literal["QUEUED", "UNAVAILABLE", "DONE", "FAILED"] | None = None


class AiOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str | None
    unit: str | None = None
    quote: str | None = None
    conditions: list[Condition] = []


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _sentence(text: str, position: int) -> str:
    start = 0
    for piece in SENTENCE.split(text):
        found = text.find(piece, start)
        if found <= position < found + len(piece):
            return piece.strip()
        start = found + len(piece)
    return text.strip()


def _outside(text: str, matches: Sequence[re.Match[str]]) -> list[re.Match[str]]:
    asides = [m.span() for m in ASIDE.finditer(text)]
    return [m for m in matches if not any(a <= m.start() < b for a, b in asides)]


def _durations(text: str) -> set[tuple[Decimal, str]]:
    out = set()
    for match in _outside(text, list(DURATION.finditer(text))):
        number = _decimal(match[1])
        word = match[2].casefold()
        unit = DURATION_UNITS.get(word[0] if word[0] in "msh" else word.rstrip("간"), word)
        if number is not None:
            out.add((number, unit))
    return out


def _rule(text: str, unit: str, condition_names: Sequence[str]) -> ReplyValue:
    pattern = re.compile(rf"({NUMBER})\s*{re.escape(unit)}{UNIT_SUFFIX}", re.I)
    matches = _outside(text, list(pattern.finditer(text)))
    values = {d for m in matches if (d := _decimal(m[1])) is not None}
    if not values:
        return ReplyValue(method="none", reason="NO_VALUE")
    if len({m.upper().replace(" ", "") for m in QUESTION_REF.findall(text)}) > 1:
        return ReplyValue(method="none", reason="MULTIPLE_QUESTIONS")
    if CORRECTION.search(text):
        return ReplyValue(method="none", reason="CORRECTION")
    if len(values) > 1:
        return ReplyValue(method="none", reason="MULTIPLE_VALUES")
    if CONDITIONAL.search(ASIDE.sub("", text)):
        return ReplyValue(method="none", reason="CONDITIONAL")
    first = matches[0]
    conditions = []
    if "duration" in condition_names:
        found = _durations(text)
        if len(found) == 1:
            ((number, dunit),) = found
            conditions.append(Condition(name="duration", value=str(number), unit=dunit))
    return ReplyValue(
        method="rule",
        value=first[1].replace(",", ""),
        unit=unit,
        conditions=conditions,
        quote=_sentence(text, first.start()),
    )


def ai_prompt(question_text: str, attribute: str, unit: str | None, reply_text: str) -> str:
    """AI 공급자에 보낼 프롬프트. 회신 원문 밖의 값을 지어내지 말라고 묶는다."""
    return (
        "고객 회신에서 아래 질의에 대한 새 요구값 하나를 찾는다.\n"
        f"질의: {question_text}\n속성: {attribute}\n기대 단위: {unit or '없음'}\n"
        "규칙: 값과 근거 문장은 회신 원문에 그대로 있어야 한다. 답이 없거나 애매하면 value를 null로 둔다. "
        "회신에 여러 질의의 답이 있으면 이 질의의 답만 고른다. 앞 값을 뒤에서 정정했으면 정정한 값을 고른다.\n"
        'JSON만 출력: {"value": 문자열|null, "unit": 문자열|null, "quote": 문자열|null, '
        '"conditions": [{"name": 문자열, "value": 문자열, "unit": 문자열|null}]}\n'
        f"회신 원문:\n{reply_text}"
    )


def _grounded(output: AiOutput, reply_text: str, unit: str | None) -> bool:
    if not output.value or not output.quote:
        return False
    compact = lambda s: _norm(s).replace(" ", "").casefold()  # noqa: E731
    if _norm(output.quote).casefold() not in _norm(reply_text).casefold():
        return False
    if compact(output.value) not in compact(output.quote):
        return False
    return unit is None or (output.unit or "").casefold() == unit.casefold()


def read_reply_value(
    reply_text: str,
    *,
    unit: str | None,
    attribute: str,
    question_text: str,
    condition_names: Sequence[str] = (),
    provider: Provider | None = None,
) -> ReplyValue:
    """회신 원문에서 새 요구값 후보를 읽는다.

    Args:
        reply_text: 회신 원문.
        unit: 요구의 기대 단위. 없으면 규칙은 읽지 않고 AI로 넘긴다.
        attribute: 요구 속성 이름(AI 프롬프트용).
        question_text: 보낸 질의 문장(AI 프롬프트용).
        condition_names: 요구 조건 이름(duration이면 시간도 읽는다).
        provider: AI 공급자. 없으면 규칙이 못 읽은 회신은 none이다.

    Returns:
        ReplyValue: 후보 값과 근거 문장, 읽은 방법.
    """
    text = unicodedata.normalize("NFKC", reply_text)
    ruled = (
        _rule(text, unit, condition_names) if unit else ReplyValue(method="none", reason="NO_VALUE")
    )
    if ruled.method == "rule":
        return ruled
    if provider is None:
        return ruled
    try:
        generation = provider(ai_prompt(question_text, attribute, unit, reply_text))
    except Exception:  # noqa: BLE001 - 회신 기록은 AI 실패와 무관하게 남아야 한다.
        return ReplyValue(method="none", reason="AI_FAILED")
    try:
        output = AiOutput.model_validate(json.loads(generation.text))
    except (json.JSONDecodeError, ValidationError, TypeError):
        return ReplyValue(method="none", reason="AI_UNGROUNDED")
    if not _grounded(output, reply_text, unit):
        return ReplyValue(method="none", reason="AI_UNGROUNDED")
    return ReplyValue(
        method="ai",
        value=output.value,
        unit=output.unit,
        conditions=output.conditions,
        quote=output.quote,
    )
