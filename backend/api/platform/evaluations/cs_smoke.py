"""버전이 고정된 C/S 합성 사례. 실제 자료·SDK·사용자 지정 실행기는 받지 않는다."""

import hashlib
import json
from typing import Any, TypedDict

from backend.engine.cs.classification import INPUT_VERSION, ClassificationInput
from backend.engine.cs.classification_call import (
    ClassificationAttempt,
    ClassificationCallError,
    run_classification_attempt,
)
from backend.shared.ai import Generation, Usage


class SyntheticCase(TypedDict):
    """고정 입력 본문과 응답 또는 응답 없음의 합성 정의."""

    body: str
    response: str | None


PROFILE = {"task": "cs_classification", "fixture_version": "cs-smoke-v1"}
PROMPT_VERSION = "cs-smoke-fixed-v1"
# 응답·사용량까지 합성 fixture의 일부이며 실제 품질·소비량으로 집계하지 않는다.
FIXTURES: dict[str, SyntheticCase] = {
    "cs-smoke-valid": {
        "body": "Synthetic return inquiry.",
        "response": '{"category_code":"RETURN","secondary_category":null,"urgency_flag":"NONE",'
        '"provided_info":[],"return_subtype":null}',
    },
    "cs-smoke-invalid": {
        "body": "Synthetic malformed response inquiry.",
        "response": "synthetic-person@example.test invalid response",
    },
    "cs-smoke-timeout": {"body": "Synthetic timeout inquiry.", "response": None},
}
DATASET_HASH = hashlib.sha256(
    json.dumps(FIXTURES, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
TARGET_SET_HASH = hashlib.sha256("\n".join(sorted(FIXTURES)).encode()).hexdigest()


def matches_profile(experiment: dict[str, Any]) -> bool:
    """합성 입력·응답 집합과 실행 판본이 정확히 고정됐는지 확인한다.

    Args:
        experiment: 승인 hash 검사와 별도로 확인할 실험 메타데이터.

    Returns:
        bool: 고정 합성 C/S 프로필에 일치하면 True.
    """
    return (
        experiment["configuration"] == PROFILE
        and experiment["input_builder_version"] == INPUT_VERSION
        and experiment["prompt_version"] == PROMPT_VERSION
        and experiment["dataset_hash"] == DATASET_HASH
        and experiment["target_set_hash"] == TARGET_SET_HASH
    )


def run_case(target_ref: str) -> ClassificationAttempt:
    """고정 합성 사례를 기존 C/S 단일 시도 경계로 실행한다.

    Args:
        target_ref: 동결된 합성 집합에서 검증한 대상 식별자.

    Returns:
        ClassificationAttempt: 시도1의 내부 결과. 원시 응답은 저장 전에 전부 가려야 한다.
    """
    fixture = FIXTURES[target_ref]

    def fake_provider(payload: ClassificationInput) -> Generation:
        """외부 효과 없이 고정 응답 또는 타임아웃을 반환한다.

        Args:
            payload: build_input이 검증한 입력.

        Returns:
            Generation: 합성 사용량과 응답. 타임아웃 사례는 고정 오류다.
        """
        if fixture["response"] is None:
            raise ClassificationCallError("CS_PROVIDER_TIMEOUT", usage=Usage(input_tokens=7))
        return Generation(fixture["response"], Usage(7, 3, None, 0, 10), "cs-smoke-v1", ["STOP"])

    return run_classification_attempt(
        {"channel": "EMAIL", "body_language": "en", "subject": None, "body": fixture["body"]},
        sensitive_values={},
        allowed_info_codes=frozenset(),
        provider=fake_provider,
        attempt_number=1,
    )
