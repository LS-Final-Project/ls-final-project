"""KAN-115 개선요청서·부서 질문 초안 입력·출력 회귀. 네트워크·모델·실제 데이터 없음."""

import json
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from annotated_types import MaxLen

from backend.api.cs.improvements import schemas
from backend.engine.cs.improvement_drafting import (
    IMPROVEMENT_DRAFT_INPUT_VERSION,
    MAX_PROPOSED_DEPTS,
    TEXT_MAX_LENGTH,
    ConditionSummary,
    EvidenceCase,
    EvidenceTicket,
    ImprovementDraftContext,
    build_improvement_draft_input,
    render_prompt,
    to_content_fields,
    validate_output,
)
from backend.shared.departments import TRANSFER_DEPARTMENTS

AS_OF = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
TICKET = EvidenceTicket(
    ticket_ref="T-1",
    received_at=AS_OF - timedelta(days=3),
    product_code="SYN-MCCB-100",
    fw_version_reported="2.0.1",
    error_code_reported="E12",
    summary="Trips at 300 V. serial: ZX-PRIVATE, contact person@example.test 김고객",
)
CASE = EvidenceCase(
    case_ref="C-1",
    known_at=AS_OF - timedelta(days=1),
    fault_id="FLT-017",
    fw_version_at_issue="2.0.1",
    root_cause="Trip curve setting too low",
    root_cause_type="SETTING",
    action_taken="Reset the curve at site for 김고객",
    fw_fixed_version=None,
    resolved_by_dept="TS_LV",
)
FAULT = ImprovementDraftContext(
    request_type="FAULT",
    product_code="SYN-MCCB-100",
    fault_id="FLT-017",
    doc_signal=None,
    tickets=[TICKET],
    cases=[CASE],
    condition=ConditionSummary(condition_status="UNKNOWN", summary="Voltage differs."),
    candidate_depts=["TS_LV", "RND_FW"],
    as_of=AS_OF,
)
DOCUMENT = FAULT.model_copy(
    update={
        "request_type": "DOCUMENT",
        "fault_id": None,
        "doc_signal": "VALUE_CONFLICT",
        "cases": [],
    }
)
SENSITIVE = {"김고객": "PERSONAL"}


def draft(**update):
    """정상 고장 초안 원시 JSON을 만든다.

    Args:
        update: 바꿀 최상위 키.

    Returns:
        str: 원시 응답.
    """
    body = {
        "problem_statement": "Check why units trip at 300 V on FW 2.0.1.",
        "confirmed_facts": [{"text": "A unit tripped at 300 V.", "evidence_refs": ["T-1"]}],
        "inconsistencies": [
            {
                "text": "The case reports a setting cause on the same FW.",
                "evidence_refs": ["T-1", "C-1"],
            }
        ],
        "missing_info": ["Ambient temperature at trip."],
        "not_claimed": ["Do not conclude a hardware defect."],
        "review_task": "Review the default trip curve.",
        "proposed_depts": ["TS_LV"],
        "dept_questions": [{"dept_code": "TS_LV", "question": "Is the default curve 0.8 In?"}],
    }
    return json.dumps(body | update)


def check(raw, context=FAULT):
    """원시 응답을 검사한다.

    Args:
        raw: 원시 응답.
        context: 맥락.

    Returns:
        ImprovementDraft: 검사 결과.
    """
    return validate_output(raw, context=context)


def test_fault_input_masks_free_text_and_uses_iso_times():
    """문의 요약·사례 서술의 민감값을 가리고 시각을 ISO 문자열로 넘긴다.

    Args:
        없음.

    Returns:
        None: 원값 비노출·판본·프롬프트를 확인한다.
    """
    prepared = build_improvement_draft_input(FAULT, sensitive_values=SENSITIVE)
    assert prepared.version == IMPROVEMENT_DRAFT_INPUT_VERSION and prepared.removed_annotations == 0
    payload = prepared.payload
    dumped = render_prompt(payload)
    for secret in ("ZX-PRIVATE", "person@example.test", "김고객"):
        assert secret not in dumped
    assert "[SERIAL_PROVIDED]" in payload.tickets[0].summary
    assert "[PERSONAL_PROVIDED]" in payload.cases[0].action_taken
    assert payload.tickets[0].received_at == TICKET.received_at.isoformat()
    assert payload.as_of == AS_OF.isoformat() and payload.condition_status == "UNKNOWN"
    assert "Trips at 300 V." in payload.tickets[0].summary


