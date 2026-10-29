"""KAN-118 개선요청 부서 회신 해석 입력·출력·후속 행동 회귀. 네트워크·모델·실제 데이터 없음."""

import json
from typing import get_args

import pytest

from backend.api.cs.improvements import schemas
from backend.engine.cs import improvement_reply as engine
from backend.engine.cs.improvement_reply import (
    IMPROVEMENT_REPLY_INPUT_VERSION,
    DeptReplyText,
    FollowupPlan,
    ImprovementContext,
    build_improvement_reply_input,
    plan_followup,
    render_prompt,
    validate_output,
)

CONTEXT = ImprovementContext(
    request_type="FAULT",
    product_code="SYN-MCCB-100",
    fault_id="FLT-017",
    doc_signal=None,
    confirmed_facts="Trips at 300 V on 3 units.",
    review_task="Check the trip curve setting.",
    missing_info=None,
    not_claimed="Do not conclude a hardware defect.",
    condition_status="UNKNOWN",
    fw_fixed_version="2.1.0",
    dept_code="DEPT-QA",
    reply_type="ADOPTED",
    request_version=2,
    has_action_record=False,
)


def ctx(**update):
    """기본 맥락에서 필드를 바꾼다.

    Args:
        update: 바꿀 필드.

    Returns:
        ImprovementContext: 맥락.
    """
    return CONTEXT.model_copy(update=update)


def claim(kind, text="Trip curve was corrected.", *, fw=None, confirm=None):
    """원시 주장 하나를 만든다.

    Args:
        kind: 주장 종류.
        text: 주장 문장.
        fw: FW_VERSION 판본.
        confirm: 확인 필요 여부. None이면 FIX·SCOPE·FW_VERSION만 True.

    Returns:
        dict: 원시 주장.
    """
    needs = kind in {"FIX", "SCOPE", "FW_VERSION"} if confirm is None else confirm
    return {
        "kind": kind,
        "text": text,
        "certainty": "STATED",
        "needs_confirmation": needs,
        "confirmation_reason": "No action record yet." if needs else None,
        "fw_version": fw,
    }


def raw(
    claims=(),
    *,
    completed=False,
    task="ADDRESSED",
    questions=(),
    evidence=(),
    conflicts=(),
):
    """원시 해석 JSON을 만든다.

    Args:
        claims: 원시 주장 목록.
        completed: completion_claimed.
        task: answers_review_task.
        questions: (주제, 질문) 목록.
        evidence: (종류, 설명) 목록.
        conflicts: 요청서와 어긋나는 주장 요약.

    Returns:
        str: 원시 응답.
    """
    return json.dumps(
        {
            "claims": list(claims),
            "completion_claimed": completed,
            "answers_review_task": task,
            "remaining_questions": [{"topic": t, "question": q} for t, q in questions],
            "evidence_requests": [{"kind": k, "description": d} for k, d in evidence],
            "conflicts_with_request": list(conflicts),
        }
    )


def test_vocabularies_match_the_api_schemas():
    """엔진 어휘가 improvements API 스키마와 같다.

    Args:
        없음.

    Returns:
        None: get_args 비교로 어긋남을 막는다.
    """
    for name in ("RequestType", "DocSignal", "ConditionStatus", "NextTask"):
        assert get_args(getattr(engine, name)) == get_args(getattr(schemas, name))
    assert get_args(engine.ReplyType) == get_args(
        schemas.DeptReply.model_fields["reply_type"].annotation
    )


def test_masking_hides_contacts_and_serials_and_omits_action_record():
    """이메일·라벨 시리얼·민감값이 프롬프트에 없고 조치 기록 유무는 모델 입력에서 빠진다.

    Args:
        없음.

    Returns:
        None: 정제 입력을 확인한다.
    """
    body = "QA here, qa.lead@example.test (박담당). serial: ZX-QA-77 fixed by 2.1.0."
    prepared = build_improvement_reply_input(
        CONTEXT,
        DeptReplyText(body=body, body_language="en"),
        sensitive_values={"박담당": "PERSONAL"},
    )
    assert prepared.version == IMPROVEMENT_REPLY_INPUT_VERSION and prepared.removed_annotations == 0
    prompt = render_prompt(prepared.payload)
    for secret in ("qa.lead@example.test", "박담당", "ZX-QA-77"):
        assert secret not in prompt
    assert "fixed by 2.1.0." in prepared.payload.body
    assert "has_action_record" not in prepared.payload.model_dump()
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        build_improvement_reply_input(
            CONTEXT, DeptReplyText(body=body, body_language="en"), sensitive_values={"x": "SECRET"}
        )


def test_adopted_with_action_record_is_a_followup_candidate():
    """채택과 조치 기록이 있으면 PLAN_FOLLOWUP 후보이며 조치 주장은 여전히 확인 목록에 남는다.

    Args:
        없음.

    Returns:
        None: 후속 계획을 확인한다.
    """
    context = ctx(has_action_record=True)
    output = validate_output(raw([claim("FIX"), claim("CAUSE", "Wrong curve.")]), context=context)
    plan = plan_followup(context, output)
    assert (plan.next_task, plan.actor) == ("PLAN_FOLLOWUP", "CS_OWNER")
    assert [c.kind for c in plan.confirmations] == ["FIX"]


