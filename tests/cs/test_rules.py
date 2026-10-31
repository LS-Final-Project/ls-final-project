"""KAN-104 rule engine without an LLM: fixed policy, calendar clock, boundaries and invalid input."""

import hashlib
import inspect
from datetime import UTC, datetime, timedelta, timezone

import pytest

from backend.engine.cs import rules
from backend.engine.cs.rules import (
    RETURN_SUBTYPE_STATED_ONLY,
    RETURN_SUBTYPE_UNCONFIRMED,
    RuleInputError,
    decide,
    decide_priority,
    due_times,
    product_family,
    route,
    sla_elapsed_hours,
    sla_status,
    sla_target,
)

RECEIVED = datetime(2026, 3, 28, 22, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("category", "urgency", "priority", "rule_id"),
    [
        # SAFETY wins for every category, information requests included.
        ("TECH_SPEC", "SAFETY", "P1", "PR-01"),
        ("DEFECT", "SAFETY", "P1", "PR-01"),
        ("DEFECT", "SITE_DOWN", "P1", "PR-02"),
        ("FW_UPDATE", "SITE_DOWN", "P1", "PR-02"),
        ("RETURN", "SITE_DOWN", "P2", "PR-03"),
        ("WARRANTY", "SITE_DOWN", "P2", "PR-03"),
        # SITE_DOWN on an information category falls through to the later rules.
        ("MANUAL", "SITE_DOWN", "P4", "PR-07"),
        ("QUOTE", "PROD_IMPACT", "P2", "PR-04"),
        ("INSTALL", "COMMISSION", "P2", "PR-04"),
        ("ORDER_LEAD", "NONE", "P3", "PR-05"),
        ("DEFECT", "DEADLINE", "P3", "PR-05"),
        ("COMPLIANCE", "DEADLINE", "P3", "PR-06"),
        ("TECH_SPEC", "NONE", "P4", "PR-07"),
    ],
)
def test_priority_rules_in_table_order(category, urgency, priority, rule_id):
    assert decide_priority(category, urgency) == rules.PriorityDecision(priority, rule_id)


@pytest.mark.parametrize(
    ("category", "urgency"), [("UNKNOWN", "NONE"), ("DEFECT", ""), ("defect", "NONE")]
)
def test_unknown_codes_are_refused_not_guessed(category, urgency):
    with pytest.raises(RuleInputError):
        decide_priority(category, urgency)


def test_family_comes_from_the_product_master():
    assert product_family("TRF-OIL-1000") == "TRANSFORMER"
    with pytest.raises(RuleInputError):
        product_family("TRF-OIL-9999")


@pytest.mark.parametrize(
    ("family", "category", "dept", "rule_id"),
    [
        ("LV_BREAKER", "TECH_SPEC", "TS_LV", "RT-LV_BREAKER-TECH_SPEC"),
        ("LV_BREAKER", "WARRANTY", "WARRANTY_DESK", "RT-LV_BREAKER-WARRANTY"),
        # Family overrides replace the category default.
        ("TRANSFORMER", "INSTALL", "FIELD_SVC", "RT-TRANSFORMER-INSTALL"),
        ("MV_EQUIP", "INSTALL", "FIELD_SVC", "RT-MV_EQUIP-INSTALL"),
    ],
)
def test_routing_by_family_and_primary_category(family, category, dept, rule_id):
    decision = route(family, category, None)
    assert (decision.dept, decision.rule_id, decision.review_reasons) == (dept, rule_id, ())


def test_unconfirmed_return_is_never_treated_as_non_defect():
    unconfirmed = route("TRANSFORMER", "RETURN", None)
    assert unconfirmed.dept != "SCM"
    assert unconfirmed.rule_id == "RT-TRANSFORMER-RETURN"
    assert unconfirmed.review_reasons == (RETURN_SUBTYPE_UNCONFIRMED,)
    defect = route("TRANSFORMER", "RETURN", "DEFECT")
    assert (defect.dept, defect.review_reasons) == (unconfirmed.dept, ())
    non_defect = route("TRANSFORMER", "RETURN", "NON_DEFECT")
    assert (non_defect.dept, non_defect.rule_id) == ("SCM", "RT-ALL-RETURN-NON_DEFECT")
    # Stated by the customer only: the SCM route stands, but a person checks before promising.
    assert non_defect.review_reasons == (RETURN_SUBTYPE_STATED_ONLY,)
    # The escalation is a suggestion with its condition, not a second department.
    assert non_defect.escalation_dept == "SALES_OPS"
    assert non_defect.escalation_condition


