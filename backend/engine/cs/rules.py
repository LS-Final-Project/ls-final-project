"""C/S rule engine: priority, product family, department and SLA without an LLM (KAN-104).

The policy tables in ./policy are verbatim copies of the provided reference files. The code
checks that each table still says what this module implements, so an edited table fails
loudly instead of silently changing results. The C/S app runs this check when it starts.

Only the primary category drives priority and routing. A secondary category never replaces
it, and an unconfirmed return subtype is never treated as NON_DEFECT.
"""

import csv
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Literal, get_args

from backend.engine.cs.classification import Category, Urgency

POLICY_DIR = Path(__file__).with_name("policy")
# SHA-256 of the copied reference files. Changing a table needs a reviewed update here.
POLICY_FILES = {
    "priority_rules.csv": "f0d83685955a297b40b740a75ed6a05ae625174aee4d269e1b9908b728700cc8",
    "routing_rules.csv": "caacc47fe5a5b2167a8725285d1e1bcb7324b4792411e772b94b4c1ea846980d",
    "sla_policy.csv": "0ffa8d265cc863b3a1add2db0628d181d9e3584b015c0adbce47b39be6ac0fe2",
    "product_master.csv": "e8d6784d456499f706e5468eb6334790b32a7a145c43378e36d5d76a6ab19cbb",
}
POLICY_VERSION = (
    "cs-rules-v1:"
    + hashlib.sha256(
        "".join(POLICY_FILES[name] for name in sorted(POLICY_FILES)).encode()
    ).hexdigest()[:12]
)

Priority = Literal["P1", "P2", "P3", "P4"]
Tier = Literal["PLATINUM", "GOLD", "STANDARD"]
ReturnSubtype = Literal["DEFECT", "NON_DEFECT"]
SlaStatus = Literal["MET", "BREACHED", "AT_RISK", "ON_TRACK", "N/A"]

CATEGORIES = frozenset(get_args(Category))
URGENCIES = frozenset(get_args(Urgency))
TIERS = frozenset(get_args(Tier))
# Reasons routing needs a person. UNCONFIRMED: no return subtype at all (the customer did not
# say and nobody has inspected). STATED_ONLY: the route rests on what the customer stated at
# intake, with no inspection result yet (LS engineers, 2026-10-29: the return reason is decided
# by inspection, not at intake).
RETURN_SUBTYPE_UNCONFIRMED = "RETURN_SUBTYPE_UNCONFIRMED"
RETURN_SUBTYPE_STATED_ONLY = "RETURN_SUBTYPE_STATED_ONLY"

# priority_rules.csv, evaluated top to bottom. Conditions are written for people in the
# table, so the same rules are spelled out here and checked against its IDs and priorities.
_SITE_DOWN_P1 = frozenset({"DEFECT", "INSTALL", "FW_UPDATE"})
_SITE_DOWN_P2 = frozenset({"ORDER_LEAD", "RETURN", "WARRANTY"})
_OPERATIONAL = frozenset({"DEFECT", "INSTALL", "FW_UPDATE", "WARRANTY", "RETURN", "ORDER_LEAD"})
_PRIORITY_RULES: tuple[tuple[str, Priority], ...] = (
    ("PR-01", "P1"),
    ("PR-02", "P1"),
    ("PR-03", "P2"),
    ("PR-04", "P2"),
    ("PR-05", "P3"),
    ("PR-06", "P3"),
    ("PR-07", "P4"),
)
_NON_DEFECT_RULE = "RT-ALL-RETURN-NON_DEFECT"
_SLA_CLOCK = "24x7 calendar hours"
_AT_RISK = "elapsed >= 75% of target"
AT_RISK_RATIO = 0.75


class RuleInputError(ValueError):
    """The input is outside the policy (unknown code, naive time). Never guessed around."""


@dataclass(frozen=True)
class PriorityDecision:
    priority: Priority
    rule_id: str


@dataclass(frozen=True)
class RoutingDecision:
    dept: str
    rule_id: str
    rule_source: str
    # A suggestion for a person, with the table's condition. Never applied automatically.
    escalation_dept: str | None
    escalation_condition: str | None
    review_reasons: tuple[str, ...]


@dataclass(frozen=True)
class SlaTarget:
    sla_id: str
    first_response_hours: int
    resolution_hours: int


