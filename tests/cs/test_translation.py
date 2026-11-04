"""한국어 번역 엔진: 입력 마스킹·판본과 출력 검사(DB·모델 없음)."""

import json

import pytest

from backend.engine.cs.summary import build_summary_input
from backend.engine.cs.summary import input_sha256 as summary_sha256
from backend.engine.cs.translation import (
    MAX_BODY_CHARS,
    MAX_SUBJECT_CHARS,
    TRANSLATION_PROMPT_VERSION,
    TranslationOutputError,
    body_limit,
    build_translation_input,
    input_sha256,
    needs_translation,
    render_prompt,
    validate_output,
)

TICKET = {
    "channel": "EMAIL",
    "body_language": "es",
    "subject": "Fuga de aceite en TRF-OIL-1000",
    "body": (
        "La junta pierde aceite desde 2026-10-02.\n\n"
        "Error E-204, firmware 4.2.1, 35 kV. Escríbame a buyer@example.com."
    ),
}
SUBJECT_KO = "TRF-OIL-1000 누유"
BODY_KO = (
    "2026-10-02부터 개스킷에서 기름이 샙니다.\n\n"
    "오류 E-204, 펌웨어 4.2.1, 35 kV. [PERSONAL_PROVIDED]로 연락 주세요."
)


def raw(subject: str = SUBJECT_KO, body: str = BODY_KO) -> str:
    return json.dumps({"subject_ko": subject, "body_ko": body}, ensure_ascii=False)


def test_the_input_is_masked_and_versioned_apart_from_the_summary():
    """이메일은 분류·요약과 같이 가리고, 원문이 바뀌면 판본도 바뀐다. 같은 원문이어도 요약과 판본이 다르다."""
    payload = build_translation_input(TICKET)
    assert "buyer@example.com" not in payload.body and "[PERSONAL_PROVIDED]" in payload.body
    prompt = render_prompt(payload)
    assert "buyer@example.com" not in prompt and "body_ko" in prompt and "subject_ko" in prompt
    assert input_sha256(payload) == input_sha256(build_translation_input(dict(TICKET)))
    changed = build_translation_input({**TICKET, "body": TICKET["body"] + " Urgente."})
    assert input_sha256(changed) != input_sha256(payload)
    assert input_sha256(payload) != summary_sha256(build_summary_input(TICKET))
    assert TRANSLATION_PROMPT_VERSION == "v1"


def test_a_faithful_translation_keeps_lines_and_passes():
    output = validate_output(
        raw(f"  {SUBJECT_KO} ", BODY_KO + "\n"), source=build_translation_input(TICKET)
    )
    assert output.subject_ko == SUBJECT_KO
    # 문단 나눔은 그대로, 앞뒤 공백만 뗀다.
    assert output.body_ko == BODY_KO and "\n\n" in output.body_ko


@pytest.mark.parametrize(
    ("subject", "body", "code"),
    [
        (SUBJECT_KO, "Oil leaks since 2026-10-02.", "CS_TRANSLATION_NOT_KOREAN"),
        # 원문에 없는 시리얼·날짜·수치·판본은 지어낸 것이다.
        (SUBJECT_KO, "시리얼 SN-99812 장비에서 기름이 샙니다.", "CS_TRANSLATION_UNGROUNDED"),
        (SUBJECT_KO, "펌웨어 4.2.2에서 기름이 샙니다.", "CS_TRANSLATION_UNGROUNDED"),
        (SUBJECT_KO, "3일 전부터 기름이 샙니다.", "CS_TRANSLATION_UNGROUNDED"),
        ("TRF-OIL-2000 누유", "기름이 샙니다.", "CS_TRANSLATION_UNGROUNDED"),
        # 원문에 없는 가림 표시도 만들 수 없다.
        (SUBJECT_KO, "[SERIAL_PROVIDED] 장비에서 기름이 샙니다.", "CS_TRANSLATION_UNGROUNDED"),
        (SUBJECT_KO, "누유 건은 보증이 승인되었습니다.", "CS_TRANSLATION_COMMITMENT"),
        ("", BODY_KO, "CS_TRANSLATION_SCHEMA"),  # 원문에 제목이 있는데 비움
        (SUBJECT_KO, "   ", "CS_TRANSLATION_SCHEMA"),
        ("가" * (MAX_SUBJECT_CHARS + 1), BODY_KO, "CS_TRANSLATION_SCHEMA"),
        (SUBJECT_KO, "가" * (MAX_BODY_CHARS + 1), "CS_TRANSLATION_SCHEMA"),
    ],
)
def test_translations_breaking_the_rules_are_rejected(subject, body, code):
    with pytest.raises(TranslationOutputError) as caught:
        validate_output(raw(subject, body), source=build_translation_input(TICKET))
    assert caught.value.code == code
    # 오류에는 원문·출력이 남지 않는다.
    assert str(caught.value) == code


