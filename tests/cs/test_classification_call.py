"""C/S 호출 경계의 합성 회귀. 네트워크·모델·실제 데이터에 접근하지 않는다."""

import json
from dataclasses import asdict
from unittest.mock import Mock

import pytest

from backend.engine.cs.classification import INPUT_VERSION
from backend.engine.cs.classification_call import (
    ClassificationCallError,
    run_classification_attempt,
)
from backend.shared.ai import Generation, Usage

RECORD = {
    "channel": "PHONE",
    "body_language": "en",
    "subject": None,
    "body": "Urgency: SAFETY\nserial: ZX-PRIVATE; person@example.test reports a trip.",
    "category_code": "GOLD_SECRET",
    "resolution_case": "FUTURE_SECRET",
}
RAW = json.dumps(
    {
        "category_code": "RETURN",
        "secondary_category": None,
        "urgency_flag": "NONE",
        "provided_info": [],
        "return_subtype": None,
    }
)


def attempt(provider, *, record=None, number=1):
    """합성 설정으로 단일 시도를 실행한다.

    Args:
        provider: 주입할 fake 호출자.
        record: 대체할 합성 입력.
        number: 시도 번호.

    Returns:
        ClassificationAttempt: 외부 호출 없는 시도 결과.
    """
    return run_classification_attempt(
        RECORD if record is None else record,
        sensitive_values={},
        allowed_info_codes=frozenset({"TEST_SERIAL"}),
        provider=provider,
        attempt_number=number,
    )


def test_only_prepared_input_reaches_provider_once():
    """정제한 네 필드만 한 번 보내고 결과·사용량·판본을 기록한다.

    Args:
        없음.

    Returns:
        None: 정답·사후 기록·민감값·PHONE 주석 비전달을 확인한다.
    """
    provider = Mock(return_value=Generation(RAW, Usage(10, 3, None, 0, 13), "fake-v1", ["STOP"]))
    result = attempt(provider)
    provider.assert_called_once()
    payload = provider.call_args.args[0].model_dump()
    assert set(payload) == {"channel", "body_language", "subject", "body"}
    for secret in ("GOLD_SECRET", "FUTURE_SECRET", "ZX-PRIVATE", "person@example.test", "SAFETY"):
        assert secret not in json.dumps(payload)
    assert "[SERIAL_PROVIDED]" in payload["body"]
    assert "[PERSONAL_PROVIDED]" in payload["body"]
    assert result.outcome == "VALID" and result.prediction is not None
    assert result.prediction.return_subtype is None
    assert result.input_version == INPUT_VERSION and result.removed_annotations == 1
    assert result.input_sha256 and len(result.input_sha256) == 64
    assert result.raw_response == RAW
    assert result.usage == Usage(10, 3, None, 0, 13)
    assert result.model_version == "fake-v1" and result.finish_reasons == ("STOP",)
    assert result.call_started and not result.execution_uncertain
    assert result.latency_ms is not None and result.latency_ms >= 0


@pytest.mark.parametrize(
    "record,code",
    [
        ({}, "CS_INVALID_INPUT"),
        (RECORD | {"body": "Urgency: NONE"}, "CS_EMPTY_INPUT"),
        (RECORD | {"body": "Urgency: HIGH\nTrip"}, "CS_UNSUPPORTED_PHONE_ANNOTATION"),
    ],
)
def test_rejected_input_never_calls_provider(record, code):
    """입력 보류는 제공자를 호출하거나 원문으로 우회하지 않는다.

    Args:
        record: 합성 입력 위반.
        code: 기대하는 안전 오류.

    Returns:
        None: 미호출 상태와 제공되지 않은 메타데이터를 확인한다.
    """
    provider = Mock()
    result = attempt(provider, record=record)
    provider.assert_not_called()
    assert result.outcome == "HELD" and result.error_code == code
    assert not result.call_started and not result.execution_uncertain
    assert result.raw_response is result.input_sha256 is result.latency_ms is None
    assert result.removed_annotations is None
    assert all(value is None for value in asdict(result.usage).values())


@pytest.mark.parametrize("raw", ["", "RAW_SECRET", RAW.replace('"NONE"', '"INVALID"')])
def test_schema_failure_keeps_received_usage_without_retry(raw):
    """빈 응답도 수신된 원시 위반이며 사용량을 버리거나 자동 재시도하지 않는다.

    Args:
        raw: 합성 원시 스키마 위반.

    Returns:
        None: 원시 응답·사용량 보존과 안전한 repr을 확인한다.
    """
    provider = Mock(return_value=Generation(raw, Usage(4, 2, None, None, 6), "fake-v1", ["STOP"]))
    result = attempt(provider)
    provider.assert_called_once()
    assert result.outcome == "SCHEMA_VIOLATION" and result.error_code == "CS_INVALID_OUTPUT"
    assert result.raw_response == raw and result.prediction is None
    assert result.usage == Usage(4, 2, None, None, 6)
    assert result.call_started and not result.execution_uncertain
    assert "raw_response=" not in repr(result) and "RAW_SECRET" not in repr(result)


