"""Improvement request state rules (permissions.md 2.2, 2.2.1), without a database.

The request status is a summary of the current round's department rows. A person never
picks it: department actions change one department, and the summary follows.
"""

from typing import Any, Literal

Row = dict[str, Any]
DeptStatus = Literal[
    "SENT", "ACKNOWLEDGED", "ADOPTED", "INFO_REQUESTED", "REJECTED", "ACTION_TAKEN"
]

# Department-unit actions are refused once the request left the delivery phase.
FROZEN_STATES = ("FOLLOW_UP", "COMPLETED")
# A department may be called again (resend) from these states only.
RESEND_FROM = ("SENT", "ACKNOWLEDGED", "INFO_REQUESTED", "REJECTED")


def summarize(departments: list[Row]) -> str:
    """Request status from the current round's departments (2.2.1 table, first match wins).

    Args:
        departments: rows with `status` (and `rejection_reviewed_at`) for the current round.

    Returns:
        One of REJECTED, ACTION_TAKEN, ADOPTED, INFO_REQUESTED, ACKNOWLEDGED, SENT.
    """
    if not departments:
        raise ValueError("A round needs at least one department")
    states = [d["status"] for d in departments]
    if all(s == "REJECTED" for s in states):
        return "REJECTED"
    if all(s == "ACTION_TAKEN" for s in states if s != "REJECTED"):
        return "ACTION_TAKEN"
    if any(s in ("ADOPTED", "ACTION_TAKEN") for s in states):
        return "ADOPTED"
    if "INFO_REQUESTED" in states:
        return "INFO_REQUESTED"
    if "ACKNOWLEDGED" in states:
        return "ACKNOWLEDGED"
    return "SENT"


def rejection_closed(departments: list[Row]) -> bool:
    """Every department rejected and every rejection was reviewed: the request is closed by
    rejection and only a new round (KAN-117) reopens it."""
    return bool(departments) and all(
        d["status"] == "REJECTED" and d["rejection_reviewed_at"] is not None for d in departments
    )


def followup_allowed(departments: list[Row]) -> bool:
    """ACTION_TAKEN → FOLLOW_UP needs every department acted or rejection-reviewed, and at least
    one action (2.2)."""
    acted = [d for d in departments if d["status"] == "ACTION_TAKEN"]
    settled = all(
        d["status"] == "ACTION_TAKEN"
        or (d["status"] == "REJECTED" and d["rejection_reviewed_at"] is not None)
        for d in departments
    )
    return bool(departments) and bool(acted) and settled


def reply_target(reply_type: str) -> DeptStatus:
    """The department status a reply sets."""
    targets: dict[str, DeptStatus] = {
        "ADOPTED": "ADOPTED",
        "INFO_REQUESTED": "INFO_REQUESTED",
        "REJECTED": "REJECTED",
    }
    return targets[reply_type]
