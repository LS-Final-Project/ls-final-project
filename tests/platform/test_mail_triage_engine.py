"""KAN-73 메일 업무 유형 분류 엔진: 합성 MIME 추출·마스킹·출력 검증·검토 필요 규칙(DB·외부 모델 없음)."""

import json
from email.message import EmailMessage

import pytest

from backend.engine.mail_triage.extract import AttachmentSummary, TriageInput, build_input
from backend.engine.mail_triage.triage import triage, validate_output

FILE = AttachmentSummary(kind="file", content_type="application/pdf", is_inline=False)
LINK = AttachmentSummary(kind="reference", content_type=None, is_inline=False)
LOGO = AttachmentSummary(kind="file", content_type="image/png", is_inline=True)


def mime(
    subject="견적 요청: 변압기 10대", body="새 프로젝트 RFP를 보냅니다.", html=None, **headers
):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "buyer@example.invalid"
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    message.set_content(body)
    if html is not None:
        message.add_alternative(html, subtype="html")
    return message.as_bytes()


def parsed(raw=None, *, folder="inbox", attachments=()):
    return build_input(raw if raw is not None else mime(), folder=folder, attachments=attachments)


def answer(**values):
    body = {
        "mail_type": "NEW_RFP",
        "needs_review": False,
        "evidence": [{"source": "subject", "start": 0, "end": 2}],
        "link_suggestion": "NEW_PROJECT",
    }
    return lambda _input: json.dumps(body | values)


def test_plain_text_is_extracted_and_contacts_and_serials_are_masked():
    raw = mime(
        subject="문의 contact@example.invalid",
        body="장비 serial: VSN-123456 에서 오류가 납니다. 연락처 a.b@example.invalid",
    )
    data = parsed(raw)
    assert data.subject == "문의 [PERSONAL_PROVIDED]"
    assert "VSN-123456" not in data.body and "[SERIAL_PROVIDED]" in data.body
    assert "a.b@example.invalid" not in data.body and data.is_reply is False


def test_html_only_mail_uses_visible_text():
    message = EmailMessage()
    message["Subject"] = "HTML 문의"
    message.set_content(
        "<html><style>p{color:red}</style><script>x()</script><p>보이는 본문</p></html>",
        subtype="html",
    )
    data = parsed(message.as_bytes())
    assert data.body == "보이는 본문"


def test_quoted_history_is_cut_and_reply_headers_are_detected():
    body = "변경 사항을 반영해 주세요.\n\n> 이전 메일 인용\n-----Original Message-----\n이전 본문"
    data = parsed(mime(body=body, In_Reply_To="<abc@example.invalid>"))
    assert data.body == "변경 사항을 반영해 주세요." and data.is_reply is True


def test_long_text_is_limited_and_garbage_bytes_give_a_safe_empty_input():
    assert len(parsed(mime(body="가" * 20_000)).body) == 8_000
    garbage = build_input(b"\xff\xfe" * 3, folder="inbox", attachments=())
    assert (garbage.subject, garbage.is_reply) == ("", False)


def test_input_never_carries_attachment_names():
    assert "name" not in AttachmentSummary.model_fields
    assert set(TriageInput.model_fields) == {"folder", "subject", "body", "is_reply", "attachments"}


def test_a_clean_new_rfp_is_not_flagged_for_review():
    result = triage(parsed(), answer())
    assert (result.mail_type, result.needs_review, result.attachments_pending) == (
        "NEW_RFP",
        False,
        False,
    )
    assert result.link_suggestion == "NEW_PROJECT"


@pytest.mark.parametrize(
    ("attachments", "pending"), [((FILE,), True), ((LINK,), False), ((LOGO,), False)]
)
def test_unprocessed_file_attachments_mean_pending_and_review(attachments, pending):
    result = triage(parsed(attachments=attachments), answer())
    assert result.attachments_pending is pending and result.needs_review is pending


@pytest.mark.parametrize(
    "values",
    [{"mail_type": "OTHER", "link_suggestion": None}, {"evidence": []}, {"needs_review": True}],
)
def test_other_type_no_evidence_or_model_doubt_need_review(values):
    assert triage(parsed(), answer(**values)).needs_review is True


@pytest.mark.parametrize(
    "values",
    [
        {"mail_type": "SPAM"},
        {"mail_type": "CS", "link_suggestion": "NEW_PROJECT"},
        {"mail_type": "OTHER", "link_suggestion": "NEW_CS_TICKET"},
        {"mail_type": "CANDIDATE_REPLY", "link_suggestion": "EXISTING_THREAD"},  # 회신 아님
        {"evidence": [{"source": "body", "start": 0, "end": 10_000}]},
        {"evidence": [{"source": "subject", "start": 3, "end": 3}]},
        {"evidence": [{"source": "reply_header"}]},  # 회신 헤더 없음
        {"evidence": [{"source": "body"}]},
        {"needs_review": "no"},
        {"extra": 1},
    ],
)
def test_outputs_against_the_input_or_the_type_rules_are_rejected(values):
    with pytest.raises(ValueError, match="MAIL_TRIAGE_INVALID_OUTPUT"):
        triage(parsed(), answer(**values))


@pytest.mark.parametrize("raw", ["not json", "[]", json.dumps({"mail_type": "CS"})])
def test_malformed_provider_text_is_rejected(raw):
    with pytest.raises(ValueError, match="MAIL_TRIAGE_INVALID_OUTPUT"):
        validate_output(raw, parsed())


def test_reply_evidence_and_thread_links_are_allowed_for_replies():
    reply = parsed(
        mime(subject="RE: 후보 일정", body="참여 가능합니다.", References="<a@x.invalid>")
    )
    result = triage(
        reply,
        answer(
            mail_type="CANDIDATE_REPLY",
            evidence=[{"source": "reply_header"}, {"source": "body", "start": 0, "end": 5}],
            link_suggestion="EXISTING_THREAD",
        ),
    )
    assert (result.mail_type, result.link_suggestion, result.needs_review) == (
        "CANDIDATE_REPLY",
        "EXISTING_THREAD",
        False,
    )
