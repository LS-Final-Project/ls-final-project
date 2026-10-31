"""C/S 분류 단일 시도의 연결 경계. SDK 생성·재시도·DB 저장은 수행하지 않는다."""

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

from backend.engine.cs.classification import (
    INPUT_VERSION,
    INPUT_VERSION_STRIPPED,
    ClassificationInput,
    ClassificationOutput,
    build_input,
    validate_output,
)
from backend.shared.ai import Generation, Usage

CallErrorCode = Literal["CS_RATE_LIMITED", "CS_PROVIDER_TIMEOUT", "CS_PROVIDER_ERROR"]


class ClassificationCallError(Exception):
    """제공자 어댑터가 원문 없이 전달하는 실패와 알려진 사용량."""

    def __init__(
        self,
        code: CallErrorCode,
        *,
        usage: Usage | None = None,
        http_status: int | None = None,
        call_attempts: int | None = None,
    ) -> None:
        """허용된 오류 코드와 사용량만 복사해 전달한다.

        Args:
            code: 레이트 제한·타임아웃·기타 호출 실패의 고정 코드.
            usage: 제공자가 알려준 사용량. 미제공 값은 null로 유지한다.
            http_status: 알려진 HTTP 상태. 제공자 메시지는 받지 않는다.
            call_attempts: 실패까지의 실제 호출 수(재시도 포함). 모르면 None이다.

        Returns:
            None: 시도 경계에서 처리할 안전한 오류를 만든다.
        """
        if code not in {"CS_RATE_LIMITED", "CS_PROVIDER_TIMEOUT", "CS_PROVIDER_ERROR"}:
            raise ValueError("CS_INVALID_CALL_ERROR")
        if http_status is not None and (
            type(http_status) is not int or not 100 <= http_status <= 599
        ):
            raise ValueError("CS_INVALID_CALL_ERROR")
        super().__init__(code)
        self.code = code
        self.usage = replace(usage) if usage is not None else Usage()
        self.http_status = http_status
        self.call_attempts = (
            call_attempts if type(call_attempts) is int and call_attempts >= 1 else None
        )


@dataclass(frozen=True)
class ClassificationAttempt:
    """메모리 내 시도 스냅샷. raw_response는 미마스킹 내부 자료이며 공개·저장용이 아니다."""

    attempt_number: int
    outcome: Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD"] = "HELD"
    input_version: str = INPUT_VERSION
    input_sha256: str | None = None
    removed_annotations: int | None = None
    call_started: bool = False
    execution_uncertain: bool = False
    error_code: str | None = None
    http_status: int | None = None
    latency_ms: float | None = None
    usage: Usage = field(default_factory=Usage)
    raw_response: str | None = field(default=None, repr=False)
    prediction: ClassificationOutput | None = field(default=None, repr=False)
    model_version: str | None = field(default=None, repr=False)
    finish_reasons: tuple[str, ...] = field(default=(), repr=False)
    # 응답의 Generation.model·attempts. 공급자 예외로 Generation이 없으면 None이다.
    requested_model: str | None = field(default=None, repr=False)
    call_attempts: int | None = None
    # 분류 프롬프트 판본. 공급자가 판본을 고르므로 이 함수는 모르고, 저장 직전에 실행기(build_cs_runner)가 채운다.
    prompt_version: str | None = None


def run_classification_attempt(
    record: Mapping[str, object],
    *,
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]],
    allowed_info_codes: frozenset[str],
    provider: Callable[[ClassificationInput], Generation],
    attempt_number: int,
    strip_phone_annotation: bool = False,
) -> ClassificationAttempt:
    """정제 입력을 한 번 호출하고 원시 검사 결과와 사용량을 보존한다.

    Args:
        record: 정규화된 문의. 정답·사후 정보는 build_input에서 제외한다.
        sensitive_values: 어댑터가 식별한 민감값과 마스킹 종류.
        allowed_info_codes: 검수된 정보 코드 집합. 기본값을 추측하지 않는다.
        provider: 정제된 네 필드만 받아 공통 Generation을 반환하는 호출자.
        attempt_number: 외부 실행기가 부여한 1 이상의 시도 번호.
        strip_phone_annotation: 분석용. build_input에 그대로 넘긴다(기본 False, 판본은 입력 판본에 남는다).

    Returns:
        ClassificationAttempt: 내부 시도 기록. 입력 실패는 호출하지 않으며 자동 재시도하지 않는다.
    """
    if type(attempt_number) is not int or attempt_number < 1:
        raise ValueError("CS_INVALID_ATTEMPT_NUMBER")
    result = ClassificationAttempt(
        attempt_number,
        input_version=INPUT_VERSION_STRIPPED if strip_phone_annotation else INPUT_VERSION,
    )
    try:
        prepared = build_input(
            record,
            sensitive_values=sensitive_values,
            strip_phone_annotation=strip_phone_annotation,
        )
    except ValueError as error:
        input_code = str(error)
        if input_code not in {
            "CS_INVALID_INPUT",
            "CS_EMPTY_INPUT",
            "CS_UNSUPPORTED_PHONE_ANNOTATION",
        }:
            input_code = "CS_INVALID_INPUT"
        return replace(result, error_code=input_code)

    encoded = json.dumps(
        prepared.payload.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result = replace(
        result,
        input_sha256=hashlib.sha256(encoded).hexdigest(),
        removed_annotations=prepared.removed_annotations,
        call_started=True,
    )
    started = time.perf_counter()
    try:
        response = provider(prepared.payload)
    except Exception as error:
        # 실패 원문이나 임의 예외 이름을 기록하지 않는다. 원격 미실행으로 단정하지 않는다.
        code: CallErrorCode = "CS_PROVIDER_ERROR"
        usage = Usage()
        http_status = None
        attempts = None
        if isinstance(error, ClassificationCallError):
            code, usage, http_status = error.code, replace(error.usage), error.http_status
            attempts = error.call_attempts
        elif isinstance(error, TimeoutError):
            code = "CS_PROVIDER_TIMEOUT"
        return replace(
            result,
            outcome="TIMEOUT" if code == "CS_PROVIDER_TIMEOUT" else "TRANSPORT_FAILURE",
            execution_uncertain=True,
            error_code=code,
            http_status=http_status,
            usage=usage,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            call_attempts=attempts,
        )

    result = replace(
        result,
        latency_ms=round((time.perf_counter() - started) * 1000, 3),
        usage=replace(response.usage),
        raw_response=response.text,
        model_version=response.model_version,
        finish_reasons=tuple(response.finish_reasons),
        requested_model=response.model,
        call_attempts=response.attempts,
    )
    try:
        prediction = validate_output(response.text, allowed_info_codes=allowed_info_codes)
    except ValueError:
        return replace(result, outcome="SCHEMA_VIOLATION", error_code="CS_INVALID_OUTPUT")
    return replace(result, outcome="VALID", prediction=prediction)
