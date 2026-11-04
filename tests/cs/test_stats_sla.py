"""KAN-233 업무 통계 SLA 계산(DB 없음): 분석 화면과 같은 기한, 임박 75%, 미확인 분리."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.api.cs.stats import sla

NOW = datetime(2026, 11, 5, 3, tzinfo=UTC)  # 서울 12:00
SAFETY = {
    "category_code": "DEFECT",
    "secondary_category": None,
    "urgency_flag": "SAFETY",
    "provided_info": [],
    "return_subtype": None,
}


def row(hours_ago, ai=SAFETY, state="IN_REVIEW", closed_after=None, sent_after=None, **extra):
    received = NOW - timedelta(hours=hours_ago)
    return {
        "id": uuid4(),
        "external_id": "T-1",
        "subject": "s",
        "received_at": received,
        "product_code": "TRF-OIL-1000",
        "state": state,
        "closed_at": received + timedelta(hours=closed_after) if closed_after is not None else None,
        "owner_user_id": None,
        "customer_tier": None,
        "return_subtype_confirmed": None,
        "classification": ai,
        "corrections": [],
        "first_response_at": received + timedelta(hours=sent_after) if sent_after else None,
        "transfer_depts": [],
        **extra,
    }


def summarize(rows, **kw):
    return sla.summary(
        rows, start=None, end=None, now=NOW, sees_all=True, departments=frozenset(), **kw
    )


def test_standard_tier_p1_deadlines_count_from_receipt():
    ticket = sla.ticket_sla(row(10))
    assert ticket is not None and ticket.priority == "P1"
    assert (ticket.first_response_hours, ticket.resolution_hours) == (4, 72)


def test_at_risk_starts_at_75_percent_of_the_resolution_target():
    # 72시간 목표의 75% = 54시간.
    just_before, at = summarize([row(53.9)]), summarize([row(54)])
    assert (just_before.at_risk, at.at_risk, at.overdue) == (0, 1, 0)
    assert summarize([row(72)]).overdue == 1


def test_unclassified_and_unknown_policy_values_are_unknown_not_guessed():
    outside = {**SAFETY, "category_code": "NOT_A_CATEGORY"}
    result = summarize([row(1, ai=None), row(1, ai=outside)])
    assert (result.unknown, result.at_risk, result.overdue) == (2, 0, 0)
    assert result.first_response.met + result.first_response.breached == 0


def test_correction_without_ai_run_still_gives_deadlines():
    ticket = row(1, ai=None, corrections=[{"category_code": "DEFECT", "urgency_flag": "SAFETY"}])
    assert sla.ticket_sla(ticket) is not None


def test_closed_ticket_without_a_send_is_unrecorded_for_first_response():
    result = summarize([row(100, state="CLOSED", closed_after=50)])
    assert (result.first_response.unrecorded, result.resolution.met) == (1, 1)


def test_owner_due_today_uses_the_seoul_calendar_day():
    # 서울 12:00 기준, 기한 11시간 뒤(23:00)는 오늘, 13시간 뒤(다음 날 01:00)는 아니다.
    counts = sla.by_owner([row(61), row(59)], NOW)
    assert counts[None] == {"due_today": 1, "overdue": 0, "sla_unknown": 0}


# ── 리뷰 P2: 재발 집계 실패는 그 칸만 null, SLA 입력은 두 경로가 함께 쓴다 ──
import asyncio  # noqa: E402

from backend.api.cs.stats import routes as stats_routes  # noqa: E402


def test_recurrence_failure_is_null_not_an_overview_error(monkeypatch):
    async def boom(conn, row, now):
        raise RuntimeError("signal failed")

    monkeypatch.setattr(stats_routes, "signal_for", boom)
    result = asyncio.run(stats_routes.recurrence_after_completion(None, [{"id": 1}], 2, False, NOW))
    assert result is None


def test_recurrence_counts_and_limited_flag(monkeypatch):
    class Signal:
        def __init__(self, decision):
            self.decision = decision

    decisions = iter(["RECURRENCE", "NONE", "RECURRENCE"])

    async def fake(conn, row, now):
        return Signal(next(decisions)), None

    monkeypatch.setattr(stats_routes, "signal_for", fake)
    result = asyncio.run(stats_routes.recurrence_after_completion(None, [{}, {}, {}], 4, True, NOW))
    assert result is not None
    assert (result.checked, result.recurrence, result.not_applicable, result.limited) == (
        3,
        2,
        4,
        True,
    )


def test_sla_rows_are_read_once_for_the_same_filter(monkeypatch):
    calls = []

    async def fake(conn, p):
        calls.append(p)
        return [{"id": len(calls)}]

    monkeypatch.setattr(stats_routes.repository, "sla_tickets", fake)
    stats_routes._sla_rows.clear()
    p = {"start": None, "end": None, "dept": None, "customer": None, "owner": None}
    first = asyncio.run(stats_routes.shared_sla_rows(None, p))
    second = asyncio.run(stats_routes.shared_sla_rows(None, dict(p)))
    other = asyncio.run(stats_routes.shared_sla_rows(None, {**p, "dept": "TS_TRF"}))
    assert first is second and len(calls) == 2 and other != first
    stats_routes._sla_rows.clear()
