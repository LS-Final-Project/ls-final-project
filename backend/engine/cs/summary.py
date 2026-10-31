"""고객 문의 원문의 한국어 요약(KAN-131 화면 요청). 모델 호출은 하지 않는다.

해외 고객 문의를 한국인 담당자가 빨리 파악하도록 원문을 한국어 몇 문장으로 줄인다. 분류(5칸)와 따로 둔다:
채택된 분류 프롬프트와 평가를 바꾸지 않기 위해서다. 요약은 판정이 아니다. 유형·긴급도·보증·원인은 분류와
규칙표가 정하고, 요약은 고객이 쓴 내용만 옮긴다.

안전 규칙(출력 검사):
- 한글이 있어야 한다(번역이 아니라 원문 그대로 나오는 경우를 거른다).
- 요약에 나온 코드형 토큰(TRF-OIL-1000, E-204처럼 글자와 숫자가 섞인 것)은 원문에 그대로 있어야 하고, 수는 값으로
  원문에 있어야 한다(2026-10-02를 2026년 10월 2일로 바꿔 쓰는 것은 된다). 모델이 시리얼·날짜·수치를 지어내지
  못하게 한다.
- 승인·확정 약속 문구(초안과 같은 금지 문구)를 사실처럼 쓰면 안 된다. "고객은 …승인되었다고 함"처럼 주장을
  옮기는 문장은 된다.
입력은 분류와 같은 마스킹(이메일, 라벨 붙은 시리얼)을 거친다.

프롬프트는 이 모듈에 둔다. prompt_registry의 단계는 평가·채택 게이트와 묶여 있어서다(채택 전 기능).
"""

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, ValidationError

from backend.engine.cs.classification import ClassificationInput, _unique_object, build_input
from backend.engine.cs.drafting import _Strict, find_forbidden_commitment

SUMMARY_INPUT_VERSION = "cs-summary-input-v1"
SUMMARY_PROMPT_VERSION = "v1"
MAX_CHARS = 300
HANGUL = re.compile(r"[가-힣]")
# 숫자가 든 코드(모델명·시리얼·오류 코드처럼 글자와 숫자가 섞인 토큰). `/`는 나누는 기호로 본다.
CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# 수(천 단위 쉼표 포함). 날짜를 2026-10-02 → 2026년 10월 2일처럼 바꿔 써도 각 수가 원문에 있으면 통과한다.
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
# 고객 주장을 옮기는 보고형 표현. 확정 약속 문구가 이 안에 있으면 요약이 아니라 인용이다.
REPORTED = re.compile(
    r"(?:다|라)\s*고\s*(?:함|했|하|주장|들었|말|요청|문의|전함|밝|적)|(?:다|라)는\s"
)
SENTENCE = re.compile(r"[^.!?\n。]+")
INSTRUCTION = (
    "당신은 전력기기 해외 고객 문의를 한국인 C/S 담당자에게 요약해 주는 보조자다. 아래 입력 JSON의 "
    "subject와 body만 사용한다.\n"
    "- 고객이 무엇을 겪었고 무엇을 요청하는지 한국어 1~3문장으로 요약한다. 300자를 넘기지 않는다.\n"
    "- 원문에 없는 사실, 원인 추정, 보증·교체·환불 여부 판단, 약속을 쓰지 않는다. 고객이 주장한 내용은 "
    "'고객은 …라고 함'처럼 주장으로 쓴다.\n"
    "- 숫자, 날짜, 모델명, 시리얼, 오류 코드는 원문 표기 그대로 옮기고 새로 만들지 않는다. 원문에 숫자가 "
    "글자로 쓰였으면(예: three days) 숫자로 바꾸지 말고 뜻을 한국어로 쓴다(예: 사흘).\n"
    "- [EMAIL]·[SERIAL] 같은 가림 표시는 그대로 둔다.\n"
    '- 출력은 JSON 하나: {"summary_ko": "..."}'
)
SummaryErrorCode = Literal[
    "CS_SUMMARY_SCHEMA", "CS_SUMMARY_NOT_KOREAN", "CS_SUMMARY_UNGROUNDED", "CS_SUMMARY_COMMITMENT"
]


