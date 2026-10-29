"""KAN-142 메일 분류 채점. 합성 결과만 쓰고 정답 파일·모델·DB에 접근하지 않는다."""

import pytest

from backend.engine.mail_triage.triage import Evidence, TriageResult
from eval.mail_metrics import MailGold, summarize_triage


def result(mail_type, link, *, review=False, evidence=()):
    """분류 결과를 만든다.

    Args:
        mail_type: 메일 유형.
        link: 연결 추천.
        review: 검토 필요.
        evidence: 근거.

    Returns:
        TriageResult: 합성 결과.
    """
    return TriageResult(
        mail_type=mail_type,
        needs_review=review,
        attachments_pending=False,
        evidence=tuple(evidence),
        link_suggestion=link,
    )


GOLD = {
    "m1": MailGold("NEW_RFP", "NEW_PROJECT", False),
    "m2": MailGold("CS", "NEW_CS_TICKET", True),
    "m3": MailGold("EXISTING_CHANGE", "EXISTING_THREAD", False),
    "m4": MailGold("OTHER", None, None),
    "m5": MailGold("CANDIDATE_REPLY", "EXISTING_THREAD", True),
}
RESULTS = {
    "m1": result("NEW_RFP", "NEW_PROJECT", evidence=[Evidence(source="subject", start=0, end=3)]),
    "m2": result("CS", "EXISTING_THREAD"),
    "m3": result(
        "CANDIDATE_REPLY",
        "EXISTING_THREAD",
        review=True,
        evidence=[Evidence(source="reply_header")],
    ),
    "m4": None,
    # 검증을 거치지 않은 결과도 들어올 수 있으므로 형식 규칙을 다시 본다.
    "m5": TriageResult.model_construct(
        mail_type="CANDIDATE_REPLY",
        needs_review=True,
        attachments_pending=False,
        evidence=(Evidence.model_construct(source="body", start=5, end=2),),
        link_suggestion="EXISTING_THREAD",
    ),
}


def test_triage_metrics_match_hand_calculation():
    """5클래스 혼동·실패 NO_PREDICTION·연결 추천·근거 유효율·검토 과잉/누락.

    Args:
        없음.

    Returns:
        None: 손계산 값을 확인한다.
    """
    summary = summarize_triage(GOLD, RESULTS)
    assert (summary["total"], summary["failed"]) == (5, 1)
    assert summary["accuracy"] == pytest.approx(3 / 5)
    assert summary["confusion_matrix"]["EXISTING_CHANGE"]["CANDIDATE_REPLY"] == 1
    assert summary["confusion_matrix"]["OTHER"]["NO_PREDICTION"] == 1
    assert summary["per_class"]["CANDIDATE_REPLY"]["f1"] == pytest.approx(2 / 3)
    assert summary["macro_f1"] == pytest.approx(8 / 15)
    assert summary["link_suggestion"] == pytest.approx(
        {"exact_rate": 3 / 5, "exact_rate_when_type_correct": 2 / 3}
    )
    assert summary["evidence_valid_rate"] == pytest.approx(3 / 4)
    assert summary["needs_review"] == pytest.approx(
        {"rate": 0.5, "compared": 4, "over_hold": 1, "missed": 1}
    )
    rows = {row["mail_id"]: row for row in summary["per_mail"]}
    assert rows["m4"]["prediction"] is None and rows["m4"]["needs_review"] is None


def test_triage_rejects_mismatched_ids():
    """정답과 결과 ID 집합이 다르면 거부한다.

    Args:
        없음.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        summarize_triage(GOLD, {k: v for k, v in RESULTS.items() if k != "m4"})
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        summarize_triage(GOLD, RESULTS | {"m4": "NEW_RFP"})