def test_secondary_category_cannot_replace_the_primary():
    # A subtype only matters for a primary RETURN; a secondary return intent changes nothing.
    assert route("LV_BREAKER", "DEFECT", "NON_DEFECT") == route("LV_BREAKER", "DEFECT", None)
    assert "secondary_category" not in inspect.signature(decide).parameters


def test_invalid_routing_input_is_refused():
    with pytest.raises(RuleInputError):
        route("LV_BREAKER", "RETURN", "UNKNOWN")  # type: ignore[arg-type]
    for subtype in (None, "DEFECT", "NON_DEFECT"):
        # Every branch refuses an unknown family, the NON_DEFECT shortcut included.
        with pytest.raises(RuleInputError, match="family"):
            route("NO_FAMILY", "RETURN", subtype)
    with pytest.raises(RuleInputError):
        route("NO_FAMILY", "DEFECT", None)


def test_sla_targets_by_category_tier_and_priority():
    target = sla_target("TECH_SPEC", "PLATINUM", "P1")
    assert (target.sla_id, target.first_response_hours, target.resolution_hours) == (
        "SLA-TECH_SPEC-PLATINUM-P1",
        1,
        24,
    )
    for bad in (("TECH_SPEC", "SILVER", "P1"), ("TECH_SPEC", "GOLD", "P5")):
        with pytest.raises(RuleInputError):
            sla_target(*bad)


def test_due_times_use_the_calendar_clock_across_weekends_and_offsets():
    target = sla_target("TECH_SPEC", "PLATINUM", "P1")
    # Saturday night: no business-hours shift, and the offset is kept exactly.
    first, resolution = due_times(RECEIVED, target)
    assert (first, resolution) == (RECEIVED + timedelta(hours=1), RECEIVED + timedelta(hours=24))
    seoul = RECEIVED.astimezone(timezone(timedelta(hours=9)))
    assert due_times(seoul, target) == (first, resolution)
    with pytest.raises(RuleInputError):
        due_times(datetime(2026, 3, 28, 22, 0), target)


def test_waiting_for_the_customer_is_not_counted():
    end = RECEIVED + timedelta(hours=30)
    assert sla_elapsed_hours(RECEIVED, end, 6.5) == pytest.approx(23.5)
    with pytest.raises(RuleInputError):
        sla_elapsed_hours(end, RECEIVED, 0)
    with pytest.raises(RuleInputError):
        sla_elapsed_hours(RECEIVED, end, -1)


@pytest.mark.parametrize(
    ("elapsed", "resolved", "status"),
    [
        (24.0, True, "MET"),  # exactly on target is met
        (24.01, True, "BREACHED"),
        (17.99, False, "ON_TRACK"),
        (18.0, False, "AT_RISK"),  # 75% of 24 h
        (23.99, False, "AT_RISK"),
        (24.0, False, "BREACHED"),
    ],
)
def test_sla_status_boundaries(elapsed, resolved, status):
    assert sla_status(elapsed, 24, resolved) == status


def test_no_response_closure_has_no_resolution_sla():
    assert sla_status(500, 24, True, closed_without_response=True) == "N/A"


def test_decide_combines_the_rules_with_the_policy_version():
    decision = decide(
        category="RETURN",
        urgency="SITE_DOWN",
        product_code="TRF-OIL-1000",
        customer_tier="GOLD",
        return_subtype=None,
        received_at=RECEIVED,
    )
    assert decision.priority.rule_id == "PR-03"
    assert decision.product_family == "TRANSFORMER"
    assert decision.routing.review_reasons == (RETURN_SUBTYPE_UNCONFIRMED,)
    assert decision.sla.sla_id == "SLA-RETURN-GOLD-P2"
    assert decision.policy_version == rules.POLICY_VERSION


