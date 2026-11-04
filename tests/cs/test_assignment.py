"""KAN-214 assignment rules: each rule branch, the tie-break and load spreading in a batch.

Rule conformance only (contracts/cs/assignment.md): whether the policy is a good one is not
evaluated here.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.engine.cs.assignment import (
    ASSIGNMENT_RULES_VERSION,
    IMPROVEMENT_RULES_VERSION,
    Agent,
    RequestInput,
    TicketInput,
    assign_batch,
    assign_requests,
    batch_order,
    decide,
    decide_request,
    primary_language,
)

T0 = datetime(2026, 11, 1, tzinfo=UTC)
A = UUID("00000000-0000-4000-8000-00000000000a")
B = UUID("00000000-0000-4000-8000-00000000000b")
C = UUID("00000000-0000-4000-8000-00000000000c")


def agent(user_id, languages, families, *, active=True, open_count=0):
    return Agent(user_id, frozenset(languages), frozenset(families), active, open_count)


def ticket(n, language="th", family="LV_BREAKER", *, priority=None, minutes=0):
    return TicketInput(
        UUID(int=n), language, family, T0 + timedelta(minutes=minutes), priority=priority
    )


def test_language_and_family_picks_the_least_loaded_matching_agent():
    agents = [
        agent(A, ["en", "th"], ["LV_BREAKER"]),
        agent(B, ["th"], ["LV_BREAKER", "DRIVE"]),
        agent(C, ["th"], ["DRIVE"]),
    ]
    d = decide(ticket(1), agents, {A: 3, B: 1, C: 0})
    assert (d.user_id, d.reason, d.matched_language, d.matched_family) == (
        B,
        "LANGUAGE_AND_FAMILY",
        "th",
        "LV_BREAKER",
    )
    # C has fewer tickets but does not handle the family; it is not in the tier.
    assert (d.candidate_count, d.chosen_open_count, d.rule_version) == (
        2,
        1,
        ASSIGNMENT_RULES_VERSION,
    )


def test_language_only_when_no_agent_has_both():
    agents = [agent(A, ["th"], ["DRIVE"]), agent(B, ["en"], ["LV_BREAKER"])]
    d = decide(ticket(1), agents, {})
    assert (d.user_id, d.reason, d.matched_language, d.matched_family) == (
        A,
        "LANGUAGE_ONLY",
        "th",
        None,
    )


def test_family_with_english_when_nobody_reads_the_language():
    agents = [
        agent(A, ["es"], ["LV_BREAKER"]),
        agent(B, ["en"], ["LV_BREAKER"]),
        agent(C, ["en"], ["DRIVE"]),
    ]
    d = decide(ticket(1, "pl"), agents, {})
    assert (d.user_id, d.reason, d.matched_language, d.matched_family) == (
        B,
        "FAMILY_ENGLISH_FALLBACK",
        "en",
        "LV_BREAKER",
    )


def test_no_match_leaves_the_ticket_unassigned():
    agents = [agent(A, ["es"], ["DRIVE"]), agent(B, ["en"], ["DRIVE"])]
    d = decide(ticket(1, "pl", "LV_BREAKER"), agents, {})
    assert (d.user_id, d.reason, d.candidate_count, d.chosen_open_count) == (
        None,
        "NO_MATCH",
        0,
        None,
    )
    assert decide(ticket(2), [], {}).reason == "NO_MATCH"


def test_inactive_agents_are_never_chosen():
    agents = [agent(A, ["th"], ["LV_BREAKER"], active=False), agent(B, ["en"], ["LV_BREAKER"])]
    d = decide(ticket(1), agents, {})
    assert (d.user_id, d.reason) == (B, "FAMILY_ENGLISH_FALLBACK")


def test_unknown_product_family_uses_language_only():
    agents = [agent(A, ["en"], ["LV_BREAKER"]), agent(B, ["th"], ["DRIVE"])]
    assert decide(ticket(1, family=None), agents, {}).reason == "LANGUAGE_ONLY"
    assert decide(ticket(2, "pl", family=None), agents, {}).reason == "NO_MATCH"


def test_language_tag_is_reduced_to_its_primary_subtag():
    assert [primary_language(t) for t in ("en-US", "ZH_hans", " th ")] == ["en", "zh", "th"]
    d = decide(ticket(1, "TH-th"), [agent(A, ["th"], ["LV_BREAKER"])], {})
    assert (d.user_id, d.matched_language) == (A, "th")


def test_tie_goes_to_the_smaller_user_id_whatever_the_input_order():
    one, two = agent(B, ["th"], ["LV_BREAKER"]), agent(A, ["th"], ["LV_BREAKER"])
    assert decide(ticket(1), [one, two], {}).user_id == A
    assert decide(ticket(1), [two, one], {}).user_id == A


def test_batch_spreads_load_and_counts_existing_open_tickets():
    agents = [
        agent(A, ["th"], ["LV_BREAKER"], open_count=2),
        agent(B, ["th"], ["LV_BREAKER"], open_count=0),
    ]
    decisions = assign_batch([ticket(n, minutes=n) for n in range(1, 6)], agents)
    # B starts empty: B, B (tie 2:2 → A), then alternate.
    assert [d.user_id for d in decisions] == [B, B, A, B, A]
    assert [d.chosen_open_count for d in decisions] == [0, 1, 2, 2, 3]


def test_batch_runs_in_priority_then_received_order():
    tickets = [
        ticket(1, minutes=0, priority="P4"),
        ticket(2, minutes=5, priority="P1"),
        ticket(3, minutes=1, priority=None),
        ticket(4, minutes=2, priority="P1"),
        ticket(5, minutes=3, priority="P3"),
    ]
    assert [t.ticket_id.int for t in batch_order(tickets)] == [4, 2, 5, 1, 3]
    agents = [agent(A, ["th"], ["LV_BREAKER"]), agent(B, ["th"], ["LV_BREAKER"])]
    decisions = assign_batch(tickets, agents)
    assert [d.ticket_id.int for d in decisions] == [4, 2, 5, 1, 3]
    assert [d.user_id for d in decisions] == [A, B, A, B, A]


def test_same_input_gives_the_same_result():
    agents = [agent(A, ["th", "en"], ["LV_BREAKER"]), agent(B, ["th"], ["DRIVE"])]
    tickets = [ticket(n, ["th", "pl", "es"][n % 3], minutes=n) for n in range(9)]
    assert assign_batch(tickets, agents) == assign_batch(list(reversed(tickets)), agents)


# --- Improvement requests: product family only, fewest open requests ----------------------


def request(n, family="LV_BREAKER", minutes=0):
    return RequestInput(UUID(int=n), family, T0 + timedelta(minutes=minutes))


def test_request_goes_to_the_family_agent_with_the_fewest_open_requests():
    # Language is not used: a Thai-only agent handling the family qualifies.
    agents = [agent(A, ["en"], ["LV_BREAKER"]), agent(B, ["th"], ["LV_BREAKER"])]
    d = decide_request(request(1), agents, {A: 2, B: 1})
    assert (d.user_id, d.reason, d.matched_family, d.candidate_count, d.chosen_open_count) == (
        B,
        "FAMILY",
        "LV_BREAKER",
        2,
        1,
    )
    assert d.rule_version == IMPROVEMENT_RULES_VERSION
    assert decide_request(request(1), list(reversed(agents)), {}).user_id == A  # tie → user_id


def test_request_without_a_family_agent_is_no_match():
    agents = [agent(A, ["en"], ["DRIVE"]), agent(B, ["en"], ["LV_BREAKER"], active=False)]
    assert decide_request(request(1), agents, {}).reason == "NO_MATCH"
    assert decide_request(request(2, family=None), agents, {}).user_id is None


def test_request_batch_spreads_load_oldest_first():
    agents = [agent(A, ["en"], ["LV_BREAKER"]), agent(B, ["en"], ["LV_BREAKER"])]
    loads = {A: 1}
    decisions = assign_requests(
        [request(1, minutes=3), request(2, minutes=0), request(3, minutes=1)], agents, loads
    )
    assert [d.request_id.int for d in decisions] == [2, 3, 1]
    assert [d.user_id for d in decisions] == [B, A, B]
    assert loads == {A: 2, B: 2}