@pytest.mark.parametrize("kind", ["FIX", "SCOPE", "FW_VERSION"])
def test_fix_scope_and_version_claims_must_need_confirmation(kind):
    """조치·범위·판본 주장은 근거가 없으므로 needs_confirmation=false를 거부한다.

    Args:
        kind: 주장 종류.

    Returns:
        None: CS_INVALID_OUTPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_OUTPUT$"):
        validate_output(
            raw([claim(kind, fw="2.1.0" if kind == "FW_VERSION" else None, confirm=False)]),
            context=CONTEXT,
        )


@pytest.mark.parametrize(
    ("text", "context"),
    [
        (raw([claim("FIX")]), ctx(reply_type="REJECTED")),
        (raw([claim("FW_VERSION", fw="2.1.0")]), ctx(reply_type="REJECTED")),
        (raw(), ctx(reply_type="INFO_REQUESTED")),
        (raw([claim("FW_VERSION", fw="2.2.0")]), CONTEXT),
        (raw([claim("FW_VERSION", fw="2.2.0")], conflicts=["Version differs."]), CONTEXT),
        (raw(task="NO_TASK"), CONTEXT),
        (raw(task="ADDRESSED"), ctx(review_task=None)),
        (raw([claim("FW_VERSION")]), CONTEXT),
        (raw([claim("CAUSE", fw="2.1.0")]), CONTEXT),
        (raw([{**claim("FIX"), "confirmation_reason": None}]), CONTEXT),
        (raw(questions=[("q", "?")] * 11), ctx(reply_type="INFO_REQUESTED")),
        (raw()[:-1] + ', "claims": []}', CONTEXT),
    ],
)
def test_context_violations_are_rejected(text, context):
    """반려의 조치 주장, 질문 없는 추가 정보 요청, 판본 충돌 누락, 검토 과제 불일치 등을 거부한다.

    Args:
        text: 원시 응답.
        context: 개선요청 맥락.

    Returns:
        None: CS_INVALID_OUTPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_OUTPUT$"):
        validate_output(text, context=context)


def test_version_conflict_recorded_with_the_version_passes():
    """요청서와 다른 판본 주장은 conflicts에 그 판본을 적으면 통과하고, 같은 판본은 충돌이 아니다.

    Args:
        없음.

    Returns:
        None: 검사 결과를 확인한다.
    """
    conflicts = ["Dept says fixed in v2.2.0, request says 2.1.0."]
    output = validate_output(
        raw([claim("FW_VERSION", fw="2.2.0")], conflicts=conflicts), context=CONTEXT
    )
    assert output.conflicts_with_request == conflicts
    assert validate_output(raw([claim("FW_VERSION", fw=" 2.1.0 ")]), context=CONTEXT).claims


def test_completion_claim_does_not_make_the_request_complete():
    """부서가 완료라고 해도 완료 사실만 전달하고 상태·판본·회차·완료 필드를 만들지 않는다.

    Args:
        없음.

    Returns:
        None: 후속 계획 필드를 확인한다.
    """
    output = validate_output(raw([claim("NO_ISSUE", "Resolved.")], completed=True), context=CONTEXT)
    plan = plan_followup(CONTEXT, output)
    assert plan.completion_claimed and plan.next_task == "RECORD_ACTION"
    assert set(FollowupPlan.model_fields) == {
        "next_task",
        "actor",
        "dept_code",
        "completion_claimed",
        "confirmations",
        "remaining_questions",
        "evidence_requests",
        "conflicts_with_request",
    }


def test_forbidden_commitments_in_questions_are_rejected():
    """질문에 승인·확정 약속이 있으면 거부한다.

    Args:
        없음.

    Returns:
        None: CS_FORBIDDEN_COMMITMENT를 확인한다.
    """
    text = raw(questions=[("RMA", "Your RMA is approved, can we ship?")])
    with pytest.raises(ValueError, match="^CS_FORBIDDEN_COMMITMENT$"):
        validate_output(text, context=ctx(reply_type="INFO_REQUESTED"))


@pytest.mark.parametrize(
    ("reply_type", "has_action", "claims", "expected"),
    [
        ("REJECTED", False, [claim("CAUSE", "Out of scope.")], ("REVIEW_REJECTION", "CS_OWNER")),
        ("INFO_REQUESTED", True, [], ("SUPPLEMENT", "CS_OWNER")),
        ("ADOPTED", False, [claim("FIX")], ("RECORD_ACTION", "DEPARTMENT")),
        ("ADOPTED", False, [], ("RECORD_ACTION", "DEPARTMENT")),
        ("ADOPTED", True, [claim("SCOPE", "All lots.")], ("PLAN_FOLLOWUP", "CS_OWNER")),
        ("ACKNOWLEDGED", True, [], ("REPLY", "DEPARTMENT")),
    ],
)
def test_plan_followup_next_task(reply_type, has_action, claims, expected):
    """부서가 고른 reply_type과 조치 기록 유무로 다음 할 일을 고른다.

    Args:
        reply_type: 부서 회신 유형.
        has_action: 조치 기록 유무.
        claims: 원시 주장.
        expected: (next_task, actor).

    Returns:
        None: 후속 계획을 확인한다.
    """
    context = ctx(reply_type=reply_type, has_action_record=has_action)
    questions = [("lot", "Which lots were affected?")]
    output = validate_output(raw(claims, questions=questions), context=context)
    plan = plan_followup(context, output)
    assert (plan.next_task, plan.actor) == expected and plan.dept_code == "DEPT-QA"


def test_plan_followup_rejects_interpretation_from_another_context():
    """다른 맥락으로 검사한 해석(채택에서 받은 조치 주장)을 반려 맥락에 쓰면 거부한다.

    Args:
        없음.

    Returns:
        None: CS_INVALID_INPUT을 확인한다.
    """
    output = validate_output(raw([claim("FIX")]), context=CONTEXT)
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        plan_followup(ctx(reply_type="REJECTED"), output)
