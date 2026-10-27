"""실제 자료와 모델을 사용하지 않는 C/S 입력·원시 출력 계약 회귀."""

import copy
import json

import pytest

from backend.engine.cs.classification import INPUT_VERSION, build_input, validate_output

# 실제 코드북이 아닌 검사 전용 코드. 운영 코드북은 KAN-92/95에서 연결한다.
INFO_CODES = frozenset({"TEST_SERIAL", "TEST_PHOTO"})
VALID = {
    "category_code": "RETURN",
    "secondary_category": None,
    "urgency_flag": "NONE",
    "provided_info": [],
    "return_subtype": None,
}


def test_input_whitelist_and_no_mutation():
    """정답·사후 기록·별도 오류 칸은 모델 입력으로 전달하지 않는다.

    Args:
        없음.

    Returns:
        None: 네 필드만 선택하고 원본을 보존함을 확인한다.
    """
    record = {
        "channel": "CHAT",
        "body_language": "en",
        "subject": None,
        "body": "May I return it?",
        "category_code": "GOLD_SECRET",
        "secondary_category": "GOLD_SECRET",
        "urgency_flag": "GOLD_SECRET",
        "provided_info": ["GOLD_SECRET"],
        "resolution_case": {"text": "FUTURE_SECRET"},
        "error_code_reported": "EXTRA_SECRET",
        "fw_version_reported": "EXTRA_SECRET",
        "return_subtype": "GOLD_SECRET",
    }
    before = copy.deepcopy(record)
    prepared = build_input(record, sensitive_values={})
    assert prepared.payload.model_dump() == {
        "channel": "CHAT",
        "body_language": "en",
        "subject": None,
        "body": "May I return it?",
    }
    assert prepared.version == INPUT_VERSION
    assert prepared.removed_annotations == 0
    assert record == before


@pytest.mark.parametrize("channel", ["PHONE", "EMAIL"])
def test_annotation_and_sensitive_values(channel):
    """주석은 PHONE에서만 제거하고 알려진 민감값과 이메일·시리얼을 가린다.

    Args:
        channel: 입력 채널.

    Returns:
        None: 의미 있는 증상과 정보 제공 표시를 보존한다.
    """
    record = {
        "channel": channel,
        "body_language": "ko",
        "subject": "Urgency: SITE_DOWN",
        "body": "Urgency: SAFETY\nurgency : NONE\nTrip at 300 V. serial: ZX-123; a@example.test 김테스트",
    }
    prepared = build_input(record, sensitive_values={"김테스트": "PERSONAL"})
    payload = prepared.payload.model_dump_json()
    assert "ZX-123" not in payload and "a@example.test" not in payload and "김테스트" not in payload
    assert "[SERIAL_PROVIDED]" in payload and "[PERSONAL_PROVIDED]" in payload
    assert "Trip at 300 V." in prepared.payload.body
    assert prepared.removed_annotations == (3 if channel == "PHONE" else 0)
    assert ("SAFETY" in payload) is (channel != "PHONE")
    assert "ZX-123" in record["body"]


@pytest.mark.parametrize("label", ["S/N", "Serial number", "Serial No.", "시리얼 번호"])
def test_serial_labels_and_known_unlabelled_values(label):
    """명시적 시리얼 표기와 어댑터가 식별한 원값을 마스킹한다.

    Args:
        label: 시리얼 표기.

    Returns:
        None: 제목·본문의 원값을 숨긴다.
    """
    record = {
        "channel": "WEB_FORM",
        "body_language": "en",
        "subject": "ZX-123",
        "body": f"{label}: AB-234, repeated ZX-123. error E42",
    }
    result = build_input(record, sensitive_values={"ZX-123": "SERIAL"})
    text = result.payload.model_dump_json()
    assert "AB-234" not in text and "ZX-123" not in text
    assert "error E42" in text


@pytest.mark.parametrize(
    "change",
    [
        {"channel": "INVALID"},
        {"body": None},
        {"body": 3},
        {"body": ""},
        {"subject": []},
        {"body_language": ""},
        {"body_language": "private@example.test"},
    ],
)
def test_invalid_input_is_safe(change):
    """잘못된 입력을 원문 없는 고정 오류로 거부한다.

    Args:
        change: 합성 입력 위반.

    Returns:
        None: 타입 변환이나 빈 성공이 없음을 확인한다.
    """
    record = {"channel": "CHAT", "body_language": "en", "subject": None, "body": "private"} | change
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        build_input(record, sensitive_values={})


