"""고객 문의 원문의 한국어 전체 번역(문의 처리 화면의 원문/한국어 전환). 모델 호출은 하지 않는다.

요약(summary.py)과 같은 구조다. 요약은 몇 항목으로 줄이고, 번역은 제목·본문을 빠짐없이 옮긴다. 번역은 판정이
아니다. 유형·긴급도·보증·원인은 분류와 규칙표가 정하고, 번역은 고객이 쓴 내용만 옮긴다. 원문이 기준이다.

안전 규칙(출력 검사, 요약과 같은 검사를 제목+본문 번역 전체에 건다):
- 본문 번역에 한글이 있어야 한다.
- 코드형 토큰(모델명·시리얼·오류 코드·펌웨어 판본)은 원문에 그대로 있어야 하고, 수는 값으로 원문에 있어야 한다.
  점으로 이은 수(펌웨어 판본·소수)는 원문에 그대로 있어야 한다. 가림 표시([PERSONAL_PROVIDED]·
  [SERIAL_PROVIDED])도 원문에 있는 것만 쓸 수 있다.
- 승인·확정 약속 문구를 사실처럼 쓰면 안 된다. 고객이 들었다·주장한다는 문장을 옮기는 것은 된다.
- 길이: 제목 번역 500자, 본문 번역은 마스킹한 원문 본문의 3배(최소 200자)와 8000자 중 작은 값까지.
- 원문에 제목이 없으면 제목 번역은 빈 문자열이어야 하고, 있으면 비어 있으면 안 된다.
원문이 이미 한국어(`body_language`가 ko)면 번역하지 않는다(API가 모델을 부르지 않고 422).

프롬프트는 이 모듈에 둔다. 요약처럼 채택 전 기능이라 prompt_registry의 평가·채택 게이트에 묶지 않는다.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, ValidationError

from backend.engine.cs.classification import ClassificationInput, _unique_object, build_input
from backend.engine.cs.drafting import _Strict
from backend.engine.cs.summary import HANGUL, commits, ungrounded

TRANSLATION_INPUT_VERSION = "cs-translation-input-v1"
TRANSLATION_PROMPT_VERSION = "v1"
MAX_SUBJECT_CHARS = 500
MAX_BODY_CHARS = 8000
# 본문 번역은 원문 본문 길이의 이 배수까지(짧은 원문은 최소 MIN_BODY_LIMIT자).
BODY_RATIO = 3
MIN_BODY_LIMIT = 200
# 분류 입력 마스킹이 넣는 가림 표시.
PLACEHOLDER = re.compile(r"\[[A-Z]+_PROVIDED\]")
# 점으로 이은 수(펌웨어 판본 4.2.1, 소수 35.5). 요약 검사는 수를 하나씩 값으로 보므로 4.2.2도 통과해 따로 본다.
DOTTED = re.compile(r"\d+(?:\.\d+)+")
INSTRUCTION = (
    "당신은 전력기기 해외 고객 문의를 한국인 C/S 담당자에게 한국어로 옮겨 주는 번역자다. 아래 입력 JSON의 "
    "subject와 body만 사용한다.\n"
    "- 요약하지 말고 제목과 본문 전체를 빠짐없이 자연스러운 한국어로 옮긴다. 문단·줄바꿈(\\n)은 원문 그대로 "
    "둔다.\n"
    "- 원문에 없는 사실을 더하거나 빼거나 해석하지 않는다. 원인 추정, 보증·교체·환불 여부 판단, 약속을 "
    "더하지 않는다. 고객이 들었다거나 주장한 내용은 '…라고 했습니다'처럼 그대로 옮긴다.\n"
    "- 숫자, 단위, 날짜, 모델명, 시리얼, 오류 코드, 펌웨어 판본은 원문 표기 그대로 옮기고 새로 만들거나 "
    "바꾸지 않는다(예: 2026-10-02, 4.2.1, E-204, 35 kV는 그대로). 원문에 숫자가 글자로 쓰였으면(예: three "
    "days) 숫자로 바꾸지 말고 한국어 낱말로 쓴다(예: 사흘).\n"
    "- [PERSONAL_PROVIDED]·[SERIAL_PROVIDED] 같은 가림 표시는 그대로 둔다.\n"
    "- subject가 null이거나 비어 있으면 subject_ko는 빈 문자열로 둔다.\n"
    '- 출력은 JSON 하나: {"subject_ko": "...", "body_ko": "..."}'
)
TranslationErrorCode = Literal[
    "CS_TRANSLATION_SCHEMA",
    "CS_TRANSLATION_NOT_KOREAN",
    "CS_TRANSLATION_UNGROUNDED",
    "CS_TRANSLATION_COMMITMENT",
]


class TranslationOutput(_Strict):
    subject_ko: str = Field(max_length=MAX_SUBJECT_CHARS)
    body_ko: str = Field(min_length=1, max_length=MAX_BODY_CHARS, pattern=r"\S")


class TranslationOutputError(ValueError):
    """모델 출력이 번역 규칙을 어겼다. 원문·출력은 담지 않고 코드만 남긴다."""

    def __init__(self, code: TranslationErrorCode) -> None:
        super().__init__(code)
        self.code: TranslationErrorCode = code


def build_translation_input(record: Mapping[str, object]) -> ClassificationInput:
    """분류·요약과 같은 허용 필드·마스킹을 거친 모델 입력.

    Args:
        record: 문의 행(channel·body_language·subject·body).

    Returns:
        ClassificationInput: 마스킹된 입력. 민감값 표가 없어 알려진 이메일·라벨 시리얼만 가린다.
    """
    return build_input(record, sensitive_values={}).payload


def needs_translation(payload: ClassificationInput) -> bool:
    """원문이 이미 한국어(ko, ko-KR 등)면 False."""
    return payload.body_language.split("-", 1)[0] != "ko"


def body_limit(payload: ClassificationInput) -> int:
    """본문 번역 최대 길이."""
    return min(MAX_BODY_CHARS, max(MIN_BODY_LIMIT, BODY_RATIO * len(payload.body)))


def input_sha256(payload: ClassificationInput) -> str:
    """번역 입력 판본. 문의를 고치면 바뀌어 이전 번역이 오래된 것으로 보인다."""
    encoded = json.dumps(
        {"v": TRANSLATION_INPUT_VERSION, **payload.model_dump(mode="json")},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def render_prompt(payload: ClassificationInput) -> str:
    return INSTRUCTION + "\n\n입력:\n" + payload.model_dump_json()


def validate_output(raw: str, *, source: ClassificationInput) -> TranslationOutput:
    """모델 원시 출력을 검사한다.

    Args:
        raw: 모델 응답 텍스트(JSON).
        source: 모델에 보낸 마스킹 입력.

    Returns:
        TranslationOutput: 규칙을 지킨 번역(앞뒤 공백만 뗀다). 어기면 TranslationOutputError(코드만)다.
    """
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
        output = TranslationOutput.model_validate(data)
    except (ValueError, ValidationError, TypeError, RecursionError):
        raise TranslationOutputError("CS_TRANSLATION_SCHEMA") from None
    subject = output.subject_ko.strip()
    body = output.body_ko.strip()
    has_subject = bool((source.subject or "").strip())
    if bool(subject) != has_subject or len(body) > body_limit(source):
        raise TranslationOutputError("CS_TRANSLATION_SCHEMA")
    output = TranslationOutput(subject_ko=subject, body_ko=body)
    if not HANGUL.search(body):
        raise TranslationOutputError("CS_TRANSLATION_NOT_KOREAN")
    text = f"{subject}\n{body}"
    original = f"{source.subject or ''} {source.body}"
    if ungrounded(text, original) or any(
        not set(pattern.findall(text)) <= set(pattern.findall(original))
        for pattern in (DOTTED, PLACEHOLDER)
    ):
        raise TranslationOutputError("CS_TRANSLATION_UNGROUNDED")
    if commits(text):
        raise TranslationOutputError("CS_TRANSLATION_COMMITMENT")
    return output
