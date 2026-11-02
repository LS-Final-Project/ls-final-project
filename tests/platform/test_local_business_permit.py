from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest

from scripts.local_business.permit import Denied, Permit


def permit(tmp_path, **kwargs):
    p = Permit(tmp_path / "local" / "charges.sqlite3")
    token = p.authorize(max_jobs=1, max_calls=2, reserve_usd="0.50", **kwargs)
    job = str(uuid4())
    p.grant(token, job)
    return p, token, job


def test_concurrent_driver_and_same_job_permissions_are_not_duplicated(tmp_path):
    p = Permit(tmp_path / "charges.sqlite3")

    def authorize(_):
        try:
            return p.authorize(max_jobs=1, max_calls=1, reserve_usd="0.25")
        except Denied:
            return None

    with ThreadPoolExecutor(2) as pool:
        tokens = list(pool.map(authorize, range(2)))
    assert sum(t is not None for t in tokens) == 1
    token = next(t for t in tokens if t is not None)
    job = str(uuid4())
    p.grant(token, job)
    with pytest.raises(Denied, match="LOCAL_JOB_ALREADY_AUTHORIZED"):
        p.grant(token, job)
    with pytest.raises(Denied, match="LOCAL_JOB_LIMIT"):
        p.grant(token, str(uuid4()))


@pytest.mark.parametrize("unknown", ["response_without_usage", "timeout_or_crash"])
def test_unknown_or_interrupted_call_survives_restart_and_blocks_new_spend(tmp_path, unknown):
    p, token, job = permit(tmp_path)
    charge = p.reserve(token, job, "configured-model")
    if unknown == "response_without_usage":
        p.settle(charge, actual_usd=None)
    p.close_driver(token)
    reopened = Permit(p.path)
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        reopened.authorize(max_jobs=1, max_calls=1, reserve_usd="0.10")
    with reopened.transaction() as db:
        row = db.execute("SELECT reserved,actual,state FROM charges").fetchone()
        assert row["reserved"] == "0.50"
        assert row["actual"] is None
        assert row["state"] in {"RESERVED", "UNKNOWN"}


def test_known_overrun_is_not_clamped_and_blocks_future_calls(tmp_path):
    p, token, job = permit(tmp_path)
    charge = p.reserve(token, job, "configured-model")
    assert (
        p.settle(charge, actual_usd="2.10", usage={"input_tokens": 10, "total_tokens": 20})
        == "LOCAL_RESERVATION_EXCEEDED"
    )
    with p.transaction() as db:
        assert p.committed(db) == Decimal("2.10")
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        p.reserve(token, job, "configured-model")


def test_known_usage_and_total_spend_persist_across_explicit_permissions(tmp_path):
    p, token, job = permit(tmp_path, budget_usd="1.00")
    charge = p.reserve(token, job, "configured-model")
    p.settle(
        charge, actual_usd="0.40", usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    )
    p.close_driver(token)
    token = p.authorize(max_jobs=1, max_calls=1, reserve_usd="0.50", budget_usd="1.00")
    new_job = str(uuid4())
    p.grant(token, new_job)
    charge = p.reserve(token, new_job, "configured-model")
    p.settle(charge, actual_usd="0.40")
    p.close_driver(token)
    with pytest.raises(Denied, match="LOCAL_BUDGET_EXHAUSTED"):
        p.authorize(max_jobs=1, max_calls=1, reserve_usd="0.50", budget_usd="1.00")


def test_reservation_precedes_network_and_blocks_simultaneous_paid_calls(tmp_path):
    p, token, job = permit(tmp_path)
    p.reserve(token, job, "configured-model")
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        p.reserve(token, job, "configured-model")


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "3.00", "3.01"])
def test_invalid_amounts_do_not_open_a_driver(tmp_path, amount):
    p = Permit(tmp_path / "charges.sqlite3")
    with pytest.raises(ValueError):
        p.authorize(max_jobs=1, max_calls=1, reserve_usd=amount)


@pytest.mark.parametrize("limit", [True, False, 1.5, "2"])
@pytest.mark.parametrize("name", ["max_jobs", "max_calls"])
def test_limits_require_integers(tmp_path, limit, name):
    p = Permit(tmp_path / "charges.sqlite3")
    limits = {"max_jobs": 1, "max_calls": 1, name: limit}
    with pytest.raises(ValueError, match="INVALID_LOCAL_LIMIT"):
        p.authorize(**limits, reserve_usd="0.25")
