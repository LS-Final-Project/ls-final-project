"""KAN-113 개선요청 후보 결정 규칙. 합성 기록만 쓴다(원문·실제 데이터 없음)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from backend.engine.cs.improvement_candidates import (
    CaseRecord,
    DocumentSignalRecord,
    FirmwareRelease,
    applicable_fix,
    document_candidates,
    evaluate,
    fault_candidates,
    parse_version,
)

T0 = datetime(2025, 10, 1, tzinfo=UTC)
LATER = datetime(2026, 12, 31, tzinfo=UTC)


def case(n, customer, days, product="TRF-OIL-1000", fault="FLT-067", cause="WEAR_PART", fw=None):
    return CaseRecord(
        case_id=f"RC-{n}",
        ticket_id=f"T-{n}",
        product_code=product,
        fault_id=fault,
        customer_id=customer,
        known_at=T0 + timedelta(days=days),
        root_cause_type=cause,
        fw_version_at_issue=fw,
    )


def test_three_distinct_customers_within_90_days_reach_the_threshold():
    cases = [case(1, "A", 0), case(2, "A", 10), case(3, "B", 40), case(4, "C", 85)]
    [result] = fault_candidates(cases, [], as_of=LATER)
    assert (result.outcome, result.reason_code) == ("ELIGIBLE", "REPEAT_90D_3_CUSTOMERS")
    # 도달 시각은 세 번째 고객의 기록이 알려진 시각이다. 같은 고객의 재발은 세지 않는다.
    assert result.reached_at == T0 + timedelta(days=85)
    assert (result.distinct_customers, result.cases_total) == (3, 4)
    assert result.reason == "90일 안 서로 다른 고객 3/3곳."
    assert result.condition_status == "UNKNOWN" and len(result.evidence) == 4


def test_the_window_slides_and_a_91_day_spread_does_not_count():
    [spread] = fault_candidates(
        [case(1, "A", 0), case(2, "B", 45), case(3, "C", 91)], [], as_of=LATER
    )
    assert (spread.outcome, spread.distinct_customers_in_best_window) == ("NOT_ELIGIBLE", 2)
    assert spread.reason == "90일 안 서로 다른 고객 2/3곳."
    # 뒤쪽 90일 구간에서 셋이 모이면 도달이다.
    [late] = fault_candidates(
        [case(1, "A", 0), case(2, "B", 100), case(3, "C", 120), case(4, "D", 150)], [], as_of=LATER
    )
    assert (late.outcome, late.reached_at) == ("ELIGIBLE", T0 + timedelta(days=150))


def test_records_known_after_as_of_are_not_used():
    cases = [case(1, "A", 0), case(2, "B", 30), case(3, "C", 60)]
    [early] = fault_candidates(cases, [], as_of=T0 + timedelta(days=59))
    assert (early.outcome, early.cases_total) == ("NOT_ELIGIBLE", 2)
    [exact] = fault_candidates(cases, [], as_of=T0 + timedelta(days=60))
    assert exact.outcome == "ELIGIBLE"
    with pytest.raises(ValueError):
        evaluate([], [], [], as_of=datetime(2026, 1, 1))


def test_firmware_fix_released_before_the_record_makes_one_customer_enough():
    releases = [
        FirmwareRelease(
            product_code="METER-LP3500", fw_version="1.60", release_date=date(2024, 1, 1)
        ),
        FirmwareRelease(
            product_code="METER-LP3500", fw_version="1.72", release_date=date(2025, 4, 7)
        ),
        FirmwareRelease(product_code="OTHER", fw_version="9.9", release_date=date(2020, 1, 1)),
    ]
    one = [case(1, "A", 35, product="METER-LP3500", fault="FLT-072", cause="FIRMWARE", fw="1.60")]
    [result] = fault_candidates(one, releases, as_of=LATER)
    assert (result.outcome, result.reason_code) == ("ELIGIBLE", "FIRMWARE_FIX_RELEASED")
    assert (result.fw_fixed_version, result.fw_released_on) == ("1.72", date(2025, 4, 7))
    assert (result.required_customers, result.firmware_rule_applied) == (1, True)
    # 수정판이 기록 뒤에 배포됐으면 예외가 아니다(시간상 불일치 사례는 일반 기준으로 본다).
    before_fix = [
        case(1, "A", 0, product="METER-LP3500", fault="FLT-072", cause="FIRMWARE", fw="1.60")
    ]
    late_release = [
        FirmwareRelease(
            product_code="METER-LP3500", fw_version="1.72", release_date=date(2025, 12, 1)
        )
    ]
    [not_yet] = fault_candidates(before_fix, late_release, as_of=LATER)
    assert (not_yet.outcome, not_yet.firmware_rule_applied) == ("NOT_ELIGIBLE", False)
    # 고장 당시 버전이 이미 수정판 이상이면 수정판이 아니다.
    current = [
        case(1, "A", 35, product="METER-LP3500", fault="FLT-072", cause="FIRMWARE", fw="1.72")
    ]
    assert fault_candidates(current, releases, as_of=LATER)[0].outcome == "NOT_ELIGIBLE"


def test_unknown_or_odd_firmware_versions_are_insufficient_not_eligible():
    releases = [FirmwareRelease(product_code="P", fw_version="2.0", release_date=date(2025, 1, 1))]
    unknown = [case(1, "A", 10, product="P", fault="F", cause="FIRMWARE", fw=None)]
    [u] = fault_candidates(unknown, releases, as_of=LATER)
    assert (u.outcome, u.reason_code) == ("INSUFFICIENT_EVIDENCE", "FW_VERSION_UNKNOWN")
    odd = [case(1, "A", 10, product="P", fault="F", cause="FIRMWARE", fw="build-7")]
    [o] = fault_candidates(odd, releases, as_of=LATER)
    assert o.reason_code == "FW_VERSION_NOT_COMPARABLE"
    assert parse_version("v1.72") == (1, 72) and parse_version("1.10") > parse_version("1.9")
    assert applicable_fix(odd[0], releases) == (None, "FW_VERSION_NOT_COMPARABLE")


def test_missing_customer_ids_block_the_count_instead_of_counting_as_one():
    cases = [case(1, "A", 0), case(2, None, 10), case(3, None, 20)]
    [result] = fault_candidates(cases, [], as_of=LATER)
    assert (result.outcome, result.reason_code) == ("INSUFFICIENT_EVIDENCE", "NO_CUSTOMER_ID")
    assert (result.distinct_customers, result.cases_without_customer) == (1, 2)
    # Two known customers plus two unknown records must not reach three.
    [two] = fault_candidates(
        [case(1, "A", 0), case(2, "B", 5), case(3, None, 6), case(4, None, 7)], [], as_of=LATER
    )
    assert (two.outcome, two.distinct_customers_in_best_window) == ("INSUFFICIENT_EVIDENCE", 2)


def test_groups_are_per_product_and_fault_and_carry_condition_status():
    cases = [
        case(1, "A", 0),
        case(2, "B", 1),
        case(3, "C", 2),
        case(4, "A", 0, fault="FLT-001"),
        case(5, "B", 1, fault="FLT-001"),
        case(6, "C", 2, fault="FLT-001"),
        case(7, "A", 0, product="MCCB-ABS-103c"),
    ]
    results = fault_candidates(
        cases, [], as_of=LATER, condition_confirmed={("TRF-OIL-1000", "FLT-067"): True}
    )
    keyed = {(r.product_code, r.fault_id): r for r in results}
    assert keyed[("TRF-OIL-1000", "FLT-067")].condition_status == "CONFIRMED"
    assert keyed[("TRF-OIL-1000", "FLT-001")].condition_status == "UNKNOWN"
    assert keyed[("MCCB-ABS-103c", "FLT-067")].outcome == "NOT_ELIGIBLE"


def sig(n, kind, days, doc="DS-X", evidence=True, product="MCCB-ABS-103c"):
    return DocumentSignalRecord(
        ticket_id=f"T-{n}",
        product_code=product,
        doc_signal=kind,
        doc_id=doc,
        known_at=T0 + timedelta(days=days),
        has_evidence_location=evidence,
    )


def test_document_errors_need_evidence_not_repetition_and_repeated_specs_need_three():
    signals = [
        sig(1, "BROKEN_LINK", 1),
        sig(2, "VALUE_CONFLICT", 2, evidence=False),
        sig(3, "REPEATED_SPEC", 3),
        sig(4, "REPEATED_SPEC", 4),
        sig(4, "REPEATED_SPEC", 5),
        sig(5, "NOT_FOUND", 6),
        sig(6, "NOT_FOUND", 7, doc="DS-Y"),
    ]
    results = {(r.doc_signal, r.doc_id): r for r in document_candidates(signals, as_of=LATER)}
    link = results[("BROKEN_LINK", "DS-X")]
    assert (link.outcome, link.reason_code, link.required_tickets) == (
        "ELIGIBLE",
        "DOCUMENT_ERROR_EVIDENCE",
        1,
    )
    assert link.receiver_unconfirmed is True
    conflict = results[("VALUE_CONFLICT", "DS-X")]
    assert (conflict.outcome, conflict.reason_code) == (
        "INSUFFICIENT_EVIDENCE",
        "NO_EVIDENCE_LOCATION",
    )
    # 같은 문의의 신호 두 번은 한 건이다: 2/3.
    repeated = results[("REPEATED_SPEC", "DS-X")]
    assert (repeated.outcome, repeated.tickets_with_evidence, repeated.required_tickets) == (
        "NOT_ELIGIBLE",
        2,
        3,
    )
    assert ("NOT_FOUND", "DS-X") in results and ("NOT_FOUND", "DS-Y") in results


def test_evaluate_bundles_both_kinds_with_the_rule_version():
    report = evaluate(
        [case(1, "A", 0), case(2, "B", 1), case(3, "C", 2)],
        [],
        [sig(9, "NOT_FOUND", 1)],
        as_of=LATER,
    )
    assert report.version == "improvement-candidates-v1"
    assert [f.outcome for f in report.faults] == ["ELIGIBLE"]
    assert [d.outcome for d in report.documents] == ["ELIGIBLE"]


def test_the_earlier_route_sets_reached_at_when_both_apply():
    releases = [FirmwareRelease(product_code="P", fw_version="2.0", release_date=date(2025, 1, 1))]
    # Three customers by day 3 (general rule), a firmware record with a released fix on day 50.
    cases = [
        case(1, "A", 1, product="P", fault="F"),
        case(2, "B", 2, product="P", fault="F"),
        case(3, "C", 3, product="P", fault="F"),
        case(4, "D", 50, product="P", fault="F", cause="FIRMWARE", fw="1.0"),
    ]
    [result] = fault_candidates(cases, releases, as_of=LATER)
    assert (result.reason_code, result.reached_at) == (
        "REPEAT_90D_3_CUSTOMERS",
        T0 + timedelta(days=3),
    )
    # Firmware first: the firmware record on day 2 reaches before the third customer on day 60.
    early_fw = [
        case(1, "A", 1, product="P", fault="F"),
        case(2, "B", 2, product="P", fault="F", cause="FIRMWARE", fw="1.0"),
        case(3, "C", 60, product="P", fault="F"),
    ]
    [fw] = fault_candidates(early_fw, releases, as_of=LATER)
    assert (fw.reason_code, fw.reached_at) == ("FIRMWARE_FIX_RELEASED", T0 + timedelta(days=2))
    assert fw.reason.endswith("기록 고객 3곳(기준 1곳).")
    # A firmware record without a customer id does not make the group a candidate by itself.
    anonymous = [case(1, None, 5, product="P", fault="F", cause="FIRMWARE", fw="1.0")]
    assert fault_candidates(anonymous, releases, as_of=LATER)[0].outcome != "ELIGIBLE"
