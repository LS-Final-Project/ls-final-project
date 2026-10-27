"""KAN-104: the rules reproduce the provided policy results (docs/evaluation.md 16~17행).

Runs only when CS_TRAINING_DATA_DIR points at the folder with tickets.csv (read only, never
committed). Only the train and valid splits are read; the test split stays for the final
evaluation.

Priority uses records with priority_adjusted_by_agent=N, as the target 1.000 requires.
Department reproduction needs a reviewed return subtype, which the data does not have
(KAN-95). Until then a return must route to one of its two policy departments and is never
sent to Order & Logistics without a confirmed subtype.
"""

import csv
import os
from datetime import datetime
from pathlib import Path

import pytest

from backend.engine.cs.rules import (
    RuleInputError,
    decide_priority,
    product_family,
    route,
    sla_elapsed_hours,
    sla_status,
    sla_target,
)

DATA = os.getenv("CS_TRAINING_DATA_DIR")
# The provided data was cut on this date; open tickets were measured up to it.
AS_OF = datetime.fromisoformat("2026-10-01T00:00:00+00:00")
# Source anomalies, excluded from the SLA elapsed check by ID: the first response is dated
# after AS_OF, so the provided customer_wait_hours is negative. The rules refuse such input.
NEGATIVE_WAIT = {"CSV1-003890", "CSV1-003946", "CSV1-003951"}


@pytest.fixture(scope="module")
def tickets():
    if not DATA or not (Path(DATA) / "tickets.csv").exists():
        pytest.skip("제공 데이터 검사는 CS_TRAINING_DATA_DIR이 필요합니다.")
    with open(Path(DATA) / "tickets.csv", encoding="utf-8-sig", newline="") as file:
        rows = [row for row in csv.DictReader(file) if row["data_split"] in ("train", "valid")]
    assert len(rows) == 3403
    return rows


def time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_priority_reproduces_every_unadjusted_record(tickets):
    target = [t for t in tickets if t["priority_adjusted_by_agent"] == "N"]
    wrong = []
    for t in target:
        decision = decide_priority(t["category_code"], t["urgency_flag"])
        if (decision.priority, decision.rule_id) != (t["priority"], t["priority_rule_id"]):
            wrong.append(t["ticket_id"])
    assert len(target) == 3303
    assert wrong == []


def test_family_and_department_follow_the_policy(tickets):
    mismatched = []
    for t in tickets:
        family = product_family(t["product_code"])
        assert family == t["product_family"], t["ticket_id"]
        if t["category_code"] == "RETURN":
            continue
        decision = route(family, t["category_code"], None)
        if (decision.dept, decision.rule_id) != (t["assigned_dept"], t["routing_rule_id"]):
            mismatched.append(t["ticket_id"])
    assert mismatched == []


def test_returns_route_to_one_of_their_policy_departments(tickets):
    returns = [t for t in tickets if t["category_code"] == "RETURN"]
    for t in returns:
        family = product_family(t["product_code"])
        options = {
            (d.dept, d.rule_id)
            for d in (route(family, "RETURN", s) for s in ("DEFECT", "NON_DEFECT"))
        }
        assert (t["assigned_dept"], t["routing_rule_id"]) in options, t["ticket_id"]
        # Without a reviewed subtype the rules ask a person instead of choosing SCM.
        assert route(family, "RETURN", None).dept != "SCM"
    assert len(returns) == 298


def test_sla_targets_elapsed_time_and_status(tickets):
    negative = {t["ticket_id"] for t in tickets if float(t["customer_wait_hours"] or 0) < 0}
    assert negative == NEGATIVE_WAIT
    for t in tickets:
        target = sla_target(t["category_code"], t["customer_tier"], t["priority"])
        assert (str(target.first_response_hours), str(target.resolution_hours)) == (
            t["sla_first_response_target_h"],
            t["sla_resolution_target_h"],
        ), t["ticket_id"]
        resolved = bool(t["resolved_at_utc"])
        if t["ticket_id"] in NEGATIVE_WAIT:
            with pytest.raises(RuleInputError):
                sla_elapsed_hours(
                    time(t["received_at_utc"]), AS_OF, float(t["customer_wait_hours"])
                )
            continue
        end = time(t["resolved_at_utc"]) if resolved else AS_OF
        elapsed = sla_elapsed_hours(
            time(t["received_at_utc"]), end, float(t["customer_wait_hours"] or 0)
        )
        # The provided value is rounded to 0.1 h.
        assert abs(elapsed - float(t["sla_elapsed_hours"])) < 0.11, t["ticket_id"]
        status = sla_status(
            float(t["sla_elapsed_hours"]),
            target.resolution_hours,
            resolved,
            closed_without_response=t["final_action"] == "CLOSED_NO_RESPONSE",
        )
        assert status == t["sla_resolution_status"], t["ticket_id"]


def test_returns_reproduce_with_the_reviewed_subtype(tickets):
    """Runs once KAN-95 has locked the reviewed return subtypes (CS_RETURN_SUBTYPE_GOLD).

    The gold file is a person's review of the inquiry text. It is never derived from the
    provided department, so a mismatch here is a finding to review, not a value to copy.
    """
    path = os.getenv("CS_RETURN_SUBTYPE_GOLD")
    if not path:
        pytest.skip("반품 하위유형 정답(KAN-95) 파일이 필요합니다.")
    with open(path, encoding="utf-8-sig", newline="") as file:
        gold = {row["ticket_id"]: row["return_subtype"] or None for row in csv.DictReader(file)}
    assert set(gold.values()) <= {"DEFECT", "NON_DEFECT", None}
    returns = [t for t in tickets if t["category_code"] == "RETURN"]
    assert {t["ticket_id"] for t in returns} <= set(gold)
    mismatched, unconfirmed = [], []
    for t in returns:
        subtype = gold[t["ticket_id"]]
        decision = route(product_family(t["product_code"]), "RETURN", subtype)
        if subtype is None:
            unconfirmed.append(t["ticket_id"])
        elif (decision.dept, decision.rule_id) != (t["assigned_dept"], t["routing_rule_id"]):
            mismatched.append(t["ticket_id"])
    # Unconfirmed subtypes are reported for review, not counted as reproduced.
    print(f"returns={len(returns)} unconfirmed={len(unconfirmed)} mismatched={mismatched}")
    assert mismatched == []
