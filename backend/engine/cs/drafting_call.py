"""C/S 답변 초안 단일 시도의 연결 경계(KAN-106). SDK 생성·재시도·DB 저장은 수행하지 않는다."""

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.classification_call import CallErrorCode, ClassificationCallError
from backend.engine.cs.drafting import (
    DRAFT_INPUT_VERSION,
    DraftOutput,
    EvidenceSnippet,
    build_draft_input,
    render_prompt,
    validate_output,
)
from backend.shared.ai import Generation, Usage


@dataclass(frozen=True)
class DraftAttempt:
    """메모리 내 초안 시도 스냅샷. raw_response는 내부 자료이며 공개·저장용이 아니다."""

    attempt_number: int
    outcome: Literal["VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD"] = "HELD"
    input_version: str = DRAFT_INPUT_VERSION
    input_sha256: str | None = None
    removed_annotations: int | None = None
    call_started: bool = False
    execution_uncertain: bool = False
    error_code: str | None = None
    http_status: int | None = None
    latency_ms: float | None = None
    usage: Usage = field(default_factory=Usage)
    raw_response: str | None = field(default=None, repr=False)
    prediction: DraftOutput | None = field(default=None, repr=False)
    model_version: str | None = field(default=None, repr=False)
    finish_reasons: tuple[str, ...] = field(default=(), repr=False)
    requested_model: str | None = field(default=None, repr=False)
    call_attempts: int | None = None


def run_draft_attempt(
    record: Mapping[str, object],
    *,
    classification: ClassificationOutput,
    required_info_codes: frozenset[str],
    already_asked_codes: frozenset[str],
    evidence: list[EvidenceSnippet],
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]],
    provider: Callable[[str], Generation],
    attempt_number: int,
) -> DraftAttempt:
    """정제 입력으로 프롬프트를 만들어 한 번 호출하고 원시 검사 결과와 사용량을 보존한다.

    Args:
        record: 정규화된 문의.
        classification: 검사를 통과한 분류 결과.
        required_info_codes: 문의 유형에 필요한 정보 코드.
        already_asked_codes: 이미 요청한 정보 코드.
        evidence: 근거 스니펫.
        sensitive_values: 어댑터가 식별한 민감값과 마스킹 종류.
        provider: 프롬프트 문자열을 받아 공통 Generation을 반환하는 호출자.
            실제 연결은 generate(client, settings, prompt, response_schema=DraftOutput)이다.
        attempt_number: 외부 실행기가 부여한 1 이상의 시도 번호.

    Returns:
        DraftAttempt: 내부 시도 기록. 입력 실패는 호출하지 않으며 자동 재시도하지 않는다.
            금지 약속은 SCHEMA_VIOLATION과 CS_FORBIDDEN_COMMITMENT로 남는다.
    """
    if type(attempt_number) is not int or attempt_number < 1:
        raise ValueError("CS_INVALID_ATTEMPT_NUMBER")
    result = DraftAttempt(attempt_number)
    try:
        prepared = build_draft_input(
            record,
            classification=classification,
            required_info_codes=required_info_codes,
            already_asked_codes=already_asked_codes,
            evidence=evidence,
            sensitive_values=sensitive_values,
        )
    except ValueError as error:
        input_code = str(error)
        if input_code not in {
            "CS_INVALID_INPUT",
            "CS_EMPTY_INPUT",
            "CS_UNSUPPORTED_PHONE_ANNOTATION",
            "CS_BLOCKED_EVIDENCE",
        }:
            input_code = "CS_INVALID_INPUT"
        return replace(result, error_code=input_code)

    payload = prepared.payload
    result = replace(
        result,
        input_sha256=hashlib.sha256(payload.model_dump_json().encode("utf-8")).hexdigest(),
        removed_annotations=prepared.removed_annotations,
        call_started=True,
    )
    started = time.perf_counter()
    try:
        response = provider(render_prompt(payload))
    except Exception as error:
        # 실패 원문이나 임의 예외 이름을 기록하지 않는다. 원격 미실행으로 단정하지 않는다.
        code: CallErrorCode = "CS_PROVIDER_ERROR"
        usage = Usage()
        http_status = None
        if isinstance(error, ClassificationCallError):
            code, usage, http_status = error.code, replace(error.usage), error.http_status
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
        prediction = validate_output(
            response.text,
            evidence_chunk_ids=frozenset(snippet.chunk_id for snippet in payload.evidence),
            request_info_codes=frozenset(payload.request_info_codes),
        )
    except ValueError as error:
        return replace(result, outcome="SCHEMA_VIOLATION", error_code=str(error))
    return replace(result, outcome="VALID", prediction=prediction)
