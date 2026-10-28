"""C/S 분류의 입력 선택과 원시 출력 검사. 모델 호출과 채점은 수행하지 않는다."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Category = Literal[
    "TECH_SPEC",
    "INSTALL",
    "WARRANTY",
    "ORDER_LEAD",
    "MANUAL",
    "DEFECT",
    "COMPLIANCE",
    "QUOTE",
    "FW_UPDATE",
    "RETURN",
]
Urgency = Literal["SAFETY", "SITE_DOWN", "PROD_IMPACT", "COMMISSION", "DEADLINE", "NONE"]
INPUT_VERSION = "cs-classification-input-v3"
_PHONE_LABEL = r"(?<![\w*`-])[*_`]*Urgency[*_`]*[ \t]*[:：][ \t]*[*_`]*"
_PHONE_ANNOTATION = re.compile(
    _PHONE_LABEL
    + r"[ \t]*(?:SAFETY|SITE_DOWN|PROD_IMPACT|COMMISSION|DEADLINE|NONE)"
    + r"[*_`]*+(?![\w/-])",
    re.IGNORECASE,
)
# 제거 형식보다 넓게 남은 라벨을 찾아 접두사 우회도 입력 실패로 보류한다.
_PHONE_UNSUPPORTED = re.compile(r"Urgency[*_`]*\s*[:：]", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+")
_SERIAL = re.compile(
    r"(?i)(\b(?:serial(?:\s+(?:number|no\.?))?|s/n)|시리얼(?:\s*번호)?)"
    r"\s*[:=]\s*(?P<value>[^\s,;<>]+)"
)
_SERIAL_ABSENCE = re.compile(
    r"(?:not[ \t]+provided|n/a|none|unknown|없음)[ \t]*[.!?]?[ \t]*(?=\r?$)",
    re.IGNORECASE | re.MULTILINE,
)


def _mask_serial(match: re.Match[str]) -> str:
    """명확한 미제공 표현과 이미 마스킹한 값의 종류를 보존한다.

    Args:
        match: 시리얼 라벨과 값의 일치 결과.

    Returns:
        str: 미제공 표현·기존 마스킹 표시 또는 원값을 가린 문자열.
    """
    if match["value"].rstrip(".!?:)") in {"[PERSONAL_PROVIDED]", "[SERIAL_PROVIDED]"}:
        return match[0]
    # 값부터 줄 끝까지 확인해 UNKNOWN-123 같은 식별자를 예외로 노출하지 않는다.
    if _SERIAL_ABSENCE.match(match.string, match.start("value")):
        return match[0]
    return "serial: [SERIAL_PROVIDED]"


class ClassificationInput(BaseModel):
    """모델에 전달할 네 필드. 감사 판본은 별도로 보관한다."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    channel: Literal["EMAIL", "PARTNER_PORTAL", "WEB_FORM", "PHONE", "CHAT"]
    body_language: str = Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$", max_length=32)
    subject: str | None
    body: str = Field(min_length=1)


@dataclass(frozen=True)
class PreparedInput:
    """모델용 입력과 모델에 전달하지 않을 처리 판본·주석 제거 건수."""

    payload: ClassificationInput
    version: str
    removed_annotations: int


def mask_contacts_and_serials(value: str) -> str:
    """이메일 주소와 라벨이 붙은 시리얼 값을 가린다(C/S 분류와 메일 분류가 같은 규칙을 쓴다).

    Args:
        value: 제목이나 본문 텍스트.

    Returns:
        str: 이메일은 [PERSONAL_PROVIDED], 시리얼 값은 [SERIAL_PROVIDED]로 바꾼 텍스트.
    """
    return _SERIAL.sub(_mask_serial, _EMAIL.sub("[PERSONAL_PROVIDED]", value))


