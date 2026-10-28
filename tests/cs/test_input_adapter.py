"""KAN-92 경계 검사: 실제 자료·모델·DB 없이 합성 입력만 사용한다."""

import copy

import pytest

from backend.engine.cs import input_adapter


def sample():
    """검수 주장을 포함한 합성 입력을 만든다."""
    return {
        "channel": "PHONE",
        "body_language": "ko",
        "subject": None,
        "body": "Urgency: SAFETY\n합성사용자 장비 AB 1234 과열",
        "privacy_review_complete": True,
        "privacy_review_status": "approved",
        "category_code": "GOLD_SECRET",
        "assigned_dept": "GOLD_SECRET",
        "routing_rule_id": "GOLD_SECRET",
        "resolution_case": {"text": "FUTURE_SECRET"},
    }


@pytest.mark.parametrize("review", [False, None, "approved", "true", 1, {}, []])
def test_unverified_input_never_reaches_builder(review, monkeypatch):
    """레코드의 승인 주장·빈 매핑·truthy 값으로 검수 보류를 우회하지 못한다."""

    def unexpected(*args, **kwargs):
        pytest.fail("보류 입력이 정제 함수에 도달함")

    monkeypatch.setattr(input_adapter, "build_input", unexpected)
    with pytest.raises(ValueError, match="^CS_PRIVACY_REVIEW_REQUIRED$"):
        input_adapter.prepare_cs_input(
            sample(), privacy_review_complete=review, sensitive_values={}
        )


def test_default_review_and_missing_mapping_are_held():
    """검수 및 민감값 매핑을 묵시적으로 완료 처리하지 않는다."""
    with pytest.raises(ValueError, match="^CS_PRIVACY_REVIEW_REQUIRED$"):
        input_adapter.prepare_cs_input(sample(), sensitive_values={})
    with pytest.raises(ValueError, match="^CS_SENSITIVE_VALUES_REQUIRED$"):
        input_adapter.prepare_cs_input(sample(), privacy_review_complete=True)


def test_reviewed_synthetic_input_uses_v3_and_preserves_original():
    """어댑터→실제 v3 함수 연결에서 정답 제외·마스킹·감사 분리를 확인한다."""
    record = sample()
    sensitive = {"합성사용자": "PERSONAL", "AB 1234": "SERIAL"}
    before = copy.deepcopy((record, sensitive))
    result = input_adapter.prepare_cs_input(
        record, privacy_review_complete=True, sensitive_values=sensitive
    )
    assert result.payload.model_dump() == {
        "channel": "PHONE",
        "body_language": "ko",
        "subject": None,
        "body": "\n[PERSONAL_PROVIDED] 장비 [SERIAL_PROVIDED] 과열",
    }
    assert result.version == "cs-classification-input-v3"
    assert result.removed_annotations == 1
    assert (record, sensitive) == before


@pytest.mark.parametrize(
    ("change", "missing", "error"),
    [
        ({"body": 3}, None, "CS_INVALID_INPUT"),
        ({}, "subject", "CS_INVALID_INPUT"),
        ({"body": "Urgency: SAFETY"}, None, "CS_EMPTY_INPUT"),
        ({"body": "Urgency: PRIVATE_VALUE"}, None, "CS_UNSUPPORTED_PHONE_ANNOTATION"),
    ],
)
def test_builder_failure_is_preserved_without_raw_fallback(change, missing, error):
    """정제 실패를 원문 성공으로 대체하거나 오류에 입력을 노출하지 않는다."""
    record = sample() | change
    if missing:
        del record[missing]
    before = copy.deepcopy(record)
    with pytest.raises(ValueError) as caught:
        input_adapter.prepare_cs_input(record, privacy_review_complete=True, sensitive_values={})
    assert str(caught.value) == error
    assert record == before


@pytest.mark.parametrize("record,sensitive", [(None, {}), ({}, []), ({}, {"x": "BAD"})])
def test_invalid_boundary_types_have_safe_error(record, sensitive):
    """잘못된 경계 타입을 원문 없는 고정 오류로 거부한다."""
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        input_adapter.prepare_cs_input(
            record, privacy_review_complete=True, sensitive_values=sensitive
        )
