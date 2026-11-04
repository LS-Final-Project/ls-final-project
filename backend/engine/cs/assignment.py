"""Rule-based assignment of new C/S tickets to agents (KAN-214, contracts/cs/assignment.md).

Pure Python, no LLM, no DB: the same tickets, agents and loads always give the same result.
Who can take a ticket is decided by the agent skill table (proposal p.23 employee table,
assumed data): languages the agent reads and product families the agent handles.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

ASSIGNMENT_RULES_VERSION = "cs-assign-v1"
FALLBACK_LANGUAGE = "en"

AssignedReason = Literal["LANGUAGE_AND_FAMILY", "LANGUAGE_ONLY", "FAMILY_ENGLISH_FALLBACK"]
Reason = AssignedReason | Literal["NO_MATCH"]
# Rule order: the first tier with an active candidate decides.
REASONS: tuple[Reason, ...] = (
    "LANGUAGE_AND_FAMILY",
    "LANGUAGE_ONLY",
    "FAMILY_ENGLISH_FALLBACK",
    "NO_MATCH",
)
# Unknown priority sorts after P4 (no analysis yet).
_PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


def primary_language(tag: str) -> str:
    """Primary subtag of a language tag, lower-case: `en-US` → `en`, `ZH_hans` → `zh`."""
    return tag.strip().replace("_", "-").split("-", 1)[0].lower()


@dataclass(frozen=True)
class Agent:
    user_id: UUID
    languages: frozenset[str]
    families: frozenset[str]
    active: bool = True
    open_count: int = 0


@dataclass(frozen=True)
class TicketInput:
    ticket_id: UUID
    language: str
    # None when the product is not in product_master: only language rules can match.
    family: str | None
    received_at: datetime
    priority: str | None = None


@dataclass(frozen=True)
class Decision:
    ticket_id: UUID
    user_id: UUID | None
    reason: Reason
    matched_language: str | None
    matched_family: str | None
    # Agents in the deciding tier, and the chosen agent's open tickets before this one.
    candidate_count: int = 0
    chosen_open_count: int | None = None
    rule_version: str = field(default=ASSIGNMENT_RULES_VERSION)


def _tiers(
    ticket: TicketInput, agents: Sequence[Agent]
) -> Iterable[tuple[Reason, list[Agent], str | None, str | None]]:
    language = primary_language(ticket.language)
    family = ticket.family
    active = [a for a in agents if a.active]
    if family is not None:
        yield (
            "LANGUAGE_AND_FAMILY",
            [a for a in active if language in a.languages and family in a.families],
            language,
            family,
        )
    yield "LANGUAGE_ONLY", [a for a in active if language in a.languages], language, None
    if family is not None:
        yield (
            "FAMILY_ENGLISH_FALLBACK",
            [a for a in active if family in a.families and FALLBACK_LANGUAGE in a.languages],
            FALLBACK_LANGUAGE,
            family,
        )


def decide(ticket: TicketInput, agents: Sequence[Agent], loads: dict[UUID, int]) -> Decision:
    """One ticket. `loads` is each agent's current open ticket count (missing = 0).

    In the first tier with a candidate, the agent with the fewest open tickets wins; a tie
    goes to the smaller user_id so the result never depends on input order.
    """
    for reason, candidates, language, family in _tiers(ticket, agents):
        if candidates:
            chosen = min(candidates, key=lambda a: (loads.get(a.user_id, 0), str(a.user_id)))
            return Decision(
                ticket.ticket_id,
                chosen.user_id,
                reason,
                language,
                family,
                candidate_count=len(candidates),
                chosen_open_count=loads.get(chosen.user_id, 0),
            )
    return Decision(ticket.ticket_id, None, "NO_MATCH", None, None)


def batch_order(tickets: Iterable[TicketInput]) -> list[TicketInput]:
    """Priority first (P1 … P4, unknown last), then oldest received, then ticket_id."""
    return sorted(
        tickets,
        key=lambda t: (
            _PRIORITY_ORDER.get(t.priority or "", len(_PRIORITY_ORDER)),
            t.received_at,
            str(t.ticket_id),
        ),
    )


def assign_batch(tickets: Iterable[TicketInput], agents: Sequence[Agent]) -> list[Decision]:
    """Decides tickets in batch order. Each assignment adds one to the chosen agent's load, so
    the next ticket sees it and the load spreads across agents."""
    loads = {a.user_id: a.open_count for a in agents}
    decisions = []
    for ticket in batch_order(tickets):
        decision = decide(ticket, agents, loads)
        if decision.user_id is not None:
            loads[decision.user_id] = loads.get(decision.user_id, 0) + 1
        decisions.append(decision)
    return decisions