def test_missing_field_and_annotation_only_are_rejected():
    """누락 입력과 정답 주석만 있던 본문은 실행에 넘기지 않는다.

    Args:
        없음.

    Returns:
        None: 결손 입력을 실패 처리한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        build_input({"body": "private"}, sensitive_values={})
    with pytest.raises(ValueError, match="^CS_EMPTY_INPUT$"):
        build_input(
            {
                "channel": "PHONE",
                "body_language": "en",
                "subject": None,
                "body": "Urgency: SAFETY\n",
            },
            sensitive_values={},
        )


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"secondary_category": "INSTALL"},
        {"return_subtype": "DEFECT"},
        {"category_code": "QUOTE", "secondary_category": "RETURN", "return_subtype": "NON_DEFECT"},
        {"provided_info": ["TEST_SERIAL", "TEST_PHOTO"]},
    ],
)
def test_valid_output_preserves_unknown_return_and_secondary(change):
    """복합 의도와 불명확 반품의 null을 추측하거나 보정하지 않는다.

    Args:
        change: 계약에 맞는 합성 출력.

    Returns:
        None: 원래 다섯 필드를 그대로 반환한다.
    """
    value = VALID | change
    assert validate_output(json.dumps(value), allowed_info_codes=INFO_CODES).model_dump() == value


@pytest.mark.parametrize(
    "change",
    [
        {"category_code": "UNKNOWN"},
        {"urgency_flag": "CRITICAL"},
        {"secondary_category": ""},
        {"secondary_category": "RETURN"},
        {"provided_info": "TEST_SERIAL"},
        {"provided_info": [1]},
        {"provided_info": ["TEST_SERIAL", "TEST_SERIAL"]},
        {"provided_info": ["ZX-PRIVATE-123"]},
        {"return_subtype": "unknown"},
        {"category_code": "QUOTE", "return_subtype": "DEFECT"},
        {"priority": "P1"},
        {"assigned_dept": "PRIVATE"},
        {"product_family": "PRIVATE"},
    ],
)
def test_raw_output_violations_are_not_repaired(change):
    """타입·관계·미등록 코드·추가 필드 위반을 원문 없는 실패로 남긴다.

    Args:
        change: 원시 출력 위반.

    Returns:
        None: 폴백 성공으로 바꾸지 않음을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_OUTPUT$"):
        validate_output(json.dumps(VALID | change), allowed_info_codes=INFO_CODES)


@pytest.mark.parametrize("field", list(VALID))
def test_all_five_keys_required(field):
    """null 허용 필드도 원시 출력에서 빠지면 위반이다.

    Args:
        field: 누락시킬 키.

    Returns:
        None: 기본값을 채워 성공으로 숨기지 않는다.
    """
    value = {key: item for key, item in VALID.items() if key != field}
    with pytest.raises(ValueError, match="^CS_INVALID_OUTPUT$"):
        validate_output(json.dumps(value), allowed_info_codes=INFO_CODES)


@pytest.mark.parametrize(
    "raw",
    [
        "not json PRIVATE",
        "[]",
        "null",
        '"PRIVATE"',
        "```json\n" + json.dumps(VALID) + "\n```",
        json.dumps(VALID)[:-1] + ', "category_code":"QUOTE"}',
    ],
)
def test_invalid_json_and_duplicate_keys(raw):
    """문법 오류·코드펜스·중복 키를 원시 스키마 위반으로 거부한다.

    Args:
        raw: 잘못된 합성 응답.

    Returns:
        None: 원문 비노출과 엄격한 파싱을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_OUTPUT$"):
        validate_output(raw, allowed_info_codes=INFO_CODES)


@pytest.mark.parametrize(
    "sensitive", [{1: "SERIAL"}, {"private": []}, {"": "SERIAL"}, {"private": "UNKNOWN"}]
)
def test_invalid_sensitive_mapping_is_safe(sensitive):
    """잘못된 민감값 타입·종류는 원래 예외 대신 고정 오류로 거부한다.

    Args:
        sensitive: 잘못된 어댑터 매핑.

    Returns:
        None: 원문 없는 CS_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        build_input(
            {"channel": "CHAT", "body_language": "en", "subject": None, "body": "private"},
            sensitive_values=sensitive,
        )


