"""회신 초안 AI 보조(KAN-251)의 입력 정제와 출력 검사. 모델 호출 없음."""

import json

import pytest

from backend.engine.cs.reply_assist import (
    PROMPT_VERSIONS,
    DeptDraftInput,
    KoreanEditInput,
    KoreanInput,
    ReplyAssistError,
    build_dept_draft_input,
    input_sha256,
    mostly_korean,
    render_prompt,
    validate_output,
)

TICKET = {
    "channel": "EMAIL",
    "body_language": "en",
    "subject": "Breaker trip E-204",
    "body": "Our breaker trips with E-204. Contact buyer@example.com.",
}
RESULT = {
    "result": "펌웨어 4.2.1에서 E-204 오탐이 고쳐졌습니다. 2주 안에 배포 예정.",
    "reply_content": None,
}


def out(text: str) -> str:
    return json.dumps({"text": text}, ensure_ascii=False)


def dept() -> DeptDraftInput:
    return build_dept_draft_input(TICKET, RESULT)


def test_dept_input_masks_contacts_and_keeps_the_department_answer():
    payload = dept()
    assert "buyer@example.com" not in payload.body and "[PERSONAL_PROVIDED]" in payload.body
    assert payload.dept_answer == RESULT["result"] and payload.dept_reply is None
    prompt = render_prompt("DEPT_DRAFT", payload)
    assert "dept_answer" in prompt and "buyer@example.com" not in prompt
    # v2: 부서 답변의 '고객'을 3인칭으로 옮기지 않고 고객에게 직접 말한다.
    assert "2인칭" in prompt and PROMPT_VERSIONS["DEPT_DRAFT"] == "dept-draft-v2"


def test_a_dept_draft_in_the_customer_language_with_department_facts_passes():
    text = "Firmware 4.2.1 fixes the false E-204 trip. It is planned for release within 2 weeks."
    assert validate_output(out(text), kind="DEPT_DRAFT", payload=dept()) == text


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("펌웨어 4.2.1로 고쳐집니다.", "CS_REPLY_ASSIST_LANGUAGE"),
        # 부서 답변에 없는 판본·기간을 만들었다.
        ("Firmware 4.2.2 fixes E-204 within 2 weeks.", "CS_REPLY_ASSIST_UNGROUNDED"),
        ("Firmware 4.2.1 fixes E-204 within 3 weeks.", "CS_REPLY_ASSIST_UNGROUNDED"),
        ("Your RMA is approved. Firmware 4.2.1 fixes E-204.", "CS_FORBIDDEN_COMMITMENT"),
        ("We will replace the breaker free of charge.", "CS_FORBIDDEN_COMMITMENT"),
        ("Please see FLT-042 for the setting.", "CS_REPLY_ASSIST_UNGROUNDED"),
        ('{"text": ""}', "CS_REPLY_ASSIST_SCHEMA"),
    ],
)
def test_a_dept_draft_that_breaks_a_rule_is_rejected_with_a_code(text, code):
    raw = text if text.startswith("{") else out(text)
    with pytest.raises(ReplyAssistError) as caught:
        validate_output(raw, kind="DEPT_DRAFT", payload=dept())
    assert caught.value.code == code


def test_a_blocked_code_is_rejected_even_when_the_input_mentions_it():
    payload = build_dept_draft_input(TICKET, {"result": "FLT-042 기준 감도", "reply_content": None})
    with pytest.raises(ReplyAssistError) as caught:
        validate_output(out("Per FLT-042 the setting applies."), kind="DEPT_DRAFT", payload=payload)
    assert caught.value.code == "CS_BLOCKED_EVIDENCE"


def test_korean_translation_must_be_korean_and_keeps_the_numbers():
    payload = KoreanInput(language="en", text="Firmware 4.2.1 may fix E-204.")
    assert validate_output(
        out("펌웨어 4.2.1로 E-204가 고쳐질 수 있습니다."), kind="TO_KO", payload=payload
    )
    for text, code in (
        ("Firmware 4.2.1 may fix E-204.", "CS_REPLY_ASSIST_LANGUAGE"),
        ("펌웨어 4.2.2로 E-204가 고쳐질 수 있습니다.", "CS_REPLY_ASSIST_UNGROUNDED"),
    ):
        with pytest.raises(ReplyAssistError) as caught:
            validate_output(out(text), kind="TO_KO", payload=payload)
        assert caught.value.code == code


