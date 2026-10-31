"""KAN-131 한국어 요약 엔진: 입력 마스킹·판본과 출력 검사(DB·모델 없음)."""

import json

import pytest

from backend.engine.cs.summary import (
    MAX_CHARS,
    SummaryOutputError,
    build_summary_input,
    input_sha256,
    render_prompt,
    validate_output,
)

TICKET = {
    "channel": "EMAIL",
    "body_language": "en",
    "subject": "Oil leak on TRF-OIL-1000",
    "body": "Gasket leaks since 2026-10-02. Error E-204. Contact me at buyer@example.com.",
}


def raw(text: str) -> str:
    return json.dumps({"summary_ko": text}, ensure_ascii=False)


def test_the_input_is_masked_and_versioned_by_its_text():
    """이메일은 분류와 같이 가리고, 원문이 바뀌면 판본도 바뀐다. 프롬프트에 가린 입력만 들어간다."""
    payload = build_summary_input(TICKET)
    assert "buyer@example.com" not in payload.body
    prompt = render_prompt(payload)
    assert "buyer@example.com" not in prompt and "summary_ko" in prompt
    assert input_sha256(payload) == input_sha256(build_summary_input(dict(TICKET)))
    changed = build_summary_input({**TICKET, "body": TICKET["body"] + " Urgent."})
    assert input_sha256(changed) != input_sha256(payload)


def test_a_grounded_korean_summary_passes():
    payload = build_summary_input(TICKET)
    output = validate_output(
        raw("TRF-OIL-1000 개스킷에서 2026-10-02부터 누유가 있고 오류 E-204가 났다고 함."),
        source=payload,
    )
    assert output.summary_ko.startswith("TRF-OIL-1000")


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Oil leak since 2026-10-02.", "CS_SUMMARY_NOT_KOREAN"),
        # 원문에 없는 시리얼·날짜·수치는 지어낸 것이다.
        ("시리얼 SN-99812 장비에서 누유가 있다고 함.", "CS_SUMMARY_UNGROUNDED"),
        ("3일 전부터 누유가 있다고 함.", "CS_SUMMARY_UNGROUNDED"),
        ("누유 건은 보증이 승인되었습니다.", "CS_SUMMARY_COMMITMENT"),
        ("가" * (MAX_CHARS + 1), "CS_SUMMARY_SCHEMA"),
        ("   ", "CS_SUMMARY_SCHEMA"),
    ],
)
def test_summaries_breaking_the_rules_are_rejected(text, code):
    with pytest.raises(SummaryOutputError) as caught:
        validate_output(raw(text), source=build_summary_input(TICKET))
    assert caught.value.code == code
    # 오류에는 원문·출력이 남지 않는다.
    assert str(caught.value) == code


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        '{"summary_ko": "누유", "summary_ko": "덮어쓰기"}',
        '{"summary_ko": "누유", "extra": 1}',
        '{"summary_ko": 1}',
        "[]",
    ],
)
def test_malformed_outputs_are_schema_errors(value):
    with pytest.raises(SummaryOutputError) as caught:
        validate_output(value, source=build_summary_input(TICKET))
    assert caught.value.code == "CS_SUMMARY_SCHEMA"


@pytest.mark.parametrize(
    ("text", "grounded"),
    [
        ("모델 TRF-OIL-1000.", True),  # 문장 끝 마침표는 코드가 아니다
        ("오류 E-204/E-204가 반복됐다고 함.", True),  # /는 나누는 기호
        ("2026년 10월 2일부터 누유가 있다고 함.", True),  # 날짜를 바꿔 써도 각 수가 원문에 있다
        ("20일 전부터 누유가 있다고 함.", False),  # 원문에 없는 수
        ("e-205 오류가 났다고 함.", False),  # 원문에 없는 코드
    ],
)
def test_codes_must_match_and_numbers_must_exist_by_value(text, grounded):
    payload = build_summary_input(TICKET)
    if grounded:
        validate_output(raw(text), source=payload)
    else:
        with pytest.raises(SummaryOutputError) as caught:
            validate_output(raw(text), source=payload)
        assert caught.value.code == "CS_SUMMARY_UNGROUNDED"


def test_a_reported_claim_is_not_a_commitment():
    """고객이 들었다·주장한다는 인용은 요약이고, 사실처럼 쓴 승인은 막는다."""
    payload = build_summary_input(TICKET)
    validate_output(raw("고객은 보증이 승인되었다고 주장함."), source=payload)
    with pytest.raises(SummaryOutputError):
        validate_output(raw("고객 문의는 확인했고 보증이 승인되었습니다."), source=payload)
