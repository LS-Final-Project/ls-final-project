"""KAN-117 재요청·조치 후 재발 판정(합성 기록). 완료 조건: 직전 요청 후 새 3건, 조치 적용 장비 재발 1건,
구형·미적용 별도, 미래 정보 차단."""

from datetime import UTC, date, datetime, timedelta

import pytest

from backend.engine.cs.improvement_followups import Action, Record, RequestState, Round, evaluate

SENT = datetime(2025, 11, 16, 0, 0, tzinfo=UTC)


def record(n, *, known, received=None, fw=None, customer="CUST-1"):
    return Record(
        case_id=f"RC-{n}",
        ticket_id=f"T-{n}",
        customer_id=customer,
        known_at=known,
        received_at=received or known - timedelta(days=1),
        fw_version_at_issue=fw,
    )


def state(status="SENT", rounds=None, actions=()):
    return RequestState(
        status=status,
        rounds=rounds or [Round(round=1, opened_at=SENT)],
        actions=list(actions),
    )


def days(n):
    return SENT + timedelta(days=n)


def test_every_three_new_records_after_the_last_send_is_a_request_again_candidate():
    records = [record(i, known=days(10 * i)) for i in range(1, 7)]
    two = evaluate(state(), records, days(25))
    assert (two.decision, two.reason_code, two.counts.new_since_sent) == ("NONE", "BELOW_3", 2)
    three = evaluate(state(), records, days(30))
    assert (three.decision, three.counts.candidate_index, three.next_action) == (
        "REQUEST_AGAIN",
        1,
        "OPEN_ROUND_REQUEST_AGAIN",
    )
    # 4·5건은 같은 1번 후보에 근거가 늘어난 것, 6건이면 2번.
    assert evaluate(state(), records, days(50)).counts.candidate_index == 1
    assert evaluate(state(), records, days(60)).counts.candidate_index == 2
    # The same customer's records count: the unit is records, not customers (2026-10-29).
    assert {r.customer_id for r in records} == {"CUST-1"}


def test_a_new_round_restarts_the_count_and_earlier_records_are_not_reused():
    records = [record(i, known=days(10 * i)) for i in range(1, 5)]
    rounds = [Round(round=1, opened_at=SENT), Round(round=2, opened_at=days(35))]
    signal = evaluate(state(rounds=rounds), records, days(45))
    assert signal.counts.new_since_sent == 1 and signal.decision == "NONE"
    assert [r.record_class for r in signal.records] == ["IN_EARLIER_ROUND"] * 3 + ["NEW_SINCE_SENT"]
    assert signal.baseline.round == 2 and signal.baseline.last_sent_at == days(35)


def test_records_rounds_and_actions_after_as_of_are_not_used():
    records = [record(i, known=days(10 * i)) for i in range(1, 4)]
    # 판정 시점(29일)에는 세 번째 기록이 아직 알려지지 않았다.
    assert evaluate(state(), records, days(29)).counts.new_since_sent == 2
    # A record known exactly at the send time is not "after" it.
    at_send = evaluate(state(), [record(9, known=SENT)], days(5))
    assert at_send.records[0].record_class == "IN_EARLIER_ROUND"
    # A round opened after as_of does not exist yet.
    later = [Round(round=1, opened_at=SENT), Round(round=2, opened_at=days(100))]
    assert evaluate(state(rounds=later), records, days(30)).baseline.round == 1
    # An action recorded after as_of is unknown at as_of.
    action = Action(
        round=1,
        dept_code="TS_LV",
        action_date=date(2025, 12, 1),
        recorded_at=days(200),
        scope_kind="FIRMWARE_VERSION",
        scope_value="1.72",
    )
    signal = evaluate(
        state("ACTION_TAKEN", actions=[action]), [record(1, known=days(30), fw="1.72")], days(60)
    )
    assert signal.counts.recurrence == 0 and signal.baseline.action_dates == []
    with pytest.raises(ValueError):
        evaluate(state(), records, datetime(2026, 1, 1))


FIX = Action(
    round=1,
    dept_code="TS_LV",
    action_date=date(2025, 12, 1),
    recorded_at=datetime(2025, 12, 1, 9, tzinfo=UTC),
    scope_kind="FIRMWARE_VERSION",
    scope_value="1.72",
)


