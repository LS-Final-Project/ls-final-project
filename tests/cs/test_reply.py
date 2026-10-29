"""KAN-109 고객 회신 해석 입력·출력·초안 입력 갱신 회귀. 네트워크·모델·실제 데이터 없음."""

import json
from datetime import UTC, datetime

import pytest

from backend.engine.cs.reply import (
    REPLY_INPUT_VERSION,
    CustomerReply,
    ReplyContext,
    ReplyUpdate,
    apply_reply,
    build_reply_input,
    render_prompt,
    validate_output,
)

CONFIRMED = "ZX-CONF-0001"
CONTEXT = ReplyContext(
    ticket_product_code="SYN-MCCB-100",
    fw_version_reported="1.2.0",
    requested_info_codes=["TEST_INVOICE", "TEST_PHOTO", "TEST_SERIAL"],
    already_provided_codes=["TEST_MODEL"],
    prior_options=None,
)


def reply(body, channel="EMAIL"):
    """합성 회신을 만든다.

    Args:
        body: 회신 본문.
        channel: 접수 채널.

    Returns:
        CustomerReply: 회신.
    """
    return CustomerReply(
        channel=channel,
        body=body,
        body_language="en",
        received_at=datetime(2026, 10, 29, tzinfo=UTC),
    )


def prepare(body, *, confirmed=CONFIRMED, sensitive=None, channel="EMAIL"):
    """합성 회신으로 정제 입력을 만든다.

    Args:
        body: 회신 본문.
        confirmed: 확정 장비 시리얼 원값.
        sensitive: 어댑터 민감값.
        channel: 접수 채널.

    Returns:
        PreparedReplyInput: 정제 입력.
    """
    return build_reply_input(
        CONTEXT,
        reply(body, channel),
        confirmed_unit_serial=confirmed,
        sensitive_values=sensitive or {},
    )


def raw(
    *,
    request="APPLIES",
    unit="NOT_MENTIONED",
    version="NOT_MENTIONED",
    answered=("TEST_INVOICE", "TEST_PHOTO", "TEST_SERIAL"),
    facts=(("Breaker trips at 300 V.", "STATED"),),
    questions=(),
):
    """원시 해석 JSON을 만든다.

    Args:
        request: same_request.
        unit: same_unit.
        version: same_version.
        answered: 답한 코드.
        facts: (문장, 종류) 목록.
        questions: (코드, 질문) 목록.

    Returns:
        str: 원시 응답.
    """
    return json.dumps(
        {
            "applicability": {
                "same_request": request,
                "same_request_reason": "Reply answers the photo request.",
                "same_unit": unit,
                "same_unit_reason": "Unit is not restated.",
                "same_version": version,
                "same_version_reason": "Firmware is not restated.",
            },
            "answered_codes": list(answered),
            "new_facts": [{"text": text, "kind": kind} for text, kind in facts],
            "remaining_questions": [{"info_code": c, "question": q} for c, q in questions],
        }
    )


def check(text, serial_match="NOT_MENTIONED"):
    """기본 요청 코드로 원시 응답을 검사한다.

    Args:
        text: 원시 응답.
        serial_match: 입력의 시리얼 대조 결과.

    Returns:
        ReplyInterpretation: 검사 결과.
    """
    return validate_output(
        text,
        requested_info_codes=frozenset(CONTEXT.requested_info_codes),
        serial_match=serial_match,
    )


