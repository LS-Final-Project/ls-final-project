"""C/S 초안 검증 단일 시도의 연결 경계(KAN-200). SDK 생성·재시도·DB 저장은 수행하지 않는다."""

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from backend.engine.cs.classification_call import CallErrorCode, ClassificationCallError
from backend.engine.cs.verification import (
    VERIFICATION_INPUT_VERSION,
    VerificationInput,
    VerificationOutput,
    instruction,
    render_prompt,
    unverified_output,
    validate_output,
)
from backend.shared.ai import Generation, Usage


@dataclass(frozen=True)
class VerificationAttempt:
    """메모리 내 검증 시도 스냅샷. raw_response는 내부 자료이며 공개·저장용이 아니다."""

    attempt_number: int
    payload: VerificationInput = field(repr=False)
    outcome: Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD"] = "HELD"
    input_version: str = VERIFICATION_INPUT_VERSION
    input_sha256: str | None = None
    draft_id: str | None = None
    call_started: bool = False
    execution_uncertain: bool = False
    error_code: str | None = None
    http_status: int | None = None
    latency_ms: float | None = None
    usage: Usage = field(default_factory=Usage)
    raw_response: str | None = field(default=None, repr=False)
    prediction: VerificationOutput | None = field(default=None, repr=False)
    model_version: str | None = field(default=None, repr=False)
    finish_reasons: tuple[str, ...] = field(default=(), repr=False)
    requested_model: str | None = field(default=None, repr=False)
    call_attempts: int | None = None
    prompt_version: str = "v1"
    # 공급자에 보낸 지시문 전체(판본 본문 + 출력 스키마, 입력 제외)의 sha256 앞 16자.
    instruction_fingerprint: str | None = None

    @property
    def output(self) -> VerificationOutput:
        """실패 outcome은 모델 판정과 구분되는 문장별 미검증 이유를 제공한다."""
        return self.prediction or unverified_output(self.payload, self.error_code or self.outcome)


def run_verification_attempt(
    payload: VerificationInput,
    *,
    provider: Callable[[str], Generation],
    attempt_number: int,
    draft_id: str | None = None,
    prompt_version: str = "v1",
) -> VerificationAttempt:
    """검증자를 한 번만 호출한다. 실패 원문은 오류 코드로 바꾸며 자동 재시도하지 않는다."""
    if type(attempt_number) is not int or attempt_number < 1:
        raise ValueError("CS_INVALID_ATTEMPT_NUMBER")
    fingerprint = hashlib.sha256(instruction(prompt_version).encode()).hexdigest()[:16]
    result = VerificationAttempt(
        attempt_number,
        payload,
        draft_id=draft_id,
        prompt_version=prompt_version,
        instruction_fingerprint=fingerprint,
        input_sha256=hashlib.sha256(payload.model_dump_json().encode("utf-8")).hexdigest(),
        call_started=True,
    )
    started = time.perf_counter()
    try:
        response = provider(render_prompt(payload, prompt_version=prompt_version))
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
        prediction = validate_output(response.text, payload=payload)
    except ValueError as error:
        return replace(result, outcome="SCHEMA_VIOLATION", error_code=str(error))
    return replace(result, outcome="VALID", prediction=prediction)
