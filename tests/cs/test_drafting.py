"""KAN-106 C/S 답변 초안 입력·출력·금지 약속·호출 경계 회귀. 네트워크·모델·실제 데이터 없음."""

import copy
import json
from unittest.mock import Mock

import pytest

from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.drafting import (
    DRAFT_INPUT_VERSION,
    DRAFT_INPUT_VERSION_STRIPPED,
    DRAFT_PROMPT,
    DRAFT_VALIDATION_CODES,
    FORBIDDEN_COMMITMENTS,
    FORBIDDEN_REASON,
    EvidenceSnippet,
    ForbiddenCommitmentError,
    build_draft_input,
    find_forbidden_commitment,
    validate_output,
)
from backend.engine.cs.drafting_call import run_draft_attempt
from backend.engine.cs.prompt_registry import EVIDENCE_RULE
from backend.engine.cs.verification import build_verification_input
from backend.shared.ai import Generation, Usage

RECORD = {
    "channel": "PHONE",
    "body_language": "ko",
    "subject": "차단기 트립",
    "body": "Urgency: SAFETY\nserial: ZX-PRIVATE; person@example.test 김고객 300 V에서 트립.",
    "category_code": "GOLD_SECRET",
    "resolution_case": "FUTURE_SECRET",
}
CLASSIFICATION = ClassificationOutput(
    category_code="WARRANTY",
    secondary_category="RETURN",
    urgency_flag="NONE",
    provided_info=["TEST_SERIAL"],
    return_subtype="DEFECT",
)
EVIDENCE = [
    EvidenceSnippet(
        chunk_id="c1",
        snippet="Warranty period is 12 months from delivery.",
        section_path=["Warranty"],
        document_sha256="a" * 64,
    )
]


def prepare(**overrides):
    """합성 설정으로 초안 입력을 만든다.

    Args:
        overrides: 바꿀 키워드 인자.

    Returns:
        PreparedDraftInput: 정제 입력.
    """
    kwargs = {
        "classification": CLASSIFICATION,
        "required_info_codes": frozenset({"TEST_SERIAL", "TEST_PHOTO", "TEST_INVOICE"}),
        "already_asked_codes": frozenset({"TEST_INVOICE"}),
        "evidence": EVIDENCE,
        "sensitive_values": {"김고객": "PERSONAL"},
    } | overrides
    return build_draft_input(RECORD, **kwargs)


def option(label, *, text="보증 기간은 납품일로부터 12개월입니다.", kind="EVIDENCE", ids=("c1",)):
    """선택지 하나를 만든다.

    Args:
        label: 선택지 라벨.
        text: 문장.
        kind: 문장 종류.
        ids: 근거 chunk_id.

    Returns:
        dict: 원시 선택지.
    """
    return {
        "label": label,
        "sentences": [
            {"text": text, "kind": kind, "evidence_chunk_ids": list(ids)},
            {
                "text": "보증 적용 여부는 확인 후 안내드립니다.",
                "kind": "CONDITION",
                "evidence_chunk_ids": [],
            },
        ],
        "info_requests": [{"info_code": "TEST_PHOTO", "question": "트립 표시 사진을 보내 주세요."}],
    }


def raw(*options):
    """세 선택지 원시 JSON을 만든다.

    Args:
        options: 선택지. 없으면 기본 세 개.

    Returns:
        str: 원시 응답.
    """
    return json.dumps(
        {"options": list(options or map(option, ("CONCISE", "DETAILED", "INFO_FIRST")))}
    )


def check(text):
    """기본 입력의 근거·요청 코드로 원시 응답을 검사한다.

    Args:
        text: 원시 응답.

    Returns:
        DraftOutput: 검사 결과.
    """
    return validate_output(
        text, evidence_chunk_ids=frozenset({"c1"}), request_info_codes=frozenset({"TEST_PHOTO"})
    )


@pytest.mark.parametrize(
    ("snippet", "section_path"),
    [
        ("Sensitivity per FLT&#45;042 is 100 mA.", ["Faults"]),
        ("Sensitivity is 100 mA.", ["Faults", "FLT 042 leakage"]),
    ],
)
def test_blocked_code_in_evidence_is_rejected(snippet, section_path):
    """근거 스니펫·절 경로에 차단 코드가 있으면 조용히 빼지 않고 거부한다.

    Args:
        snippet: 근거 스니펫.
        section_path: 근거 절 경로.

    Returns:
        None: CS_BLOCKED_EVIDENCE를 확인한다.
    """
    evidence = [
        EvidenceSnippet(
            chunk_id="c9", snippet=snippet, section_path=section_path, document_sha256="b" * 64
        )
    ]
    with pytest.raises(ValueError, match="^CS_BLOCKED_EVIDENCE$"):
        prepare(evidence=EVIDENCE + evidence)