def test_masking_hides_contacts_serials_and_confirmed_unit():
    """이메일·라벨 시리얼·확정 시리얼(대소문자 달라도)·민감값과 PHONE 주석이 입력에서 빠진다.

    Args:
        없음.

    Returns:
        None: 정제 입력과 프롬프트에 원값이 없음을 확인한다.
    """
    body = (
        "Urgency: SAFETY\nHi, person@example.test here (김고객). serial: ZX-OTHER-9; "
        "also zx-conf-0001 trips at 300 V."
    )
    prepared = prepare(body, sensitive={"김고객": "PERSONAL"}, channel="PHONE")
    assert prepared.version == REPLY_INPUT_VERSION and prepared.removed_annotations == 1
    prompt = render_prompt(prepared.payload)
    for secret in (
        "person@example.test",
        "김고객",
        "ZX-OTHER-9",
        "zx-conf-0001",
        CONFIRMED,
        "2026",
    ):
        assert secret.casefold() not in prompt.casefold()
    assert "SAFETY" not in prepared.payload.body and "trips at 300 V." in prepared.payload.body
    assert prepared.payload.reply_serial_match == "DIFFERS"
    assert "answered_codes" in prompt and "NOT_MENTIONED" in prompt


def test_confirmed_serial_masks_the_whole_containing_token():
    """확정 시리얼을 품은 더 긴 토큰은 꼬리 없이 표식 하나로 가린다.

    Args:
        없음.

    Returns:
        None: 원값과 꼬리가 남지 않음을 확인한다.
    """
    assert prepare("unit ZX-CONF-00012 trips.").payload.body == "unit [SERIAL_PROVIDED] trips."


def test_invalid_input_errors_never_carry_the_confirmed_serial():
    """잘못된 입력 오류 메시지에 확정 시리얼 원값이 들어가지 않는다.

    Args:
        없음.

    Returns:
        None: 안전한 오류 코드만 나온다.
    """
    for kwargs in ({"sensitive": {CONFIRMED: "SECRET"}}, {"confirmed": "   "}):
        with pytest.raises(ValueError) as error:
            prepare(f"serial: {CONFIRMED}", **kwargs)
        assert str(error.value) == "CS_INVALID_INPUT" and CONFIRMED not in repr(error.value)


@pytest.mark.parametrize(
    ("body", "confirmed", "sensitive", "expected"),
    [
        ("Photo attached.", CONFIRMED, {}, "NOT_MENTIONED"),
        ("Unit ZX-RAND-77 photo attached.", CONFIRMED, {}, "NOT_MENTIONED"),
        ("Serial No.:  zx-conf-0001 .", CONFIRMED, {}, "MATCHES"),
        ("It is unit ZX-CONF-0001", f"  {CONFIRMED} ", {CONFIRMED: "SERIAL"}, "MATCHES"),
        ("serial: ZX-CONF-00012", CONFIRMED, {}, "DIFFERS"),
        ("unit ZX-CONF-00012 trips.", CONFIRMED, {CONFIRMED: "SERIAL"}, "NOT_MENTIONED"),
        ("serial: ZX-CONF-00012", CONFIRMED, {CONFIRMED: "SERIAL"}, "DIFFERS"),
        ("It is unit zx-conf-0001.", CONFIRMED, {CONFIRMED: "SERIAL"}, "MATCHES"),
        ("The other unit ZX-SECOND-2 trips.", CONFIRMED, {"ZX-SECOND-2": "SERIAL"}, "DIFFERS"),
        ("serial: not provided", CONFIRMED, {}, "NOT_MENTIONED"),
        ("serial: ZX-CONF-0001", None, {}, "NO_CONFIRMED_UNIT"),
    ],
)
def test_serial_match_is_exact_and_never_guesses(body, confirmed, sensitive, expected):
    """시리얼 대조는 정확 일치(대소문자·앞뒤 공백 무시)이며 라벨 없는 임의 토큰을 시리얼로 보지 않는다.

    Args:
        body: 회신 본문.
        confirmed: 확정 장비 시리얼.
        sensitive: 어댑터 민감값.
        expected: 대조 결과.

    Returns:
        None: 대조 결과를 확인한다.
    """
    assert prepare(body, confirmed=confirmed, sensitive=sensitive).payload.reply_serial_match == (
        expected
    )


def test_full_and_partial_answers_pass_validation():
    """전부 답한 회신과 일부만 답하고 남은 질문을 둔 회신을 받는다.

    Args:
        없음.

    Returns:
        None: 검사 결과를 확인한다.
    """
    assert check(raw()).answered_codes == ["TEST_INVOICE", "TEST_PHOTO", "TEST_SERIAL"]
    partial = check(
        raw(answered=["TEST_PHOTO"], questions=[("TEST_INVOICE", "Please send the invoice.")])
    )
    assert [q.info_code for q in partial.remaining_questions] == ["TEST_INVOICE"]