@dataclass(frozen=True)
class RuleDecision:
    policy_version: str
    priority: PriorityDecision
    product_family: str
    routing: RoutingDecision
    sla: SlaTarget
    first_response_due: datetime
    resolution_due: datetime


@dataclass(frozen=True)
class _Policy:
    families: dict[str, str]
    routes: dict[tuple[str, str], dict[str, str]]
    non_defect: dict[str, str]
    sla: dict[tuple[str, str, str], SlaTarget]


def _read(name: str) -> list[dict[str, str]]:
    path = POLICY_DIR / name
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != POLICY_FILES[name]:
        raise RuntimeError(f"{name} changed without updating POLICY_FILES")
    return list(csv.DictReader(data.decode("utf-8-sig").splitlines()))


@cache
def load_policy() -> _Policy:
    """Reads the tables once and checks they match what this module implements."""
    rows = _read("priority_rules.csv")
    if [(r["rule_id"], r["priority"]) for r in rows] != list(_PRIORITY_RULES):
        raise RuntimeError("priority_rules.csv no longer matches the coded rules")

    families = {r["product_code"]: r["family"] for r in _read("product_master.csv")}

    routes: dict[tuple[str, str], dict[str, str]] = {}
    non_defect: dict[str, str] | None = None
    for row in _read("routing_rules.csv"):
        if row["rule_source"] in ("CATEGORY_DEFAULT", "FAMILY_OVERRIDE"):
            key = (row["family"], row["category"])
            if key in routes:
                raise RuntimeError(f"Two routing rules for {key}")
            routes[key] = row
        elif row["rule_id"] == _NON_DEFECT_RULE:
            non_defect = row
    if non_defect is None:
        raise RuntimeError("routing_rules.csv has no NON_DEFECT return rule")

    sla: dict[tuple[str, str, str], SlaTarget] = {}
    for row in _read("sla_policy.csv"):
        if row["clock"] != _SLA_CLOCK or row["at_risk_threshold"] != _AT_RISK:
            raise RuntimeError(f"{row['sla_id']} uses a clock or threshold this code lacks")
        sla[(row["category"], row["customer_tier"], row["priority"])] = SlaTarget(
            row["sla_id"], int(row["first_response_hours"]), int(row["resolution_hours"])
        )
    return _Policy(families, routes, non_defect, sla)


def _require(value: str, allowed: frozenset[str], name: str) -> None:
    if value not in allowed:
        raise RuleInputError(f"Unknown {name}: {value!r}")


def decide_priority(category: str, urgency: str) -> PriorityDecision:
    """priority_rules.csv, first matching rule wins."""
    _require(category, CATEGORIES, "category")
    _require(urgency, URGENCIES, "urgency")
    if urgency == "SAFETY":
        rule = "PR-01"
    elif urgency == "SITE_DOWN" and category in _SITE_DOWN_P1:
        rule = "PR-02"
    elif urgency == "SITE_DOWN" and category in _SITE_DOWN_P2:
        rule = "PR-03"
    elif urgency in ("PROD_IMPACT", "COMMISSION"):
        rule = "PR-04"
    elif category in _OPERATIONAL:
        rule = "PR-05"
    elif urgency == "DEADLINE":
        rule = "PR-06"
    else:
        rule = "PR-07"
    return PriorityDecision(dict(_PRIORITY_RULES)[rule], rule)


def product_family(product_code: str) -> str:
    family = load_policy().families.get(product_code)
    if family is None:
        raise RuleInputError(f"Unknown product: {product_code!r}")
    return family


