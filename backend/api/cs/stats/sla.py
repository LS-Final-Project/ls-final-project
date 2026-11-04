"""SLA figures for the stats screen, computed on read from stored rule inputs (KAN-233).

SLA deadlines are not stored. Like the analysis view (analysis/service.py `build`), each
ticket's deadlines come from the rules over its latest VALID classification and every human
correction, each deadline being the earlier of the AI and corrected ones, counted from receipt.
A ticket the rules cannot run on (no classification, or a value outside the policy) has no
SLA here: it is counted as unknown, never guessed.

Like the analysis view, time waiting for the customer is not paused (rules.sla_elapsed_hours
is called with 0 waiting hours), and the 24x7 calendar clock of the policy is used.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, get_args
from uuid import UUID
from zoneinfo import ZoneInfo

from backend.api.cs.stats.schemas import (
    OverdueTicket,
    SlaCompliance,
    SlaPriority,
    SlaSummary,
)
from backend.engine.cs import analysis, rules

Row = dict[str, Any]
PRIORITIES = get_args(rules.Priority)
OVERDUE_LIMIT = 10
# "Today" for an owner's due-today count is the business calendar day in Seoul.
LOCAL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class TicketSla:
    priority: str
    first_response_due: datetime
    first_response_hours: float
    resolution_due: datetime
    resolution_hours: float


def _hours(received_at: datetime, due: datetime) -> float:
    return (due - received_at).total_seconds() / 3600


def ticket_sla(row: Row) -> TicketSla | None:
    """The deadlines the analysis view shows for this ticket, or None when it has none."""
    ai = row["classification"]
    fields = analysis.effective_fields(ai, row["corrections"] or [])
    common = {
        "product_code": row["product_code"],
        "customer_tier": row["customer_tier"],
        "return_subtype": ai.get("return_subtype") if ai else None,
        "confirmed_subtype": row["return_subtype_confirmed"],
        "received_at": row["received_at"],
    }
    try:
        ai_rules = analysis.decide_or_none(ai, **common)[0] if ai else None
        corrected = any(f.corrected for f in fields.values())
        if corrected:
            values = {name: f.effective_value for name, f in fields.items()}
            effective_rules = analysis.decide_or_none(values, **common)[0]
        else:
            effective_rules = ai_rules
    except rules.RuleInputError:
        # A naive receipt time; decide_or_none only turns policy errors into None.
        return None
    first = analysis.merge_deadline(
        ai_rules.first_response_due if ai_rules else None,
        effective_rules.first_response_due if corrected and effective_rules else None,
    )
    resolution = analysis.merge_deadline(
        ai_rules.resolution_due if ai_rules else None,
        effective_rules.resolution_due if corrected and effective_rules else None,
    )
    decision = effective_rules or ai_rules
    if first.effective is None or resolution.effective is None or decision is None:
        return None
    received = row["received_at"]
    return TicketSla(
        priority=decision.priority.priority,
        first_response_due=first.effective,
        first_response_hours=_hours(received, first.effective),
        resolution_due=resolution.effective,
        resolution_hours=_hours(received, resolution.effective),
    )


def _status(received: datetime, end: datetime, target: float, resolved: bool) -> str:
    """rules.sla_status on the calendar clock; an end before receipt counts from receipt."""
    elapsed = max(0.0, (end - received).total_seconds() / 3600)
    return rules.sla_status(elapsed, target, resolved)


def in_period(value: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    return value is not None and (start is None or value >= start) and (end is None or value < end)


def first_response(row: Row, sla: TicketSla, now: datetime) -> str | None:
    """MET/BREACHED, PENDING (no reply yet, not due) or UNRECORDED (closed with no send)."""
    sent = row["first_response_at"]
    if sent is not None:
        return _status(row["received_at"], sent, sla.first_response_hours, True)
    if row["state"] == "CLOSED":
        # Closed with no recorded customer message (e.g. a reply copied to another channel).
        return "UNRECORDED"
    status = _status(row["received_at"], now, sla.first_response_hours, False)
    return "BREACHED" if status == "BREACHED" else "PENDING"


def open_status(row: Row, sla: TicketSla, now: datetime) -> str:
    """ON_TRACK, AT_RISK (75% of the resolution target elapsed) or BREACHED, for an open ticket."""
    return _status(row["received_at"], now, sla.resolution_hours, False)


def visible_subject(row: Row, sees_all: bool, departments: frozenset[str]) -> bool:
    """The subject is shown only where the user may open the ticket (permissions.md 1.2)."""
    return sees_all or bool(departments & set(row["transfer_depts"] or ()))


def summary(
    rows: Iterable[Row],
    *,
    start: datetime | None,
    end: datetime | None,
    now: datetime,
    sees_all: bool,
    departments: frozenset[str],
) -> SlaSummary:
    """The overview's SLA section over tickets received or closed in the period."""
    first = {"MET": 0, "BREACHED": 0, "PENDING": 0, "UNRECORDED": 0}
    resolution = {"MET": 0, "BREACHED": 0}
    by_priority = {p: {"met": 0, "breached": 0, "at_risk": 0, "overdue": 0} for p in PRIORITIES}
    at_risk = overdue = unknown = 0
    late: list[tuple[float, Row, TicketSla]] = []
    for row in rows:
        received_in = in_period(row["received_at"], start, end)
        closed_in = row["state"] == "CLOSED" and in_period(row["closed_at"], start, end)
        if not (received_in or closed_in):
            continue
        sla = ticket_sla(row)
        if sla is None:
            unknown += 1
            continue
        counts = by_priority[sla.priority]
        if received_in:
            status = first_response(row, sla, now)
            if status in first:
                first[status] += 1
        if closed_in:
            status = _status(row["received_at"], row["closed_at"], sla.resolution_hours, True)
            resolution[status] += 1
            counts["met" if status == "MET" else "breached"] += 1
        if received_in and row["state"] != "CLOSED":
            status = open_status(row, sla, now)
            if status == "AT_RISK":
                at_risk += 1
                counts["at_risk"] += 1
            elif status == "BREACHED":
                overdue += 1
                counts["overdue"] += 1
                late.append(((now - sla.resolution_due).total_seconds() / 3600, row, sla))
    late.sort(key=lambda item: (-item[0], str(item[1]["id"])))
    return SlaSummary(
        measured_at=now,
        first_response=SlaCompliance(
            met=first["MET"],
            breached=first["BREACHED"],
            pending=first["PENDING"],
            unrecorded=first["UNRECORDED"],
        ),
        resolution=SlaCompliance(met=resolution["MET"], breached=resolution["BREACHED"]),
        at_risk=at_risk,
        overdue=overdue,
        unknown=unknown,
        by_priority=[SlaPriority(priority=p, **c) for p, c in by_priority.items()],  # type: ignore[arg-type]
        overdue_tickets=[
            OverdueTicket(
                ticket_id=row["id"],
                external_id=row["external_id"],
                subject=row["subject"] if visible_subject(row, sees_all, departments) else None,
                priority=sla.priority,  # type: ignore[arg-type]
                resolution_due=sla.resolution_due,
                overdue_hours=round(hours, 1),
                owner_user_id=row["owner_user_id"],
            )
            for hours, row, sla in late[:OVERDUE_LIMIT]
        ],
    )


def by_owner(rows: Iterable[Row], now: datetime) -> dict[UUID | None, Row]:
    """Per owner, over open tickets whenever received: due today, overdue and SLA unknown."""
    today_end = datetime.combine(
        now.astimezone(LOCAL).date() + timedelta(days=1), datetime.min.time(), LOCAL
    )
    owners: dict[UUID | None, Row] = {}
    for row in rows:
        if row["state"] == "CLOSED":
            continue
        counts = owners.setdefault(
            row["owner_user_id"], {"due_today": 0, "overdue": 0, "sla_unknown": 0}
        )
        sla = ticket_sla(row)
        if sla is None:
            counts["sla_unknown"] += 1
        elif open_status(row, sla, now) == "BREACHED":
            counts["overdue"] += 1
        elif sla.resolution_due < today_end:
            counts["due_today"] += 1
    return owners