def build_input(
    record: Mapping[str, object], *, sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]]
) -> PreparedInput:
    """허용 필드만 복사하고 알려진 민감값·PHONE 정답 주석을 제거한다.

    Args:
        record: 어댑터가 정규화한 문의. 추가 정답·사후 기록은 선택하지 않는다.
        sensitive_values: 어댑터가 식별한 원값과 마스킹 종류. 빈 집합도 명시해야 한다.

    Returns:
        PreparedInput: 정제 입력과 감사 메타데이터. 잘못된 입력은 안전한 ValueError다.
    """
    try:
        payload = ClassificationInput.model_validate(
            {key: record[key] for key in ("channel", "body_language", "subject", "body")}
        )
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or value not in {"SERIAL", "PERSONAL"}
            for key, value in sensitive_values.items()
        ):
            raise ValueError
    except (KeyError, ValidationError, ValueError):
        raise ValueError("CS_INVALID_INPUT") from None
    removed = 0
    values = payload.model_dump()
    for field in ("subject", "body"):
        value = values[field]
        if value is None:
            continue
        if payload.channel == "PHONE":
            value, count = _PHONE_ANNOTATION.subn("", value)
            removed += count
            if _PHONE_UNSUPPORTED.search(value):
                raise ValueError("CS_UNSUPPORTED_PHONE_ANNOTATION")
        # 긴 원값부터 한 번에 대체해 겹치는 값이나 대체 표식을 재처리하지 않는다.
        if sensitive_values:
            pattern = re.compile(
                "|".join(re.escape(key) for key in sorted(sensitive_values, key=len, reverse=True))
            )
            value = pattern.sub(
                lambda match: "[" + sensitive_values[match.group()] + "_PROVIDED]", value
            )
        values[field] = mask_contacts_and_serials(value)
    # 주석만 있던 본문은 실행 가능한 문의로 취급하지 않는다.
    if not values["body"].strip():
        raise ValueError("CS_EMPTY_INPUT")
    return PreparedInput(ClassificationInput.model_validate(values), INPUT_VERSION, removed)


class ClassificationOutput(BaseModel):
    """정확히 다섯 필드의 원시 출력. null 허용 필드도 누락할 수 없다."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    category_code: Category
    secondary_category: Category | None
    urgency_flag: Urgency
    provided_info: list[str]
    return_subtype: Literal["DEFECT", "NON_DEFECT"] | None

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        """보조 분류·중복 코드·반품 의도의 필드 관계를 검사한다.

        Args:
            self: 타입 검사를 통과한 원시 출력.

        Returns:
            Self: 관계가 유효한 출력. 의미상 정답 여부를 판정하지 않는다.
        """
        if self.secondary_category == self.category_code:
            raise ValueError("CS_DUPLICATE_CATEGORY")
        if len(set(self.provided_info)) != len(self.provided_info):
            raise ValueError("CS_DUPLICATE_INFO")
        if self.return_subtype is not None and "RETURN" not in (
            self.category_code,
            self.secondary_category,
        ):
            raise ValueError("CS_RETURN_INTENT_REQUIRED")
        return self


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """중복 JSON 키로 원시 위반을 덮어쓰지 못하게 한다.

    Args:
        pairs: JSON 객체의 원래 키 순서.

    Returns:
        dict: 중복 없는 객체.
    """
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("CS_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def validate_output(raw: str, *, allowed_info_codes: frozenset[str]) -> ClassificationOutput:
    """원시 JSON을 정규화·폴백 없이 검사하고 안전한 오류 코드만 노출한다.

    Args:
        raw: 모델의 최초 또는 각 재시도 원시 응답. 호출자가 시도별로 보존한다.
        allowed_info_codes: 검수된 info_field_codebook의 코드 집합. 임의 기본값 없음.

    Returns:
        ClassificationOutput: 유효한 다섯 필드. 위반은 CS_INVALID_OUTPUT ValueError다.
    """
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
        output = ClassificationOutput.model_validate(value)
        if any(code not in allowed_info_codes for code in output.provided_info):
            raise ValueError("CS_UNKNOWN_INFO_CODE")
        return output
    except (ValueError, RecursionError):
        raise ValueError("CS_INVALID_OUTPUT") from None