@pytest.mark.parametrize(
    "update",
    [
        {"cases": [CASE.model_copy(update={"known_at": AS_OF + timedelta(seconds=1)})]},
        {"tickets": [TICKET.model_copy(update={"received_at": AS_OF + timedelta(days=1)})]},
        {"tickets": [TICKET.model_copy(update={"product_code": "OTHER"})]},
        {"cases": [CASE.model_copy(update={"fault_id": "FLT-018"})]},
        {"fault_id": None},
        {"doc_signal": "NOT_FOUND"},
        {"cases": [CASE.model_copy(update={"case_ref": "T-1"})]},
        {"candidate_depts": ["TS_LV", "TS_LV"]},
        {"candidate_depts": ["GCS_L1"]},
    ],
    ids=[
        "future-case",
        "future-ticket",
        "product-mismatch",
        "fault-mismatch",
        "fault-without-id",
        "fault-with-signal",
        "duplicate-ref",
        "duplicate-dept",
        "unknown-dept",
    ],
)
def test_invalid_fault_context_is_rejected(update):
    """시점·제품·고장·유형 필수 값·참조·부서 규칙 위반을 거부한다.

    Args:
        update: 잘못된 맥락 변경.

    Returns:
        None: CS_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        build_improvement_draft_input(FAULT.model_copy(update=update), sensitive_values={})


@pytest.mark.parametrize(
    "update", [{"cases": [CASE]}, {"doc_signal": None}], ids=["document-with-case", "no-signal"]
)
def test_invalid_document_context_is_rejected(update):
    """문서 요청은 신호가 필요하고 사례를 붙이지 않는다.

    Args:
        update: 잘못된 맥락 변경.

    Returns:
        None: CS_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        build_improvement_draft_input(DOCUMENT.model_copy(update=update), sensitive_values={})


def test_valid_fault_and_document_drafts_pass():
    """고장 초안과, 부서·not_claimed가 비어도 되는 문서 초안이 통과한다.

    Args:
        없음.

    Returns:
        None: 정상 출력을 확인한다.
    """
    assert check(draft()).proposed_depts == ["TS_LV"]
    build_improvement_draft_input(DOCUMENT, sensitive_values={})
    document = draft(
        inconsistencies=[],
        not_claimed=[],
        proposed_depts=[],
        dept_questions=[],
    )
    assert check(document, DOCUMENT).proposed_depts == []


@pytest.mark.parametrize(
    "raw",
    [
        draft(confirmed_facts=[{"text": "x", "evidence_refs": ["T-9"]}]),
        draft(confirmed_facts=[{"text": "x", "evidence_refs": []}]),
        draft(confirmed_facts=[{"text": "x", "evidence_refs": ["T-1", "T-1"]}]),
        draft(proposed_depts=["QA_CLAIM"], dept_questions=[]),
        draft(proposed_depts=["TS_LV", "TS_LV"]),
        draft(proposed_depts=[], dept_questions=[]),
        draft(not_claimed=[]),
        draft(dept_questions=[{"dept_code": "RND_FW", "question": "FW change?"}]),
        draft(
            dept_questions=[
                {"dept_code": "TS_LV", "question": "a?"},
                {"dept_code": "TS_LV", "question": "b?"},
            ]
        ),
        draft(problem_statement="Two\nsentences."),
        draft(extra="x"),
        draft().replace('{"problem_statement"', '{"review_task": "a", "problem_statement"'),
    ],
    ids=[
        "unknown-ref",
        "fact-without-ref",
        "duplicate-ref",
        "unknown-dept",
        "duplicate-dept",
        "fault-without-dept",
        "fault-without-not-claimed",
        "question-for-unproposed-dept",
        "two-questions-one-dept",
        "multiline-problem",
        "extra-key",
        "duplicate-json-key",
    ],
)
def test_invalid_output_is_rejected(raw):
    """참조·부서·유형별 필수·형식 위반을 거부한다.

    Args:
        raw: 원시 위반.

    Returns:
        None: CS_INVALID_OUTPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_OUTPUT$"):
        check(raw)


@pytest.mark.parametrize(
    "raw",
    [
        draft(review_task="RMA가 승인되었습니다."),
        draft(
            dept_questions=[
                {"dept_code": "TS_LV", "question": "We will send a technician tomorrow?"}
            ]
        ),
        draft(missing_info=["무상 처리해 드리겠습니다."]),
    ],
    ids=["review-task", "question", "missing-info"],
)
def test_forbidden_commitment_in_any_text_is_rejected(raw):
    """어느 칸이든 확정 약속 문구가 있으면 거부한다.

    Args:
        raw: 확정 약속이 든 초안.

    Returns:
        None: CS_FORBIDDEN_COMMITMENT를 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_FORBIDDEN_COMMITMENT$"):
        check(raw)


