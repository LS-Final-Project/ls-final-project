"""KAN-186 R&D 확인 흐름(RFP API 명세 v1.3 §5.2·§5.3). 순수 함수, DB·HTTP 없음."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from backend.api.tft.rfp.comparison import compare, load_own_products, master_filter
from backend.api.tft.rfp.rnd import (
    RndQuestion,
    check_reply_fields,
    check_selection,
    rnd_check_of,
    rnd_question_for,
)
from backend.api.tft.rfp.supplement import options_for
from backend.shared.errors import ApplicationError
from tests.tft.test_rfp_comparison import DEMO_A

T0 = datetime(2026, 10, 30, 9, tzinfo=UTC)
KINDS = {"RND_FW": "RND", "TS_TRF": "TECH", "SALES_OPS": "SALES"}


def question(minutes, replied=False, feasibility=None, schedule_fits=None):
    return RndQuestion(
        question_id=uuid4(),
        created_at=T0 + timedelta(minutes=minutes),
        replied=replied,
        feasibility=feasibility,
        schedule_fits=schedule_fits,
    )


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (("FEASIBLE", None), "CONFIRMED_FEASIBLE"),
        (("NOT_FEASIBLE", None), "CONFIRMED_NOT_FEASIBLE"),
        (("FEASIBLE_WITH_SCHEDULE", False), "CONFIRMED_SCHEDULE_MISMATCH"),
        (("FEASIBLE_WITH_SCHEDULE", True), "CONFIRMED_FEASIBLE"),
        (("FEASIBLE_WITH_SCHEDULE", None), "PENDING"),
        (("UNDETERMINED", None), "PENDING"),
    ],
)
def test_the_latest_rnd_answer_decides_the_check(answer, expected):
    old = question(0, replied=True, feasibility="NOT_FEASIBLE")
    latest = question(10, replied=True, feasibility=answer[0], schedule_fits=answer[1])
    assert rnd_check_of([old, latest]) == (expected, latest.question_id)


def test_no_question_open_question_and_waiver():
    assert rnd_check_of([]) == ("NOT_REQUIRED", None)
    answered = question(0, replied=True, feasibility="FEASIBLE")
    open_one = question(5)
    assert rnd_check_of([answered, open_one]) == ("PENDING", open_one.question_id)
    # Waived after the last question: WAIVED. A new question after the waiver reopens it.
    assert rnd_check_of([open_one], waived_at=T0 + timedelta(minutes=6))[0] == "WAIVED"
    newer = question(20)
    assert rnd_check_of([open_one, newer], waived_at=T0 + timedelta(minutes=6)) == (
        "PENDING",
        newer.question_id,
    )


def test_choosing_own_development_creates_an_internal_question_to_the_first_rnd_department():
    created = rnd_question_for("변압기 2차 전압 480Y/277V", "2.5", ["TS_TRF", "RND_FW"], KINDS)
    assert created["target"] == "INTERNAL" and created["target_dept"] == "RND_FW"
    assert created["issue_type"] == "RND_FEASIBILITY"
    assert "원문 2.5" in created["question_text"] and "480Y/277V" in created["question_text"]
    # An unmapped department counts as TECH, so no R&D reviewer means 422.
    with pytest.raises(ApplicationError) as missing:
        rnd_question_for("x", None, ["TS_TRF", "UNKNOWN_DEPT"], KINDS)
    assert (missing.value.status_code, missing.value.code) == (422, "VALIDATION_ERROR")


def rating_options(rnd_check):
    products, trace = master_filter(load_own_products(), "TRANSFORMER")
    gap = compare(DEMO_A[4], products, trace)
    return {o.path: o for o in options_for(gap, "ALLOWED", rnd_check=rnd_check).options}


def test_a_partner_path_while_rnd_is_open_needs_a_waiver_reason():
    options = rating_options("PENDING")
    qid = uuid4()
    with pytest.raises(ApplicationError) as refused:
        check_selection(options["EXTERNAL_PRODUCT"], "PENDING", None, qid)
    assert (refused.value.status_code, refused.value.code) == (422, "TFT_RND_CHECK_REQUIRED")
    assert refused.value.details[0]["reason"] == str(qid)
    with pytest.raises(ApplicationError):
        check_selection(options["EXTERNAL_PRODUCT"], "PENDING", "too short", qid)
    reason = "고객 일정상 R&D 회신을 기다릴 수 없어 외부 조달로 먼저 검토"
    assert check_selection(options["EXTERNAL_PRODUCT"], "PENDING", reason, qid) == "WAIVED"
    # Own paths are not gated; a closed check needs no reason.
    assert check_selection(options["OWN_DEVELOPMENT"], "PENDING", None, qid) == "PENDING"
    closed = rating_options("CONFIRMED_NOT_FEASIBLE")
    assert (
        check_selection(closed["EXTERNAL_PRODUCT"], "CONFIRMED_NOT_FEASIBLE", None, qid)
        == "CONFIRMED_NOT_FEASIBLE"
    )


def test_blocked_or_unknown_paths_are_an_invalid_transition():
    feasible = rating_options("CONFIRMED_FEASIBLE")
    for option in (feasible["EXTERNAL_PRODUCT"], None):
        with pytest.raises(ApplicationError) as refused:
            check_selection(option, "CONFIRMED_FEASIBLE", None, None)
        assert (refused.value.status_code, refused.value.code) == (409, "TFT_INVALID_TRANSITION")


def test_rnd_answer_fields_are_only_for_internal_rnd_questions():
    check_reply_fields(
        "INTERNAL", "RND_FW", KINDS, "FEASIBLE_WITH_SCHEDULE", date(2027, 3, 1), False
    )
    check_reply_fields("CUSTOMER", None, KINDS, None, None, None)
    for target, dept in (("CUSTOMER", None), ("INTERNAL", "TS_TRF"), ("INTERNAL", "UNKNOWN")):
        with pytest.raises(ApplicationError) as refused:
            check_reply_fields(target, dept, KINDS, "FEASIBLE", None, None)
        assert refused.value.code == "TFT_QUESTION_FIELD_NOT_ALLOWED"
    with pytest.raises(ApplicationError) as partial:
        check_reply_fields("INTERNAL", "RND_FW", KINDS, None, date(2027, 3, 1), None)
    assert partial.value.code == "VALIDATION_ERROR"
