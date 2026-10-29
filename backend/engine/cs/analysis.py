"""AI classification, human corrections and the values the rules use (KAN-107). No database.

The AI result (`ai_draft`) is never edited. A person's correction (`human_input`) is stored on
its own and wins per field over every AI run, including later ones: re-running the analysis
does not undo a correction. Rules are computed twice, once from the AI values and once from
the effective values, and each SLA deadline is the earlier of the two (2026-10-29 decision):
a correction that raises urgency pulls the deadline in, one that lowers it never pushes the
deadline past what the AI values gave. Deadlines count from the ticket's receipt either way.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from backend.engine.cs import rules

CORRECTABLE_FIELDS = ("category_code", "secondary_category", "urgency_flag", "provided_info")
Source = Literal["ai_draft", "human_input"]


@dataclass(frozen=True)
class FieldValue:
    ai_value: Any
    corrected_value: Any
    effective_value: Any
    # None when neither an AI value nor a correction exists.
    source: Source | None
    corrected: bool


def effective_fields(
    ai: dict[str, Any] | None, corrections: Sequence[dict[str, Any]]
) -> dict[str, FieldValue]:
    """Per field: the latest correction if any, otherwise the AI value.

    Args:
        ai: classification of the latest VALID run, or None.
        corrections: correction `fields` maps, oldest first.

    Returns:
        dict: one FieldValue per correctable field.
    """
    values: dict[str, FieldValue] = {}
    for name in CORRECTABLE_FIELDS:
        ai_value = ai.get(name) if ai else None
        changed = [c[name] for c in corrections if name in c]
        if changed:
            values[name] = FieldValue(ai_value, changed[-1], changed[-1], "human_input", True)
        else:
            values[name] = FieldValue(
                ai_value, None, ai_value, "ai_draft" if ai is not None else None, False
            )
    return values


@dataclass(frozen=True)
class Deadline:
    ai: datetime | None
    corrected: datetime | None
    effective: datetime | None
    source: Source | None


def merge_deadline(ai: datetime | None, corrected: datetime | None) -> Deadline:
    """The earlier of the AI and corrected deadlines. Ties keep the AI deadline."""
    if ai is None and corrected is None:
        return Deadline(None, None, None, None)
    if corrected is None:
        return Deadline(ai, None, ai, "ai_draft")
    if ai is None or corrected < ai:
        return Deadline(ai, corrected, corrected, "human_input")
    return Deadline(ai, corrected, ai, "ai_draft")


def decide_or_none(
    values: dict[str, Any],
    *,
    product_code: str,
    customer_tier: str | None,
    return_subtype: rules.ReturnSubtype | None,
    confirmed_subtype: rules.ReturnSubtype | None,
    received_at: datetime,
) -> tuple[rules.RuleDecision | None, str | None]:
    """Runs the rules, or says why it cannot (missing input or a value outside the policy)."""
    if values.get("category_code") is None or values.get("urgency_flag") is None:
        return None, "CS_CLASSIFICATION_MISSING"
    if not customer_tier:
        return None, "CS_CUSTOMER_TIER_UNKNOWN"
    # The stated return reason only applies while the primary category is RETURN.
    stated = return_subtype if values["category_code"] == "RETURN" else None
    try:
        return (
            rules.decide(
                category=values["category_code"],
                urgency=values["urgency_flag"],
                product_code=product_code,
                customer_tier=customer_tier,
                return_subtype=stated,
                received_at=received_at,
                confirmed_subtype=confirmed_subtype,
            ),
            None,
        )
    except rules.RuleInputError:
        return None, "CS_RULE_INPUT"