def test_identifier_free_text_fields_are_masked_too():
    """판본·오류 코드·처리 부서 칸에 섞인 민감값도 가린다.

    Args:
        없음.

    Returns:
        None: 프롬프트에 원값이 없음을 확인한다.
    """
    leaky_ticket = TICKET.model_copy(
        update={"fw_version_reported": "2.0 serial: ZX-FW", "error_code_reported": "E1 김고객"}
    )
    leaky_case = CASE.model_copy(
        update={
            "fw_version_at_issue": "a@example.test",
            "fw_fixed_version": "S/N: ZX-FIX",
            "resolved_by_dept": "TS_LV 김고객",
        }
    )
    context = FAULT.model_copy(update={"tickets": [leaky_ticket], "cases": [leaky_case]})
    prompt = render_prompt(
        build_improvement_draft_input(context, sensitive_values=SENSITIVE).payload
    )
    for secret in ("ZX-FW", "ZX-FIX", "a@example.test", "김고객"):
        assert secret not in prompt


def test_output_limits_match_request_schema():
    """상한 상수가 요청서 스키마 제약과 같고, 넘는 출력은 거부한다.

    Args:
        없음.

    Returns:
        None: 값 일치와 이어 붙인 길이·부서 수 초과 거부를 확인한다.
    """
    [text_limit] = [m for m in get_args(schemas.Text)[1].metadata if isinstance(m, MaxLen)]
    [dept_limit] = schemas.Content.model_fields["proposed_depts"].metadata
    assert (text_limit.max_length, dept_limit.max_length) == (TEXT_MAX_LENGTH, MAX_PROPOSED_DEPTS)
    half = "a" * (TEXT_MAX_LENGTH // 2)
    with pytest.raises(ValueError, match="^CS_INVALID_OUTPUT$"):
        check(draft(missing_info=[half, half]))  # 줄바꿈 1자가 더해져 상한을 넘는다
    assert check(draft(missing_info=[half[1:], half]))
    # 부서 코드가 12개뿐이고 후보·제안 중복을 막으므로 부서 수 상한은 지금 넘을 수 없다.
    assert len(TRANSFER_DEPARTMENTS) <= MAX_PROPOSED_DEPTS


def test_content_fields_round_trip_into_request_schema():
    """평탄화한 칸이 요청서 Content 스키마를 통과하고 근거 참조는 따로 나온다.

    Args:
        없음.

    Returns:
        None: 줄바꿈 연결·빈 목록 None·참조 순서를 확인한다.
    """
    fields = to_content_fields(check(draft(missing_info=["a", "b"])))
    content = schemas.Content.model_validate(fields["content"])
    assert content.missing_info == "a\nb"
    assert content.proposed_depts == ["TS_LV"]
    assert fields["evidence_refs"] == ["T-1", "C-1"]
    assert set(fields["content"]) <= set(schemas.CONTENT_FIELDS)
    empty = to_content_fields(check(draft(inconsistencies=[], missing_info=[])))
    assert empty["content"]["inconsistencies"] is None and empty["content"]["missing_info"] is None