def test_one_record_on_an_applied_unit_is_a_recurrence_and_old_units_are_counted_apart():
    records = [
        record(1, known=days(30), fw="1.72", customer="A"),
        record(2, known=days(31), fw="1.60", customer="B"),
        record(3, known=days(32), fw=None, customer="C"),
    ]
    signal = evaluate(state("COMPLETED", actions=[FIX]), records, days(40))
    assert (signal.phase, signal.decision, signal.next_action) == (
        "AFTER_ACTION",
        "RECURRENCE",
        "OPEN_ROUND_RECURRENCE",
    )
    assert [(r.record_class, r.reason_code) for r in signal.records] == [
        ("RECURRENCE", "APPLIED_UNIT_RECURRENCE"),
        ("PRE_ACTION_UNIT", "PRE_ACTION_UNIT_FIRMWARE"),
        ("APPLICATION_UNKNOWN", "UNKNOWN_FW_VERSION"),
    ]
    assert signal.records[0].basis == {"fw_version": "1.72", "action_version": "1.72"}
    assert signal.records[0].action_dept == "TS_LV"
    assert (
        signal.counts.recurrence,
        signal.counts.pre_action_unit,
        signal.counts.application_unknown,
    ) == (1, 1, 1)
    # 구형 장비 고객은 업데이트 안내 대상. 재요청으로는 세지 않는다(조치 뒤).
    assert signal.update_notice_customers == ["B"] and signal.counts.new_since_sent == 0
    only_old = evaluate(state("FOLLOW_UP", actions=[FIX]), records[1:], days(40))
    assert (only_old.decision, only_old.reason_code) == ("NONE", "NO_RECURRENCE")


def test_the_occurrence_date_decides_before_or_after_not_the_load_time():
    # 조치 전(11/30 KST)에 접수됐지만 조치 뒤에 적재된 기록은 조치 전 발생이다.
    late_load = record(1, known=days(40), received=datetime(2025, 11, 30, 3, tzinfo=UTC), fw="1.72")
    # 12/1 08:00 KST = 11/30 23:00 UTC: 현업 날짜로는 적용일 당일이라 미확인.
    same_day = record(2, known=days(41), received=datetime(2025, 11, 30, 23, tzinfo=UTC), fw="1.72")
    signal = evaluate(state("ACTION_TAKEN", actions=[FIX]), [late_load, same_day], days(50))
    assert [(r.record_class, r.reason_code) for r in signal.records] == [
        ("BEFORE_ACTION_DATE", None),
        ("APPLICATION_UNKNOWN", "SAME_DAY_AS_ACTION"),
    ]
    assert signal.decision == "NONE"


def test_unstructured_scopes_and_missing_erp_data_stay_unknown():
    other = Action(
        round=1,
        dept_code="QA",
        action_date=date(2025, 12, 1),
        recorded_at=datetime(2025, 12, 1, 9, tzinfo=UTC),
    )
    shipped = Action(
        round=1,
        dept_code="SCM",
        action_date=date(2025, 12, 1),
        recorded_at=datetime(2025, 12, 1, 9, tzinfo=UTC),
        scope_kind="SHIPPED_FROM",
        scope_value="2025-12-01",
    )
    late = record(1, known=days(40), received=days(39))
    assert (
        evaluate(state("ACTION_TAKEN", actions=[other]), [late], days(50)).records[0].reason_code
        == "UNKNOWN_SCOPE"
    )
    assert (
        evaluate(state("ACTION_TAKEN", actions=[shipped]), [late], days(50)).records[0].reason_code
        == "UNKNOWN_SHIP_DATE"
    )


def test_partly_acted_requests_count_toward_request_again_and_recurrence_wins():
    # 요약 상태 ADOPTED(한 부서만 조치): 미적용 장비 기록은 재요청 3건에 들고, 적용 장비 1건이면 재발 우선.
    old = [record(i, known=days(30 + i), fw="1.60", customer=f"C{i}") for i in range(1, 4)]
    signal = evaluate(state("ADOPTED", actions=[FIX]), old, days(50))
    assert (signal.phase, signal.decision, signal.counts.new_since_sent) == (
        "BEFORE_ACTION",
        "REQUEST_AGAIN",
        3,
    )
    with_applied = evaluate(
        state("ADOPTED", actions=[FIX]), [*old, record(9, known=days(40), fw="1.80")], days(50)
    )
    assert (with_applied.decision, with_applied.counts.new_since_sent) == ("RECURRENCE", 3)


def test_requests_not_sent_have_no_signal():
    signal = evaluate(state("REVIEWED", rounds=[]), [record(1, known=days(3))], days(5))
    assert (signal.phase, signal.decision, signal.next_action) == ("NOT_SENT", "NONE", "NONE")