def test_request_codes_exclude_provided_and_already_asked():
    """요청 코드는 필요 − 제공 − 이미 물은 코드이며 정답·사후 기록·민감값은 빠진다.

    Args:
        없음.

    Returns:
        None: 서버 계산과 마스킹 유지를 확인한다.
    """
    before = copy.deepcopy(RECORD)
    prepared = prepare()
    payload = prepared.payload
    assert payload.request_info_codes == ["TEST_PHOTO"]
    assert payload.provided_info == ["TEST_SERIAL"]
    assert prepared.version == DRAFT_INPUT_VERSION == "cs-draft-input-v2"
    assert prepared.removed_annotations == 0
    dumped = payload.model_dump_json()
    for secret in ("GOLD_SECRET", "FUTURE_SECRET", "ZX-PRIVATE", "person@example.test", "김고객"):
        assert secret not in dumped
    assert "[SERIAL_PROVIDED]" in payload.body and "[PERSONAL_PROVIDED]" in payload.body
    assert payload.body.startswith("Urgency: SAFETY\n") and "300 V에서 트립." in payload.body
    stripped = prepare(strip_phone_annotation=True)
    assert stripped.version == DRAFT_INPUT_VERSION_STRIPPED and stripped.removed_annotations == 1
    assert "SAFETY" not in stripped.payload.body
    assert RECORD == before
    asked_all = prepare(already_asked_codes=frozenset({"TEST_PHOTO", "TEST_INVOICE"}))
    assert asked_all.payload.request_info_codes == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"evidence": EVIDENCE * 2},
        {"required_info_codes": frozenset({""})},
        {"classification": CLASSIFICATION.model_dump()},
        {"required_info_codes": ["TEST_PHOTO"]},
        {"already_asked_codes": None},
    ],
)
def test_invalid_draft_input_is_rejected(overrides):
    """중복 근거 ID·빈 코드·검사 안 된 분류는 안전한 오류다.

    Args:
        overrides: 잘못된 합성 인자.

    Returns:
        None: CS_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        prepare(**overrides)


def test_three_options_with_kinds_and_evidence_pass():
    """세 라벨·문장 종류·근거 ID가 맞으면 통과한다.

    Args:
        없음.

    Returns:
        None: 출력 구조를 확인한다.
    """
    output = check(raw())
    assert [o.label for o in output.options] == ["CONCISE", "DETAILED", "INFO_FIRST"]
    assert [s.kind for s in output.options[0].sentences] == ["EVIDENCE", "CONDITION"]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (raw(option("CONCISE"), option("DETAILED")), "CS_INVALID_OUTPUT"),
        (raw(option("CONCISE"), option("CONCISE"), option("INFO_FIRST")), "CS_DUPLICATE_OPTION"),
        (
            raw(option("CONCISE", ids=()), option("DETAILED"), option("INFO_FIRST")),
            "CS_EVIDENCE_REQUIRED",
        ),
        (
            raw(option("CONCISE", ids=("c9",)), option("DETAILED"), option("INFO_FIRST")),
            "CS_UNKNOWN_REFERENCE",
        ),
        (
            raw(option("CONCISE", ids=("c1", "c1")), option("DETAILED"), option("INFO_FIRST")),
            "CS_DUPLICATE_EVIDENCE",
        ),
        (
            raw(option("CONCISE", kind="GUESS"), option("DETAILED"), option("INFO_FIRST")),
            "CS_INVALID_OUTPUT",
        ),
        (raw().replace("TEST_PHOTO", "TEST_SERIAL", 1), "CS_UNKNOWN_REFERENCE"),
        (
            raw().replace('"label": "CONCISE"', '"label": "CONCISE", "extra": 1'),
            "CS_INVALID_OUTPUT",
        ),
        (raw().replace('{"options": ', '{"options": [], "options": '), "CS_INVALID_OUTPUT"),
        ("not json", "CS_INVALID_OUTPUT"),
        # 내용 규칙 위반과 타입 위반이 섞이면 스키마 위반으로 본다.
        (
            raw(option("CONCISE", ids=()), option("DETAILED", kind="GUESS"), option("INFO_FIRST")),
            "CS_INVALID_OUTPUT",
        ),
    ],
    ids=[
        "two-options",
        "duplicate-label",
        "evidence-without-id",
        "unknown-chunk",
        "duplicate-chunk",
        "unknown-kind",
        "provided-info-code",
        "extra-key",
        "duplicate-json-key",
        "not-json",
        "mixed-schema-and-rule",
    ],
)
def test_invalid_output_is_rejected(text, code):
    """선택지 수·라벨·근거 ID·요청 코드·추가 키 위반을 거부하고, 내용 규칙 위반은 그 코드를 보존한다.

    Args:
        text: 원시 위반.
        code: 기대 오류 코드(파싱·스키마 위반은 CS_INVALID_OUTPUT, 내용 규칙은 DRAFT_VALIDATION_CODES).

    Returns:
        None: 오류 코드를 확인한다.
    """
    assert code == "CS_INVALID_OUTPUT" or code in DRAFT_VALIDATION_CODES
    with pytest.raises(ValueError, match=f"^{code}$"):
        check(text)


def test_repeated_info_request_in_option_is_rejected():
    """한 선택지 안에서 같은 코드를 두 번 묻지 않는다.

    Args:
        없음.

    Returns:
        None: CS_DUPLICATE_INFO를 확인한다.
    """
    doubled = option("INFO_FIRST")
    doubled["info_requests"] *= 2
    with pytest.raises(ValueError, match="^CS_DUPLICATE_INFO$"):
        check(raw(option("CONCISE"), option("DETAILED"), doubled))


def test_info_first_must_ask_every_requested_code():
    """INFO_FIRST는 요청 코드를 전부 묻고, CONCISE·DETAILED는 묻지 않아도 된다.

    Args:
        없음.

    Returns:
        None: 누락은 CS_MISSING_INFO_REQUEST, 부분 집합은 통과함을 확인한다.
    """
    silent = {label: option(label) | {"info_requests": []} for label in ("CONCISE", "DETAILED")}
    assert check(raw(silent["CONCISE"], silent["DETAILED"], option("INFO_FIRST")))
    with pytest.raises(ValueError, match="^CS_MISSING_INFO_REQUEST$"):
        check(
            raw(option("CONCISE"), option("DETAILED"), option("INFO_FIRST") | {"info_requests": []})
        )


# 패턴마다 거부해야 할 확정형 예문. 패턴을 더하거나 빼면 키 집합 검사가 깨진다.
FORBIDDEN_EXAMPLES = {
    FORBIDDEN_COMMITMENTS[0].pattern: [
        "Your RMA has been approved.",
        "RMA issued: please ship.",
        "RMA approved.",
        "Your RMA request has been approved.",
        # 조건 표지가 확정 뒤에 오면 확정이다(KAN-193 약속 패턴 조건 검사).
        "Your RMA has been approved, so do not worry.",
        # 부정·once는 약속 패턴의 표지가 아니다(#466 리뷰 P2).
        "We have not found any issue and your RMA is approved.",
        "Once you reply your RMA is approved.",
    ],
    FORBIDDEN_COMMITMENTS[1].pattern: [
        "Return authorized.",
        "The return is approved.",
        "Don't worry, your return is approved.",
        "The return is approved if you need a label.",
    ],
    FORBIDDEN_COMMITMENTS[2].pattern: [
        "Warranty approved.",
        "Your warranty claim has been approved.",
    ],
    FORBIDDEN_COMMITMENTS[3].pattern: [
        "The unit is covered under warranty.",
        "This will be covered by the warranty.",
        "Your unit is covered under warranty.",
    ],
    FORBIDDEN_COMMITMENTS[4].pattern: ["It's covered under warranty.", "it’s covered by warranty"],
    FORBIDDEN_COMMITMENTS[5].pattern: [
        "We will send a technician tomorrow.",
        "We will dispatch our engineer.",
        "We'll send a technician tomorrow.",
        "We will send a technician once you confirm the address.",
        "Once you reply we will send a technician.",
        "We never delay, so we will send a technician tomorrow.",
    ],
    FORBIDDEN_COMMITMENTS[6].pattern: ["A site visit is scheduled.", "Technician has been booked."],
    FORBIDDEN_COMMITMENTS[7].pattern: [
        "We will replace the breaker free of charge.",
        "We will repair it at no cost.",
        "We’ll replace it free of charge.",
    ],
    FORBIDDEN_COMMITMENTS[8].pattern: ["RMA가 승인되었습니다.", "RMA를 발급해 드리겠습니다."],
    FORBIDDEN_COMMITMENTS[9].pattern: [
        "반품이 승인되었습니다.",
        "반품 승인해 드리겠습니다.",
        "혹시 궁금하실까 봐 말씀드리면 반품이 승인되었습니다.",
    ],
    FORBIDDEN_COMMITMENTS[10].pattern: [
        "내일 방문하겠습니다.",
        "현장에 방문해 드리겠습니다.",
        "내일 방문드리겠습니다.",
    ],
    FORBIDDEN_COMMITMENTS[11].pattern: ["방문 일정이 확정되었습니다.", "방문을 확정했습니다."],
    FORBIDDEN_COMMITMENTS[12].pattern: [
        "보증이 적용됩니다.",
        "보증으로 승인되었습니다.",
        "보증 처리됩니다.",
    ],
    FORBIDDEN_COMMITMENTS[13].pattern: ["무상 처리해 드리겠습니다.", "무상으로 교체됩니다."],
    FORBIDDEN_COMMITMENTS[14].pattern: ["We have approved your RMA.", "We've approved the return."],
    FORBIDDEN_COMMITMENTS[15].pattern: [
        "RMA 승인이 완료되었습니다.",
        "보증 처리가 완료되었습니다.",
    ],
    FORBIDDEN_COMMITMENTS[16].pattern: [
        "기술자를 파견하겠습니다.",
        "엔지니어를 파견해 드리겠습니다.",
    ],
    # KAN-193 반품 사유·제조 책임 확정.
    FORBIDDEN_COMMITMENTS[17].pattern: [
        "This is a manufacturing defect.",
        "The failure has been confirmed as a manufacturing defect.",
        "Your unit was confirmed to be a manufacturing fault.",
        "We have confirmed that it is a manufacturing defect.",
        "We have determined that your unit is a manufacturing defect.",
        "Our team confirms that this is a manufacturing defect.",
        "This is a confirmed manufacturing defect.",
        "It is clearly a manufacturing defect.",
        "We confirm that it's a manufacturing defect.",
        # 조동사가 앞에 있어도 확정이다(#354 리뷰 P2).
        "We would like to inform you that your unit is a manufacturing defect.",
        "We will confirm it is a manufacturing defect.",
        "We should note that this is a manufacturing defect.",
        "You may be glad to know this is a manufacturing defect.",
        # 앞 절·응대 문구의 부정은 확정을 부정하지 않는다(#354 리뷰 P2).
        "Please do not worry, this is a manufacturing defect.",
        "Please don't hesitate to contact us; it is a manufacturing defect.",
        "Once again, we confirm it is a manufacturing defect.",
        "Please do not worry that it is a manufacturing defect.",
        "Once again we confirm it is a manufacturing defect.",
        "There is no doubt that this is a manufacturing defect.",
        "Without a doubt, it is a manufacturing defect.",
        "Without any question this is a manufacturing defect.",
        # no·without이 근거 부재가 아닌 응대 문구일 때(Fable 검토 P1).
        "We will ship without delay since it is a manufacturing defect.",
        "No further action is needed on your side as this is a manufacturing defect.",
        "A replacement will be sent at no cost as this is a manufacturing defect.",
        "Without exception it is a manufacturing defect.",
        "No worries this is a manufacturing defect.",
    ],
    FORBIDDEN_COMMITMENTS[18].pattern: [
        "The return reason has been confirmed.",
        "Return reason is determined.",
        "No charge will apply since the return reason has been confirmed.",
    ],
    FORBIDDEN_COMMITMENTS[19].pattern: [
        "We accept full responsibility for the failure.",
        "We take liability for the damage.",
        "We accept responsibility.",
    ],
    FORBIDDEN_COMMITMENTS[20].pattern: [
        "The damage was determined to be customer misuse.",
        "It is confirmed as improper installation.",
        "We will proceed without further checks because the failure was determined to be customer misuse.",
    ],
    FORBIDDEN_COMMITMENTS[21].pattern: [
        "제조 불량으로 확인되었습니다.",
        "제품 결함으로 판정되었습니다.",
        "검사 결과 불량으로 확정됐습니다.",
        "불량이 확인되었습니다.",
    ],
    FORBIDDEN_COMMITMENTS[22].pattern: [
        "당사 책임으로 확인되었습니다.",
        "저희 책임이 인정됩니다.",
    ],
    FORBIDDEN_COMMITMENTS[23].pattern: [
        "반품 사유가 제조 불량으로 확정되었습니다.",
        "반품 사유는 단순 변심으로 확정했습니다.",
    ],
    FORBIDDEN_COMMITMENTS[24].pattern: [
        "고객 과실로 판정되었습니다.",
        "설치 부주의로 확인되었습니다.",
    ],
    FORBIDDEN_COMMITMENTS[25].pattern: ["제조 불량입니다.", "제품 결함이 맞습니다."],
    FORBIDDEN_COMMITMENTS[26].pattern: [
        "이번 불량은 저희 책임입니다.",
        "제품 고장의 책임은 당사에 있습니다.",
    ],
}
# 조건부·부정 표현은 허용한다(경계 고정).
ALLOWED_EXAMPLES = [
    "승인 여부는 검토 후 안내드리겠습니다.",
    "보증 적용 여부는 시리얼 확인 후 안내드립니다.",
    "무상 처리 가능 여부는 점검 결과에 따라 달라집니다.",
    "방문 일정 확정 여부는 담당 부서에서 회신합니다.",
    "반품 승인 여부는 검토 후 알려 드립니다.",
    "The unit may be eligible for warranty service.",
    "The repair may be covered under warranty after inspection.",
    "RMA approval is subject to review.",
    "We will confirm whether it is covered under warranty.",
    "If the unit is covered under warranty, we will let you know.",
    "The RMA is not approved yet.",
    "반품 승인 완료 여부는 확인 후 안내드립니다.",
    "보증 처리 여부는 점검 후 알려 드립니다.",
    "기술자 파견 여부는 담당 부서에서 결정합니다.",
    "We will check whether your unit is covered under warranty.",
    "We will confirm whether or not it is covered under warranty.",
    "In case it is covered under warranty, we will let you know.",
    # KAN-193: 반품 사유·책임은 검사 뒤 확정한다는 조건부 표현은 허용한다.
    "We will confirm whether it is a manufacturing defect after inspection.",
    "If the failure is a manufacturing defect, it may be covered under warranty.",
    "The return reason will be confirmed after inspection.",
    "It is not a manufacturing defect yet.",
    "Responsibility will be decided after the inspection.",
    "반품 사유는 검사 후 확정됩니다.",
    "제조 불량 여부는 검사 후 확인하겠습니다.",
    "책임 소재는 점검 결과에 따라 판단합니다.",
    "고객 과실 여부는 확인 후 안내드립니다.",
    "불량 여부를 확인하고 있습니다.",
    # 부정·미확정 서술도 허용한다(#354 리뷰 P3).
    "It has not yet been determined to be customer misuse.",
    "It has not been confirmed as improper installation.",
    "We cannot say that it is a manufacturing defect.",
    "We can't confirm that it is a manufacturing defect yet.",
    "We are not sure that the failure is a manufacturing defect.",
    "The return reason is not confirmed yet.",
    "제조 불량으로 확인되지 않았습니다.",
    "아직 고객 과실로 판정되지 않았습니다.",
    # 같은 문장 앞부분의 조건·부정 표지와 한국어 조건 어미(#354 리뷰 P2).
    "If inspection shows the failure is a manufacturing defect, it may be covered.",
    "It is possibly a manufacturing defect, which inspection will show.",
    "제조 불량으로 확인되었다면 보증이 적용될 수 있습니다.",
    "고객 과실로 판정되었는지는 검사 후 안내드립니다.",
    "당사 책임으로 확인될 경우 별도로 안내드립니다.",
    "We take responsibility for following up quickly.",
    "배송 지연은 저희 책임입니다. 사과드립니다.",
    "안내가 늦은 점은 저희 책임입니다.",
    # no·without 부정과 '~다고 볼 수 없습니다'(Fable 검토).
    "There is no evidence that this is a manufacturing defect.",
    "We see no sign that the unit is a manufacturing defect.",
    "현재 자료만으로는 제조 불량으로 확정되었다고 볼 수 없습니다.",
    # 약속 패턴도 같은 절 앞의 조건 표지를 본다(KAN-193, 재측정 CSV1-003715 오탐).
    "Whether the return is approved will be communicated after inspection.",
    "If the return is approved, we will contact you.",
    "If a site visit is scheduled, we will share the details first.",
    "Unless the warranty claim is approved, repair costs may apply.",
    "만약 반품이 승인되었다는 연락을 받으셨다면 알려 주십시오.",
]


def test_forbidden_pattern_table_is_fixed():
    """패턴 목록과 예문 표가 일치하고 각 예문이 자기 패턴에 맞는다.

    Args:
        없음.

    Returns:
        None: 패턴 추가·삭제·완화 시 실패한다.
    """
    assert set(FORBIDDEN_EXAMPLES) == {p.pattern for p in FORBIDDEN_COMMITMENTS}
    assert len(FORBIDDEN_EXAMPLES) == len(FORBIDDEN_COMMITMENTS)
    for pattern in FORBIDDEN_COMMITMENTS:
        for example in FORBIDDEN_EXAMPLES[pattern.pattern]:
            assert pattern.search(example), (pattern.pattern, example)


@pytest.mark.parametrize(
    "text", [example for examples in FORBIDDEN_EXAMPLES.values() for example in examples]
)
def test_forbidden_examples_are_rejected(text):
    """예문 표의 확정형은 문장 단위 예외를 거친 뒤에도 거부된다.

    Args:
        text: 금지 예문.

    Returns:
        None: find_forbidden_commitment가 True인지 확인한다.
    """
    assert find_forbidden_commitment(text)


@pytest.mark.parametrize("text", ALLOWED_EXAMPLES)
def test_conditional_wording_is_allowed(text):
    """조건부 표현은 어느 패턴에도 걸리지 않는다.

    Args:
        text: 허용 예문.

    Returns:
        None: 오탐이 없음을 확인한다.
    """
    assert not find_forbidden_commitment(text)


@pytest.mark.parametrize("text", ["Your RMA has been approved.", "무상 처리해 드리겠습니다."])
def test_forbidden_commitment_rejects_draft(text):
    """확정형 약속이 문장에 있으면 초안 전체를 거부한다.

    Args:
        text: 확정형 문장.

    Returns:
        None: CS_FORBIDDEN_COMMITMENT를 확인한다.
    """
    bad = raw(
        option("CONCISE"),
        option("DETAILED", text=text, kind="UNCONFIRMED", ids=()),
        option("INFO_FIRST"),
    )
    with pytest.raises(ValueError, match="^CS_FORBIDDEN_COMMITMENT$"):
        check(bad)


def test_forbidden_error_points_at_the_sentences_to_rewrite():
    """금지 약속 오류는 초안과 걸린 문장(선택지·순서·이유)을 담는다. 질문에 걸리면 문장이 없다(KAN-193)."""
    bad = raw(
        option("CONCISE"),
        option("DETAILED", text="Your RMA has been approved.", kind="UNCONFIRMED", ids=()),
        option("INFO_FIRST"),
    )
    with pytest.raises(ForbiddenCommitmentError) as caught:
        check(bad)
    assert str(caught.value) == "CS_FORBIDDEN_COMMITMENT"
    [hit] = caught.value.sentences
    assert (hit.label, hit.sentence_index, hit.reason) == ("DETAILED", 0, FORBIDDEN_REASON)
    assert caught.value.draft.options[1].sentences[0].text == "Your RMA has been approved."
    asking = option("INFO_FIRST")
    asking["info_requests"][0]["question"] = "사진을 보내 주시면 내일 방문하겠습니다."
    with pytest.raises(ForbiddenCommitmentError) as caught:
        check(raw(option("CONCISE"), option("DETAILED"), asking))
    assert caught.value.sentences == ()


def test_forbidden_commitment_in_question_rejects_draft():
    """정보 요청 질문에 든 확정형 약속도 거부한다.

    Args:
        없음.

    Returns:
        None: CS_FORBIDDEN_COMMITMENT를 확인한다.
    """
    asking = option("INFO_FIRST")
    asking["info_requests"][0]["question"] = "사진을 보내 주시면 내일 방문하겠습니다."
    with pytest.raises(ValueError, match="^CS_FORBIDDEN_COMMITMENT$"):
        check(raw(option("CONCISE"), option("DETAILED"), asking))


@pytest.mark.parametrize(
    "text",
    ["승인 여부는 검토 후 안내드리겠습니다.", "The unit may be eligible for warranty service."],
)
def test_conditional_commitment_passes_draft(text):
    """조건부 표현 문장은 초안 검사를 통과한다.

    Args:
        text: 조건부 문장.

    Returns:
        None: 유효한 출력을 확인한다.
    """
    ok = raw(
        option("CONCISE"),
        option("DETAILED", text=text, kind="CONDITION", ids=()),
        option("INFO_FIRST"),
    )
    assert check(ok).options[1].sentences[0].text == text


def test_prompt_states_rules_and_schema():
    """프롬프트에 금지 약속 지시와 출력 스키마가 들어 있다.

    Args:
        없음.

    Returns:
        None: 고정 지시문을 확인한다.
    """
    assert "약속하지 않는다" in DRAFT_PROMPT
    assert "request_info_codes" in DRAFT_PROMPT and '"evidence_chunk_ids"' in DRAFT_PROMPT


def attempt(provider, **overrides):
    """합성 설정으로 단일 초안 시도를 실행한다.

    Args:
        provider: 주입할 fake 호출자.
        overrides: 바꿀 키워드 인자.

    Returns:
        DraftAttempt: 외부 호출 없는 시도 결과.
    """
    kwargs = {
        "classification": CLASSIFICATION,
        "required_info_codes": frozenset({"TEST_SERIAL", "TEST_PHOTO"}),
        "already_asked_codes": frozenset(),
        "evidence": EVIDENCE,
        "sensitive_values": {"김고객": "PERSONAL"},
        "provider": provider,
        "attempt_number": 1,
    } | overrides
    return run_draft_attempt(RECORD, **kwargs)


def test_fake_provider_round_trip_keeps_usage_and_masked_prompt():
    """정제 입력만 담은 프롬프트를 한 번 보내고 결과·사용량·모델을 기록한다.

    Args:
        없음.

    Returns:
        None: 왕복과 KAN-87 사용량 필드 보존을 확인한다.
    """
    usage = Usage(10, 3, None, 0, 13, {"promptTokenCount": 10})
    provider = Mock(
        return_value=Generation(raw(), usage, "fake-v1", ["STOP"], model="fake", attempts=1)
    )
    result = attempt(provider)
    provider.assert_called_once()
    prompt = provider.call_args.args[0]
    assert prompt.startswith(DRAFT_PROMPT)
    for secret in ("GOLD_SECRET", "FUTURE_SECRET", "ZX-PRIVATE", "person@example.test", "김고객"):
        assert secret not in prompt
    assert result.outcome == "VALID" and result.prediction is not None
    assert result.usage == usage and result.usage.billable_input_tokens == 10
    assert result.requested_model == "fake" and result.call_attempts == 1
    assert result.model_version == "fake-v1" and result.finish_reasons == ("STOP",)
    assert result.input_sha256 and len(result.input_sha256) == 64
    assert result.input_version == DRAFT_INPUT_VERSION and result.removed_annotations == 0
    stripped = attempt(Mock(return_value=provider.return_value), strip_phone_annotation=True)
    assert (
        stripped.input_version == DRAFT_INPUT_VERSION_STRIPPED and stripped.removed_annotations == 1
    )
    assert "raw_response=" not in repr(result)


def test_fake_provider_gets_the_rule_and_a_greeting_stays_out_of_verification():
    """공급자가 받는 지시문에 문서 사실 규칙이 있고, CONDITION 인사 문장은 검증 대상이 아니다."""
    draft = json.loads(raw())
    greeting = "We have received your inquiry. Thank you for contacting us."
    for option in draft["options"]:
        option["sentences"].insert(
            0, {"text": greeting, "kind": "CONDITION", "evidence_chunk_ids": []}
        )
    usage = Usage(10, 3, None, 0, 13, {})
    generation = Generation(json.dumps(draft), usage, "fake-v1", ["STOP"], model="fake", attempts=1)
    provider = Mock(return_value=generation)
    result = attempt(provider)
    assert EVIDENCE_RULE in provider.call_args.args[0]
    assert result.outcome == "VALID" and result.prediction is not None
    payload = build_verification_input(result.prediction, EVIDENCE)
    assert payload.sentences and all(s.text != greeting for s in payload.sentences)


@pytest.mark.parametrize(
    "text,code",
    [("not json", "CS_INVALID_OUTPUT"), (None, "CS_FORBIDDEN_COMMITMENT")],
)
def test_schema_and_commitment_violations_keep_raw_and_usage(text, code):
    """스키마 위반과 금지 약속은 원시 응답·사용량을 남기고 재시도하지 않는다.

    Args:
        text: 원시 응답. None이면 금지 약속이 든 초안.
        code: 기대 오류 코드.

    Returns:
        None: SCHEMA_VIOLATION과 오류 코드 구분을 확인한다.
    """
    if text is None:
        text = raw(
            option("CONCISE", text="RMA가 승인되었습니다.", kind="UNCONFIRMED", ids=()),
            option("DETAILED"),
            option("INFO_FIRST"),
        )
    provider = Mock(return_value=Generation(text, Usage(4, 2), "fake-v1", ["STOP"]))
    result = attempt(provider)
    provider.assert_called_once()
    assert result.outcome == "SCHEMA_VIOLATION" and result.error_code == code
    assert result.raw_response == text and result.prediction is None
    assert result.usage == Usage(4, 2)
    # 금지 약속만 재작성 피드백을 만든다(KAN-193). 형식 위반은 고칠 초안이 없다.
    feedback = result.forbidden_revision()
    if code == "CS_FORBIDDEN_COMMITMENT":
        assert [(f.label, f.sentence_index) for f in feedback.failed_sentences] == [("CONCISE", 0)]
        assert "RMA가 승인되었습니다." not in repr(result)
    else:
        assert feedback is None


def test_timeout_and_rejected_input():
    """타임아웃은 불확실 상태로, 입력 위반은 호출 없이 보류로 남는다.

    Args:
        없음.

    Returns:
        None: TIMEOUT·HELD 기록을 확인한다.
    """
    timed_out = attempt(Mock(side_effect=TimeoutError("PRIVATE_TOKEN")))
    assert timed_out.outcome == "TIMEOUT" and timed_out.error_code == "CS_PROVIDER_TIMEOUT"
    assert timed_out.execution_uncertain and "PRIVATE_TOKEN" not in repr(timed_out)
    provider = Mock()
    held = attempt(provider, evidence=EVIDENCE * 2)
    provider.assert_not_called()
    assert held.outcome == "HELD" and held.error_code == "CS_INVALID_INPUT"
    assert not held.call_started
    # frozenset이 아닌 코드 집합도 예외로 새지 않고 보류로 남는다.
    typed = attempt(provider, required_info_codes=None)
    provider.assert_not_called()
    assert typed.outcome == "HELD" and typed.error_code == "CS_INVALID_INPUT"
    with pytest.raises(ValueError, match="^CS_INVALID_ATTEMPT_NUMBER$"):
        attempt(provider, attempt_number=0)


def test_blocked_code_in_draft_output_is_rejected():
    """초안 문장·질문이 차단 코드를 말하면 최종 응답 경계에서 거부한다.

    Args:
        없음.

    Returns:
        None: CS_BLOCKED_EVIDENCE를 확인한다.
    """
    text = raw(
        option("CONCISE", text="Per FLT 042, set 100 mA.", kind="UNCONFIRMED", ids=()),
        option("DETAILED"),
        option("INFO_FIRST"),
    )
    with pytest.raises(ValueError, match="^CS_BLOCKED_EVIDENCE$"):
        check(text)


def test_blocked_evidence_input_failure_keeps_its_code():
    """차단 근거로 입력을 만들지 못한 시도는 호출 없이 CS_BLOCKED_EVIDENCE로 남는다.

    Args:
        없음.

    Returns:
        None: 시도 기록의 오류 코드를 확인한다.
    """
    provider = Mock()
    blocked = EvidenceSnippet(
        chunk_id="c9",
        snippet="Sensitivity per FLT-042.",
        section_path=["Faults"],
        document_sha256="b" * 64,
    )
    result = attempt(provider, evidence=EVIDENCE + [blocked])
    provider.assert_not_called()
    assert result.error_code == "CS_BLOCKED_EVIDENCE" and not result.call_started