@pytest.mark.parametrize(
    "error,outcome,code",
    [
        (RuntimeError("PRIVATE_REQUEST_TOKEN"), "TRANSPORT_FAILURE", "CS_PROVIDER_ERROR"),
        (TimeoutError("PRIVATE_REQUEST_TOKEN"), "TIMEOUT", "CS_PROVIDER_TIMEOUT"),
    ],
)
def test_unknown_call_failure_does_not_claim_zero_usage_or_non_execution(error, outcome, code):
    """예외 원문을 숨기고 원격 실행 여부와 미제공 사용량을 추측하지 않는다.

    Args:
        error: 원문이 포함될 수 있는 합성 예외.
        outcome: 시도 실패 종류.
        code: 안전한 오류 코드.

    Returns:
        None: 한 번 호출 후 불확실 상태로 남음을 확인한다.
    """
    provider = Mock(side_effect=error)
    result = attempt(provider)
    provider.assert_called_once()
    assert result.outcome == outcome and result.error_code == code
    assert result.call_started and result.execution_uncertain
    assert result.raw_response is result.prediction is result.model_version is None
    assert all(value is None for value in asdict(result.usage).values())
    assert "PRIVATE_REQUEST_TOKEN" not in repr(result)


@pytest.mark.parametrize(
    "code,status,outcome",
    [
        ("CS_RATE_LIMITED", 429, "TRANSPORT_FAILURE"),
        ("CS_PROVIDER_TIMEOUT", None, "TIMEOUT"),
    ],
)
def test_typed_failure_preserves_known_usage(code, status, outcome):
    """알려진 실패의 사용량·HTTP 상태를 안전하게 보존한다.

    Args:
        code: 정규화된 실패 코드.
        status: 제공자가 알려준 HTTP 상태.
        outcome: 기대하는 시도 종류.

    Returns:
        None: 실패도 사용량을 남기고 재시도하지 않음을 확인한다.
    """
    provider = Mock(
        side_effect=ClassificationCallError(code, usage=Usage(8, None), http_status=status)
    )
    result = attempt(provider)
    provider.assert_called_once()
    assert result.outcome == outcome and result.error_code == code
    assert result.usage == Usage(8, None) and result.http_status == status
    assert result.execution_uncertain and result.raw_response is None


def test_later_attempt_does_not_overwrite_first_response_or_usage():
    """명시적 다음 시도와 제공자 객체 재사용이 최초 결과를 바꾸지 않는다.

    Args:
        없음.

    Returns:
        None: 최초 원시 위반과 시도별 사용량·판본 스냅샷을 확인한다.
    """
    usage = Usage(5, 1)
    response = Generation("FIRST_RAW_SECRET", usage, "fake-v1", ["STOP"])
    provider = Mock(return_value=response)
    first = attempt(provider)
    usage.input_tokens = 99
    response.text = RAW
    response.finish_reasons.append("NEW_REASON")
    second = attempt(provider, number=2)
    assert provider.call_count == 2
    assert first.attempt_number == 1 and first.outcome == "SCHEMA_VIOLATION"
    assert first.raw_response == "FIRST_RAW_SECRET" and first.usage.input_tokens == 5
    assert first.finish_reasons == ("STOP",)
    assert second.attempt_number == 2 and second.outcome == "VALID"
    assert second.usage.input_tokens == 99
    assert first.input_sha256 == second.input_sha256
    assert "FIRST_RAW_SECRET" not in repr(first)


@pytest.mark.parametrize("number", [0, -1, True])
def test_invalid_attempt_number_never_calls_provider(number):
    """잘못된 시도 번호를 호출 전에 거부한다.

    Args:
        number: 유효하지 않은 시도 번호.

    Returns:
        None: 안전한 구성 오류와 미호출을 확인한다.
    """
    provider = Mock()
    with pytest.raises(ValueError, match="^CS_INVALID_ATTEMPT_NUMBER$"):
        attempt(provider, number=number)
    provider.assert_not_called()


def test_hash_uses_prepared_input_not_gold_or_original_personal_values():
    """입력 hash는 정제된 네 필드만 반영한다.

    Args:
        없음.

    Returns:
        None: 정답·마스킹 전 개인정보는 제외하고 실제 증상 변경은 반영함을 확인한다.
    """
    provider = Mock(return_value=Generation(RAW, Usage(), None, []))
    first = attempt(provider)
    changed = RECORD | {
        "category_code": "OTHER_GOLD",
        "body": RECORD["body"].replace("person@example.test", "other@example.test"),
    }
    same = attempt(provider, record=changed)
    different = attempt(provider, record=RECORD | {"body": "No trip; a manual is needed."})
    assert first.input_sha256 == same.input_sha256
    assert first.input_sha256 != different.input_sha256


def test_cancellation_propagates_without_retry():
    """호출자 중단을 성공이나 자동 재시도로 바꾸지 않는다.

    Args:
        없음.

    Returns:
        None: 중단 예외가 상위 실행기로 전달됨을 확인한다.
    """
    provider = Mock(side_effect=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        attempt(provider)
    provider.assert_called_once()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"code": "PRIVATE_MESSAGE"},
        {"code": "CS_PROVIDER_ERROR", "http_status": "PRIVATE_STATUS"},
        {"code": "CS_PROVIDER_ERROR", "http_status": True},
    ],
)
def test_failure_adapter_rejects_unstructured_diagnostics(kwargs):
    """제공자 원문이 오류 코드·상태 칸으로 유입되지 않게 한다.

    Args:
        kwargs: 잘못된 합성 실패 메타데이터.

    Returns:
        None: 원문 없는 구성 오류를 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_CALL_ERROR$"):
        ClassificationCallError(**kwargs)