def route(
    family: str,
    category: str,
    return_subtype: ReturnSubtype | None,
    *,
    confirmed_subtype: ReturnSubtype | None = None,
) -> RoutingDecision:
    """Department for the primary category (routing_rules.csv).

    `return_subtype` is what the customer stated at intake (the LLM reads it from the message);
    `confirmed_subtype` is what a person recorded after inspection (KAN-108 workflow). The
    confirmed value wins when present. A return goes to Order & Logistics (SCM) when the
    effective subtype is NON_DEFECT; if that rests on the stated value alone the decision carries
    RETURN_SUBTYPE_STATED_ONLY so a person checks it before anything is promised. No subtype at
    all keeps the family's return department with RETURN_SUBTYPE_UNCONFIRMED, because a null is
    not evidence of a non-defective return. The subtype is ignored for other categories, so a
    secondary return intent cannot move the ticket.
    """
    _require(category, CATEGORIES, "category")
    for name, value in (
        ("return subtype", return_subtype),
        ("confirmed subtype", confirmed_subtype),
    ):
        if value not in (None, "DEFECT", "NON_DEFECT"):
            raise RuleInputError(f"Unknown {name}: {value!r}")
    effective = confirmed_subtype if confirmed_subtype is not None else return_subtype
    policy = load_policy()
    # Checked first so every branch refuses an unknown family, the NON_DEFECT one included.
    if family not in policy.families.values():
        raise RuleInputError(f"Unknown product family: {family!r}")
    reasons: tuple[str, ...] = ()
    if category == "RETURN" and effective == "NON_DEFECT":
        row = policy.non_defect
        if confirmed_subtype is None:
            reasons = (RETURN_SUBTYPE_STATED_ONLY,)
    else:
        found = policy.routes.get((family, category))
        if found is None:
            raise RuleInputError(f"No routing rule for {family!r} / {category!r}")
        row = found
        if category == "RETURN" and effective is None:
            reasons = (RETURN_SUBTYPE_UNCONFIRMED,)
    return RoutingDecision(
        dept=row["primary_dept"],
        rule_id=row["rule_id"],
        rule_source=row["rule_source"],
        escalation_dept=row["escalation_dept"] or None,
        escalation_condition=row["escalation_condition"] or None,
        review_reasons=reasons,
    )


def sla_target(category: str, tier: str, priority: str) -> SlaTarget:
    _require(category, CATEGORIES, "category")
    _require(tier, TIERS, "customer tier")
    target = load_policy().sla.get((category, tier, priority))
    if target is None:
        raise RuleInputError(f"Unknown priority: {priority!r}")
    return target


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuleInputError(f"{name} needs a time zone")
    return value


def due_times(received_at: datetime, target: SlaTarget) -> tuple[datetime, datetime]:
    """First response and resolution deadlines on the 24x7 calendar clock, before any pause.

    Time waiting for the customer is added back by the caller through sla_elapsed_hours.
    """
    start = _aware(received_at, "received_at")
    return (
        start + timedelta(hours=target.first_response_hours),
        start + timedelta(hours=target.resolution_hours),
    )


def sla_elapsed_hours(received_at: datetime, end: datetime, waiting_hours: float) -> float:
    """Calendar hours from receipt to `end` (resolution, or now), minus customer waiting."""
    start, stop = _aware(received_at, "received_at"), _aware(end, "end")
    if stop < start:
        raise RuleInputError("end is before received_at")
    if waiting_hours < 0:
        raise RuleInputError("waiting_hours is negative")
    return (stop - start).total_seconds() / 3600 - waiting_hours


def sla_status(
    elapsed_hours: float,
    target_hours: float,
    resolved: bool,
    closed_without_response: bool = False,
) -> SlaStatus:
    """MET/BREACHED once resolved; ON_TRACK, AT_RISK (≥75%) or BREACHED while open.

    A ticket closed because the customer never replied has no resolution SLA (N/A).
    """
    if closed_without_response:
        return "N/A"
    if resolved:
        return "MET" if elapsed_hours <= target_hours else "BREACHED"
    if elapsed_hours >= target_hours:
        return "BREACHED"
    return "AT_RISK" if elapsed_hours >= AT_RISK_RATIO * target_hours else "ON_TRACK"


def decide(
    *,
    category: str,
    urgency: str,
    product_code: str,
    customer_tier: str,
    return_subtype: ReturnSubtype | None,
    received_at: datetime,
    confirmed_subtype: ReturnSubtype | None = None,
) -> RuleDecision:
    """All rule results for one ticket from reviewed or classified inputs.

    `return_subtype` is the customer's stated reason at intake and `confirmed_subtype` the
    inspection result a person recorded; see `route`. There is deliberately no
    secondary_category parameter: it never changes these results.
    """
    priority = decide_priority(category, urgency)
    family = product_family(product_code)
    routing = route(family, category, return_subtype, confirmed_subtype=confirmed_subtype)
    target = sla_target(category, customer_tier, priority.priority)
    first_due, resolution_due = due_times(received_at, target)
    return RuleDecision(
        POLICY_VERSION, priority, family, routing, target, first_due, resolution_due
    )