@pytest.mark.parametrize(
    ("text", "serial_match"),
    [
        (raw(answered=["TEST_PHOTO", "TEST_OTHER"]), "NOT_MENTIONED"),
        (raw(questions=[("TEST_OTHER", "Please send it.")]), "NOT_MENTIONED"),
        (raw(answered=["TEST_PHOTO", "TEST_PHOTO"]), "NOT_MENTIONED"),
        (raw(answered=["TEST_PHOTO"], questions=[("TEST_PHOTO", "Again?")]), "NOT_MENTIONED"),
        (raw(unit="SAME"), "DIFFERS"),
        (raw(unit="NOT_MENTIONED"), "DIFFERS"),
        (raw(unit="UNCLEAR"), "DIFFERS"),
        (raw(unit="SAME"), "NO_CONFIRMED_UNIT"),
        (raw(unit="NOT_MENTIONED"), "NO_CONFIRMED_UNIT"),
        (raw(unit="DIFFERENT"), "MATCHES"),
        (raw(unit="GUESSED"), "NOT_MENTIONED"),
        (raw()[:-1] + ', "answered_codes": []}', "NOT_MENTIONED"),
        (raw()[:-1] + ', "extra": 1}', "NOT_MENTIONED"),
    ],
)
def test_out_of_scope_codes_and_contradictions_are_rejected(text, serial_match):
    """요청 밖 코드·중복·겹침·시리얼 대조 모순·중복 키·추가 키를 거부한다.

    Args:
        text: 원시 응답.
        serial_match: 입력의 시리얼 대조 결과.

    Returns:
        None: CS_INVALID_OUTPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_OUTPUT$"):
        check(text, serial_match)


@pytest.mark.parametrize(
    "text",
    [
        raw(facts=[("Your RMA is approved.", "STATED")]),
        raw().replace("Reply answers the photo request.", "The RMA has been approved."),
        raw(
            answered=["TEST_PHOTO"],
            questions=[("TEST_INVOICE", "We will send a technician tomorrow, OK?")],
        ),
    ],
)
def test_forbidden_commitments_are_rejected(text):
    """새 사실·남은 질문의 승인·확정 약속을 거부한다.

    Args:
        text: 원시 응답.

    Returns:
        None: CS_FORBIDDEN_COMMITMENT를 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_FORBIDDEN_COMMITMENT$"):
        check(text)


@pytest.mark.parametrize(
    ("kwargs", "action"),
    [
        ({}, "DRAFT_ANSWER"),
        ({"answered": ["TEST_PHOTO"]}, "REQUEST_AGAIN"),
        ({"unit": "DIFFERENT"}, "ESCALATE_TO_AGENT"),
        ({"version": "DIFFERENT"}, "ESCALATE_TO_AGENT"),
        ({"request": "DIFFERENT_REQUEST", "unit": "UNCLEAR"}, "ESCALATE_TO_AGENT"),
        ({"version": "UNCLEAR", "answered": []}, "CONFIRM_WITH_CUSTOMER"),
        ({"request": "UNCLEAR"}, "CONFIRM_WITH_CUSTOMER"),
        ({"unit": "SAME", "version": "SAME"}, "DRAFT_ANSWER"),
    ],
)
def test_apply_reply_next_action(kwargs, action):
    """다른 요청·장비·판본 > UNCLEAR > 부족 코드 순으로 다음 행동을 고른다.

    Args:
        kwargs: raw에 넘길 판정.
        action: 기대 다음 행동.

    Returns:
        None: 다음 행동을 확인한다.
    """
    assert apply_reply(CONTEXT, check(raw(**kwargs))).next_action == action