def test_spaced_serial_requires_adapter_full_value():
    """공백 포함 시리얼은 어댑터가 식별한 전체 값을 사용해 가린다.

    Args:
        없음.

    Returns:
        None: 원값 전체 제거와 증상 보존을 확인한다.
    """
    record = {
        "channel": "EMAIL",
        "body_language": "en",
        "subject": "AB 1234",
        "body": "Serial number: AB 1234; trip at 300 V",
    }
    result = build_input(record, sensitive_values={"AB 1234": "SERIAL"})
    text = result.payload.model_dump_json()
    assert "1234" not in text and "AB" not in text
    assert "[SERIAL_PROVIDED]" in text
    assert "trip at 300 V" in text


@pytest.mark.parametrize("value", ["person@example.test", "김테스트"])
@pytest.mark.parametrize("field", ["subject", "body"])
def test_serial_label_preserves_personal_mask(value, field):
    """시리얼 라벨 뒤에서도 식별된 개인정보의 마스킹 종류를 보존한다.

    Args:
        value: 합성 개인정보.
        field: 민감값이 들어갈 입력 필드.

    Returns:
        None: 원값은 숨기고 PERSONAL 표시를 SERIAL로 바꾸지 않는다.
    """
    record = {"channel": "CHAT", "body_language": "ko", "subject": None, "body": "Trip."}
    record[field] = f"Serial number: {value}."
    result = build_input(record, sensitive_values={"김테스트": "PERSONAL"})
    text = result.payload.model_dump_json()
    assert value not in text
    assert "[PERSONAL_PROVIDED]" in text
    assert "[SERIAL_PROVIDED]" not in text


@pytest.mark.parametrize(
    "annotation",
    [
        "**Urgency:** SAFETY",
        "Urgency: **SITE_DOWN**",
        "`Urgency: NONE`",
        "Urgency：DEADLINE",
        "__Urgency__: __PROD_IMPACT__",
        "*Urgency: COMMISSION*",
    ],
)
def test_supported_phone_annotation_wrappers(annotation):
    """지원하는 주석 표기 전체를 제거해 빈 마크다운을 문의로 통과시키지 않는다.

    Args:
        annotation: 지원하는 합성 PHONE 주석 표기.

    Returns:
        None: 증상은 유지하고 주석만 있던 본문은 거부한다.
    """
    record = {"channel": "PHONE", "body_language": "en", "subject": annotation, "body": annotation}
    with pytest.raises(ValueError, match="^CS_EMPTY_INPUT$"):
        build_input(record, sensitive_values={})
    record["body"] += "\nTrip at 300 V."
    result = build_input(record, sensitive_values={})
    assert result.payload.subject == ""
    assert result.payload.body == "\nTrip at 300 V."
    assert result.removed_annotations == 2


@pytest.mark.parametrize(
    "value", ["SAFETY-extra", "NONE_LIKE", "SITE_DOWN/SAFETY", "HIGH", "**SAFETY**-extra"]
)
def test_unknown_phone_annotation_is_held_without_partial_removal(value):
    """알 수 없는 주석 값은 일부만 지우지 않고 안전하게 보류한다.

    Args:
        value: 계약 밖의 합성 주석 값.

    Returns:
        None: 원문 없는 오류로 보류하며 원본과 다른 채널은 보존한다.
    """
    record = {
        "channel": "PHONE",
        "body_language": "en",
        "subject": None,
        "body": f"Urgency: {value}\nTrip at 300 V.",
    }
    before = copy.deepcopy(record)
    with pytest.raises(ValueError, match="^CS_UNSUPPORTED_PHONE_ANNOTATION$"):
        build_input(record, sensitive_values={})
    assert record == before
    record["channel"] = "EMAIL"
    result = build_input(record, sensitive_values={})
    assert result.payload.body == record["body"]
    assert result.removed_annotations == 0