class SummaryOutput(_Strict):
    summary_ko: str = Field(min_length=1, max_length=MAX_CHARS, pattern=r"\S")


class SummaryOutputError(ValueError):
    """모델 출력이 요약 규칙을 어겼다. 원문·출력은 담지 않고 코드만 남긴다."""

    def __init__(self, code: SummaryErrorCode) -> None:
        super().__init__(code)
        self.code: SummaryErrorCode = code


def build_summary_input(record: Mapping[str, object]) -> ClassificationInput:
    """분류와 같은 허용 필드·마스킹을 거친 모델 입력.

    Args:
        record: 문의 행(channel·body_language·subject·body).

    Returns:
        ClassificationInput: 마스킹된 입력. 민감값 표가 없어 알려진 이메일·라벨 시리얼만 가린다.
    """
    return build_input(record, sensitive_values={}).payload


def input_sha256(payload: ClassificationInput) -> str:
    """요약 입력 판본. 문의를 고치면 바뀌어 이전 요약이 오래된 것으로 보인다."""
    encoded = json.dumps(
        {"v": SUMMARY_INPUT_VERSION, **payload.model_dump(mode="json")},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def render_prompt(payload: ClassificationInput) -> str:
    return INSTRUCTION + "\n\n입력:\n" + payload.model_dump_json()


def _codes(text: str) -> set[str]:
    """글자와 숫자가 함께 든 토큰(끝의 . , - 는 뗀다)."""
    tokens = (token.rstrip(".,-").casefold() for token in CODE.findall(text))
    return {t for t in tokens if re.search(r"\d", t) and re.search(r"[a-z]", t)}


def _numbers(text: str) -> set[int]:
    """수 값(앞자리 0과 천 단위 쉼표 무시). 코드 안의 숫자도 원문 쪽에서는 센다."""
    return {int(n.replace(",", "")) for n in NUMBER.findall(text)}


def ungrounded(summary: str, source: str) -> bool:
    """요약의 코드·수가 원문에 없으면 True. 코드는 그대로, 수는 값으로 비교한다."""
    codes = _codes(summary)
    if not codes <= _codes(source):
        return True
    rest = CODE.sub(
        lambda m: " " if m.group(0).rstrip(".,-").casefold() in codes else m.group(0), summary
    )
    return not _numbers(rest) <= _numbers(source)


def commits(summary: str) -> bool:
    """확정 약속 문구가 고객 주장 인용이 아닌 문장에 있으면 True(초안과 같은 금지 문구)."""
    return any(
        find_forbidden_commitment(sentence) and not REPORTED.search(sentence)
        for sentence in SENTENCE.findall(summary)
    )


def validate_output(raw: str, *, source: ClassificationInput) -> SummaryOutput:
    """모델 원시 출력을 검사한다.

    Args:
        raw: 모델 응답 텍스트(JSON).
        source: 모델에 보낸 마스킹 입력.

    Returns:
        SummaryOutput: 규칙을 지킨 요약. 어기면 SummaryOutputError(코드만)다.
    """
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
        output = SummaryOutput.model_validate(data)
    except (ValueError, ValidationError, TypeError):
        raise SummaryOutputError("CS_SUMMARY_SCHEMA") from None
    summary = output.summary_ko
    if not HANGUL.search(summary):
        raise SummaryOutputError("CS_SUMMARY_NOT_KOREAN")
    if ungrounded(summary, f"{source.subject or ''} {source.body}"):
        raise SummaryOutputError("CS_SUMMARY_UNGROUNDED")
    if commits(summary):
        raise SummaryOutputError("CS_SUMMARY_COMMITMENT")
    return output