def test_code_computed_different_serial_overrides_model_and_escalates():
    """회신이 다른 시리얼을 말하면 모델이 NOT_MENTIONED로 가려도 거부하고, DIFFERENT면 사람에게 넘긴다.

    Args:
        없음.

    Returns:
        None: DIFFERS가 DRAFT_ANSWER로 새지 않음을 확인한다.
    """
    serial_match = prepare(
        "serial: ZX-OTHER-9, photo and invoice attached."
    ).payload.reply_serial_match
    assert serial_match == "DIFFERS"
    with pytest.raises(ValueError, match="^CS_INVALID_OUTPUT$"):
        check(raw(unit="NOT_MENTIONED"), serial_match)
    update = apply_reply(CONTEXT, check(raw(unit="DIFFERENT"), serial_match))
    assert update.next_action == "ESCALATE_TO_AGENT" and update.provided_codes == ["TEST_MODEL"]
    assert update.still_missing == CONTEXT.requested_info_codes


def test_serial_without_confirmed_unit_stops_automatic_drafting():
    """확정 장비 없이 시리얼을 말한 회신은 NOT_MENTIONED를 거부하고 UNCLEAR면 자동 초안으로 가지 않는다.

    Args:
        없음.

    Returns:
        None: 리뷰 재현 입력이 CONFIRM_WITH_CUSTOMER에서 멈춤을 확인한다.
    """
    serial_match = prepare(
        "serial: ZX-X-1, invoice attached.", confirmed=None
    ).payload.reply_serial_match
    assert serial_match == "NO_CONFIRMED_UNIT"
    with pytest.raises(ValueError, match="^CS_INVALID_OUTPUT$"):
        check(raw(unit="NOT_MENTIONED"), serial_match)
    update = apply_reply(CONTEXT, check(raw(unit="UNCLEAR"), serial_match))
    assert update.next_action == "CONFIRM_WITH_CUSTOMER" and update.provided_codes == ["TEST_MODEL"]


def test_unclear_reply_answers_are_not_counted_as_provided():
    """불명확한 회신의 답은 제공 코드로 합치지 않고 해석에만 남긴다.

    Args:
        없음.

    Returns:
        None: CONFIRM_WITH_CUSTOMER에서 제공·부족 코드가 그대로임을 확인한다.
    """
    interpretation = check(raw(request="UNCLEAR"))
    update = apply_reply(CONTEXT, interpretation)
    assert update.next_action == "CONFIRM_WITH_CUSTOMER" and update.provided_codes == ["TEST_MODEL"]
    assert update.still_missing == CONTEXT.requested_info_codes
    assert interpretation.answered_codes == CONTEXT.requested_info_codes


def test_apply_reply_merges_codes_and_keeps_not_mentioned():
    """제공 코드는 기존 ∪ 답한 코드(정렬), 부족 코드는 요청 − 제공이며 NOT_MENTIONED가 남는다.

    Args:
        없음.

    Returns:
        None: 갱신값과 이력 필드 부재를 확인한다.
    """
    update = apply_reply(CONTEXT, check(raw(answered=["TEST_SERIAL", "TEST_PHOTO"])))
    assert update.provided_codes == ["TEST_MODEL", "TEST_PHOTO", "TEST_SERIAL"]
    assert update.still_missing == ["TEST_INVOICE"] and update.next_action == "REQUEST_AGAIN"
    assert (update.applicability.same_unit, update.applicability.same_version) == (
        "NOT_MENTIONED",
        "NOT_MENTIONED",
    )
    assert set(ReplyUpdate.model_fields) == {
        "provided_codes",
        "still_missing",
        "next_action",
        "applicability",
        "new_facts",
        "remaining_questions",
    }


def test_apply_reply_rejects_answers_outside_the_context():
    """다른 맥락으로 검사한 해석의 요청 밖 답을 거부한다.

    Args:
        없음.

    Returns:
        None: CS_INVALID_INPUT을 확인한다.
    """
    other = CONTEXT.model_copy(update={"requested_info_codes": ["TEST_PHOTO"]})
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        apply_reply(other, check(raw()))
