"""KAN-151 인용 안전·조건/미래 정보 누설 회귀. 네트워크·모델·실제 데이터 없음.

장비 이력의 시점 제한(판정 당시 없던 기록 제외)은 units API 계층의 계약이라 여기서 다루지 않는다.
"""

import json
from datetime import UTC, datetime

import pytest

from backend.engine.cs.classification import ClassificationOutput, build_input
from backend.engine.cs.drafting import EvidenceSnippet, build_draft_input, validate_output
from backend.engine.cs.improvement_reply import (
    DeptReplyText,
    ImprovementContext,
    build_improvement_reply_input,
)
from backend.engine.cs.improvement_reply import render_prompt as render_improvement_prompt
from backend.engine.cs.reply import CustomerReply, ReplyContext, build_reply_input
from backend.engine.cs.reply import render_prompt as render_reply_prompt

RECORD = {
    "channel": "PHONE",
    "body_language": "en",
    "subject": "Breaker trips",
    "body": "**Urgency:** SITE_DOWN\nThe breaker trips at 300 V.",
    "category_code": "FUTURE_CATEGORY",
    "urgency_flag": "FUTURE_URGENCY",
    "final_reply": "FUTURE_REPLY",
    "status": "FUTURE_STATUS",
}
CLASSIFICATION = ClassificationOutput(
    category_code="WARRANTY",
    secondary_category=None,
    urgency_flag="NONE",
    provided_info=[],
    return_subtype=None,
)


def test_classification_input_keeps_only_four_fields_and_phone_annotation() -> None:
    """classification.build_input 경계: 정답·사후 칸은 버리고 PHONE Urgency 표기는 기본으로 남긴다(KAN-105 4차).

    Args:
        없음.

    Returns:
        None: 정제 입력을 확인한다.
    """
    prepared = build_input(RECORD, sensitive_values={})
    assert set(prepared.payload.model_dump()) == {"channel", "body_language", "subject", "body"}
    assert prepared.removed_annotations == 0
    assert prepared.payload.body == RECORD["body"]
    stripped = build_input(RECORD, sensitive_values={}, strip_phone_annotation=True)
    assert stripped.removed_annotations == 1
    assert stripped.payload.body.strip() == "The breaker trips at 300 V."
    assert "FUTURE" not in prepared.payload.model_dump_json()


def draft_raw(ids: list[str]) -> str:
    """세 선택지가 같은 근거 ID를 쓰는 원시 초안을 만든다.

    Args:
        ids: 근거 문장의 chunk_id.

    Returns:
        str: 원시 응답.
    """
    option = {
        "sentences": [
            {"text": "Rated per the manual.", "kind": "EVIDENCE", "evidence_chunk_ids": ids}
        ],
        "info_requests": [],
    }
    labels = ("CONCISE", "DETAILED", "INFO_FIRST")
    return json.dumps({"options": [option | {"label": label} for label in labels]})


def test_draft_rejects_evidence_ids_outside_the_input() -> None:
    """drafting.validate_output 경계: 입력에 준 근거 밖 chunk_id 인용(차단으로 빠진 청크 포함)을 거부한다.

    Args:
        없음.

    Returns:
        None: 허용 ID는 통과하고 빠진 ID는 CS_UNKNOWN_REFERENCE다(KAN-194 6차, 내부 코드 보존).
    """
    allowed = frozenset({"kept"})
    assert validate_output(
        draft_raw(["kept"]), evidence_chunk_ids=allowed, request_info_codes=frozenset()
    )
    with pytest.raises(ValueError, match="^CS_UNKNOWN_REFERENCE$"):
        validate_output(
            draft_raw(["kept", "dropped-flt"]),
            evidence_chunk_ids=allowed,
            request_info_codes=frozenset(),
        )


def test_draft_input_blocks_flt_042_evidence() -> None:
    """drafting.build_draft_input 경계: 근거 스니펫에 FLT-042가 있으면 초안 입력을 만들지 않는다.

    Args:
        없음.

    Returns:
        None: CS_BLOCKED_EVIDENCE를 확인한다.
    """
    evidence = [
        EvidenceSnippet(
            chunk_id="c1",
            snippet="Set leakage sensitivity per FLT-042.",
            section_path=["Faults"],
            document_sha256="a" * 64,
        )
    ]
    record = {key: RECORD[key] for key in ("channel", "body_language", "subject", "body")}
    with pytest.raises(ValueError, match="^CS_BLOCKED_EVIDENCE$"):
        build_draft_input(
            record,
            classification=CLASSIFICATION,
            required_info_codes=frozenset(),
            already_asked_codes=frozenset(),
            evidence=evidence,
            sensitive_values={},
        )


def test_customer_reply_hides_confirmed_serial_and_email_variants() -> None:
    """reply.build_reply_input 경계: S/N 라벨·대문자 차이 확정 시리얼과 꺾쇠 이메일 원값이 입력에 없다.

    Args:
        없음.

    Returns:
        None: 프롬프트에 원값이 없음을 확인한다.
    """
    context = ReplyContext(
        ticket_product_code="SYN-MCCB-100",
        fw_version_reported=None,
        requested_info_codes=["TEST_PHOTO"],
        already_provided_codes=[],
        prior_options=None,
    )
    reply = CustomerReply(
        channel="EMAIL",
        body="From <Ops.Lead+site@example.test>: S/N=zx-conf-7788, photo attached.",
        body_language="en",
        received_at=datetime(2026, 10, 29, tzinfo=UTC),
    )
    prepared = build_reply_input(
        context, reply, confirmed_unit_serial="ZX-CONF-7788", sensitive_values={}
    )
    prompt = render_reply_prompt(prepared.payload).casefold()
    for secret in ("ops.lead+site@example.test", "zx-conf-7788"):
        assert secret not in prompt
    assert prepared.payload.reply_serial_match == "MATCHES"


def test_department_reply_hides_contacts_and_omits_action_record() -> None:
    """improvement_reply.build_improvement_reply_input 경계: 요청서·회신의 원값이 없고 조치 기록 유무가 빠진다.

    Args:
        없음.

    Returns:
        None: 프롬프트와 정제 입력을 확인한다.
    """
    context = ImprovementContext(
        request_type="DOCUMENT",
        product_code="SYN-MCCB-100",
        fault_id=None,
        doc_signal="VALUE_CONFLICT",
        confirmed_facts="Reported by s/n: ZX-DOC-31 owner.",
        review_task=None,
        missing_info=None,
        not_claimed=None,
        condition_status="UNKNOWN",
        fw_fixed_version=None,
        dept_code="DEPT-DOC",
        reply_type="ACKNOWLEDGED",
        request_version=1,
        has_action_record=True,
    )
    reply = DeptReplyText(
        body="Ask doc.team@example.co.kr; Serial No.: ZX-DOC-32.", body_language="en"
    )
    prepared = build_improvement_reply_input(context, reply, sensitive_values={})
    prompt = render_improvement_prompt(prepared.payload)
    for secret in ("ZX-DOC-31", "doc.team@example.co.kr", "ZX-DOC-32"):
        assert secret not in prompt
    assert "has_action_record" not in prepared.payload.model_dump()