def edited_policy(monkeypatch, tmp_path):
    for name in rules.POLICY_FILES:
        (tmp_path / name).write_bytes((rules.POLICY_DIR / name).read_bytes())
    table = tmp_path / "priority_rules.csv"
    table.write_text(table.read_text(encoding="utf-8-sig").replace("PR-07,7", "PR-07,8"))
    monkeypatch.setattr(rules, "POLICY_DIR", tmp_path)
    rules.load_policy.cache_clear()


def test_the_c_s_app_does_not_start_with_an_edited_policy(monkeypatch, tmp_path):
    from backend.api.cs.main import create_app
    from backend.shared.settings import Settings

    edited_policy(monkeypatch, tmp_path)
    try:
        with pytest.raises(RuntimeError, match="priority_rules.csv changed"):
            create_app(Settings(_env_file=None, environment="test"))
    finally:
        monkeypatch.undo()
        rules.load_policy.cache_clear()


def test_an_edited_policy_table_is_detected(monkeypatch, tmp_path):
    edited_policy(monkeypatch, tmp_path)
    try:
        with pytest.raises(RuntimeError, match="priority_rules.csv changed"):
            rules.load_policy()
    finally:
        monkeypatch.undo()
        rules.load_policy.cache_clear()


def test_a_confirmed_subtype_from_inspection_overrides_what_the_customer_stated():
    # Customer said non-defective, inspection found a defect: family return department, no review.
    inspected = route("TRANSFORMER", "RETURN", "NON_DEFECT", confirmed_subtype="DEFECT")
    assert (inspected.dept, inspected.rule_id, inspected.review_reasons) == (
        route("TRANSFORMER", "RETURN", "DEFECT").dept,
        "RT-TRANSFORMER-RETURN",
        (),
    )
    # Confirmed non-defective: SCM without a review reason, whatever was stated at intake.
    for stated in (None, "DEFECT", "NON_DEFECT"):
        confirmed = route("TRANSFORMER", "RETURN", stated, confirmed_subtype="NON_DEFECT")
        assert (confirmed.dept, confirmed.review_reasons) == ("SCM", ())
    # The confirmed value is checked like the stated one and ignored outside returns.
    with pytest.raises(RuleInputError):
        route("TRANSFORMER", "RETURN", None, confirmed_subtype="UNKNOWN")  # type: ignore[arg-type]
    other = route("TRANSFORMER", "WARRANTY", None, confirmed_subtype="NON_DEFECT")
    assert other.dept != "SCM" and other.review_reasons == ()


def test_decide_passes_the_confirmed_subtype_through():
    stated_only = decide(
        category="RETURN",
        urgency="NONE",
        product_code="TRF-OIL-1000",
        customer_tier="GOLD",
        return_subtype="NON_DEFECT",
        received_at=RECEIVED,
    )
    assert stated_only.routing.review_reasons == (RETURN_SUBTYPE_STATED_ONLY,)
    confirmed = decide(
        category="RETURN",
        urgency="NONE",
        product_code="TRF-OIL-1000",
        customer_tier="GOLD",
        return_subtype="NON_DEFECT",
        received_at=RECEIVED,
        confirmed_subtype="NON_DEFECT",
    )
    assert (confirmed.routing.dept, confirmed.routing.review_reasons) == ("SCM", ())


def test_firmware_history_is_locked_but_stays_out_of_the_rule_version(monkeypatch):
    assert "firmware_history.csv" not in rules.POLICY_FILES
    joined = "".join(rules.POLICY_FILES[name] for name in sorted(rules.POLICY_FILES))
    digest = hashlib.sha256(joined.encode()).hexdigest()[:12]
    assert rules.POLICY_VERSION == f"cs-rules-v1:{digest}"
    assert len(rules.firmware_releases()) == 39
    first = rules.firmware_releases()[0]
    assert (first.product_code, first.fw_version) == ("ACB-AS-2000", "1.2")
    monkeypatch.setitem(rules.REFERENCE_FILES, "firmware_history.csv", "0" * 64)
    rules.firmware_releases.cache_clear()
    with pytest.raises(RuntimeError, match="firmware_history.csv changed"):
        rules.firmware_releases()
    monkeypatch.undo()
    rules.firmware_releases.cache_clear()