def edit(edited: str) -> KoreanEditInput:
    return KoreanEditInput(
        language="en",
        original="Firmware 4.2.1 may fix E-204. We will check the log.",
        original_ko="펌웨어 4.2.1로 E-204가 고쳐질 수 있습니다. 로그를 확인하겠습니다.",
        edited_ko=edited,
    )


def test_an_edit_can_add_numbers_the_agent_wrote_in_korean():
    payload = edit("펌웨어 4.2.1로 E-204가 고쳐질 수 있습니다. 로그를 3일 안에 확인하겠습니다.")
    text = "Firmware 4.2.1 may fix E-204. We will check the log within 3 days."
    assert validate_output(out(text), kind="FROM_KO", payload=payload) == text
    with pytest.raises(ReplyAssistError) as caught:
        validate_output(out(text.replace("3 days", "5 days")), kind="FROM_KO", payload=payload)
    assert caught.value.code == "CS_REPLY_ASSIST_UNGROUNDED"


def test_a_commitment_the_agent_wrote_passes_but_one_the_ai_added_is_rejected():
    agent = edit("보증으로 무상 교체해 드리겠습니다.")
    text = "We will replace the unit free of charge under warranty."
    assert validate_output(out(text), kind="FROM_KO", payload=agent) == text
    with pytest.raises(ReplyAssistError) as caught:
        validate_output(
            out(text), kind="FROM_KO", payload=edit("교체 여부는 검토 후 안내드립니다.")
        )
    assert caught.value.code == "CS_FORBIDDEN_COMMITMENT"


def test_the_cache_key_changes_with_the_text_and_kind():
    a = KoreanInput(language="en", text="Hello.")
    b = KoreanInput(language="en", text="Hello!")
    assert input_sha256("TO_KO", a) != input_sha256("TO_KO", b)
    assert input_sha256("TO_KO", a) == input_sha256(
        "TO_KO", KoreanInput(language="en", text="Hello.")
    )


def test_mostly_korean_detects_a_korean_draft():
    assert mostly_korean("확인 후 회신드리겠습니다. E-204")
    assert not mostly_korean("We will check E-204 and reply.")
    assert not mostly_korean("12345")


def test_length_cap_allows_a_longer_customer_language_reply_but_not_a_runaway_one():
    # 한국어는 글자가 촘촘해 고객 언어 회신이 몇 배 길어진다(실제 모델 2026-11-09: 167자 → 약 600자, 3배 상한에 걸림).
    answer = (
        "펌웨어 4.2.1에서 E-204 오탐이 고쳐졌습니다. 2주 안에 배포 예정입니다. 배포 전에는 감도 설정을 한 단계 낮추면 "
        "오탐이 줄어듭니다. 설정 방법은 사용 설명서 6장에 있습니다. 배포 일정이 바뀌면 다시 안내합니다."
    )
    payload = build_dept_draft_input(TICKET, {"result": answer, "reply_content": None})
    polite = (
        "Dear customer,\n\nThank you for contacting us about the E-204 trip on your breaker. Our engineering "
        "team has reviewed the case and confirmed that firmware 4.2.1 fixes the false E-204 trip. The release is "
        "planned within 2 weeks. Until then, lowering the sensitivity setting by one step should reduce the false "
        "trips; the procedure is described in chapter 6 of the user manual. If the release schedule changes, we "
        "will let you know.\n\nKind regards."
    )
    assert len(polite) > max(400, 3 * len(answer))
    assert validate_output(out(polite), kind="DEPT_DRAFT", payload=payload) == polite
    with pytest.raises(ReplyAssistError) as caught:
        validate_output(out("Firmware 4.2.1. " * 200), kind="DEPT_DRAFT", payload=payload)
    assert caught.value.code == "CS_REPLY_ASSIST_SCHEMA"