def test_the_body_may_be_at_most_three_times_the_source():
    """본문 번역은 마스킹한 원문 본문의 3배(최소 200자)와 8000자 중 작은 값까지다."""
    short = build_translation_input({**TICKET, "subject": None, "body": "Ruido."})
    assert body_limit(short) == 200
    validate_output(raw("", "가" * 200), source=short)
    with pytest.raises(TranslationOutputError) as caught:
        validate_output(raw("", "가" * 201), source=short)
    assert caught.value.code == "CS_TRANSLATION_SCHEMA"
    long = build_translation_input({**TICKET, "body": "a" * 5000})
    assert body_limit(long) == MAX_BODY_CHARS
    payload = build_translation_input(TICKET)
    assert body_limit(payload) == 3 * len(payload.body)


def test_a_ticket_without_subject_needs_an_empty_subject():
    payload = build_translation_input({**TICKET, "subject": None})
    assert validate_output(raw("", BODY_KO), source=payload).subject_ko == ""
    with pytest.raises(TranslationOutputError) as caught:
        validate_output(raw("제목", BODY_KO), source=payload)
    assert caught.value.code == "CS_TRANSLATION_SCHEMA"


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        '{"subject_ko": "a", "body_ko": "누유", "body_ko": "덮어쓰기"}',
        '{"subject_ko": "a", "body_ko": "누유", "extra": 1}',
        '{"body_ko": "누유"}',
        '{"subject_ko": "a", "body_ko": 1}',
        "[]",
    ],
)
def test_malformed_outputs_are_schema_errors(value):
    with pytest.raises(TranslationOutputError) as caught:
        validate_output(value, source=build_translation_input(TICKET))
    assert caught.value.code == "CS_TRANSLATION_SCHEMA"


def test_number_words_stay_words_and_reported_claims_are_not_commitments():
    """글자로 쓴 수는 낱말로 옮기고(숫자로 바꾸면 원문에 없는 수), 고객이 들었다는 승인은 옮길 수 있다."""
    payload = build_translation_input(
        {**TICKET, "subject": None, "body": "It leaks for three days. I was told it was approved."}
    )
    validate_output(raw("", "사흘째 샙니다. 승인되었다고 들었습니다."), source=payload)
    with pytest.raises(TranslationOutputError) as caught:
        validate_output(raw("", "3일째 샙니다."), source=payload)
    assert caught.value.code == "CS_TRANSLATION_UNGROUNDED"
    with pytest.raises(TranslationOutputError) as caught:
        validate_output(raw("", "사흘째 샙니다. 보증이 승인되었습니다."), source=payload)
    assert caught.value.code == "CS_TRANSLATION_COMMITMENT"


def test_korean_tickets_need_no_translation():
    assert needs_translation(build_translation_input(TICKET))
    assert needs_translation(build_translation_input({**TICKET, "body_language": "en"}))
    for language in ("ko", "ko-KR"):
        assert not needs_translation(build_translation_input({**TICKET, "body_language": language}))
