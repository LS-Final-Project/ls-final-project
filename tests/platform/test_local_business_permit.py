import sqlite3
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


def test_explicit_unlimited_passes_all_four_caps_and_keeps_known_costs(tmp_path):
    p = Permit(tmp_path / "unlimited.sqlite3")
    token = p.authorize(max_jobs=1, max_calls=1, reserve_usd="0.01", unlimited=True)
    for _ in range(101):
        job = str(uuid4())
        p.grant(token, job)
        charge = p.reserve(token, job, "fake-only")
        assert p.settle(charge, actual_usd="5.00") is None
    with p.transaction() as db:
        assert p.driver(db, token)["limit_mode"] == "unlimited"
        assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == 101
        assert db.execute("SELECT count(*) FROM charges WHERE state='SETTLED'").fetchone()[0] == 101
        assert p.committed(db) == Decimal("505.00")  # Authored fake accounting, no provider calls.
    with pytest.raises(Denied, match="LOCAL_JOB_ALREADY_AUTHORIZED"):
        p.grant(token, job)
    with pytest.raises(Denied, match="LOCAL_DRIVER_ALREADY_AUTHORIZED"):
        p.authorize(max_jobs=1, max_calls=1, reserve_usd="0.01", unlimited=True)


@pytest.mark.parametrize("settle_unknown", [True, False])
def test_unlimited_unknown_or_interrupted_reservation_still_blocks_new_work(
    tmp_path, settle_unknown
):
    p = Permit(tmp_path / "unlimited.sqlite3")
    token = p.authorize(max_jobs=1, max_calls=1, reserve_usd="0.15", unlimited=True)
    job = str(uuid4())
    p.grant(token, job)
    charge = p.reserve(token, job, "fake-only")
    if settle_unknown:
        assert p.settle(charge, actual_usd=None) == "LOCAL_COST_UNKNOWN"
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        p.grant(token, str(uuid4()))
    p.close_driver(token)
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        Permit(p.path).authorize(max_jobs=1, max_calls=1, reserve_usd="0.15", unlimited=True)


def test_unlimited_mode_belongs_to_charge_token_even_after_driver_closes(tmp_path):
    p = Permit(tmp_path / "unlimited.sqlite3")
    token = p.authorize(max_jobs=1, max_calls=1, reserve_usd="0.15", unlimited=True)
    job = str(uuid4())
    p.grant(token, job)
    charge = p.reserve(token, job, "fake-only")
    p.close_driver(token)
    assert Permit(p.path).settle(charge, actual_usd="0.90") is None
    with p.transaction() as db:
        assert db.execute("SELECT state FROM charges").fetchone()[0] == "SETTLED"
        assert (
            db.execute("SELECT limit_mode FROM driver_modes WHERE token=?", (token,)).fetchone()[0]
            == "unlimited"
        )


def test_additive_mode_table_is_concurrent_and_legacy_permissions_stay_limited(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    token, job, charge = str(uuid4()), str(uuid4()), str(uuid4())
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE driver(singleton INTEGER PRIMARY KEY CHECK(singleton=1),token TEXT,
              job_limit INTEGER NOT NULL,call_limit INTEGER NOT NULL,reservation TEXT NOT NULL,budget TEXT NOT NULL);
            CREATE TABLE grants(job_id TEXT PRIMARY KEY,token TEXT NOT NULL);
            CREATE TABLE charges(id TEXT PRIMARY KEY,token TEXT NOT NULL,job_id TEXT NOT NULL,
              model TEXT NOT NULL,reserved TEXT NOT NULL,actual TEXT,state TEXT NOT NULL,
              usage TEXT,reason TEXT);
        """)
        db.execute("INSERT INTO driver VALUES(1,?,1,1,'0.15','1.50')", (token,))
        db.execute("INSERT INTO grants VALUES(?,?)", (job, token))
        db.execute(
            "INSERT INTO charges VALUES(?,?,?,'fake','0.15',NULL,'RESERVED',NULL,NULL)",
            (charge, token, job),
        )
    with ThreadPoolExecutor(4) as pool:
        permits = list(pool.map(lambda _: Permit(path), range(4)))
    p = permits[0]
    with p.transaction() as db:
        assert p.driver(db, token)["limit_mode"] == "limited"
        assert db.execute("SELECT count(*) FROM charges").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM driver_modes").fetchone()[0] == 0
    assert p.settle(charge, actual_usd="0.90") == "LOCAL_RESERVATION_EXCEEDED"
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        p.grant(token, str(uuid4()))


def test_limited_token_is_not_upgraded_by_a_later_unlimited_request(tmp_path):
    p, token, job = permit(tmp_path)
    charge = p.reserve(token, job, "fake-only")
    p.close_driver(token)
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        p.authorize(max_jobs=1, max_calls=1, reserve_usd="0.15", unlimited=True)
    assert p.settle(charge, actual_usd="0.60") == "LOCAL_RESERVATION_EXCEEDED"


def test_only_this_process_known_inflight_charges_can_overlap(tmp_path):
    p, token, job = permit(tmp_path, budget_usd="1.50")
    first = p.reserve(token, job, "fake")
    second = p.reserve(token, job, "fake", active_charges=(first,))
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        Permit(p.path).reserve(token, job, "fake")
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        p.reserve(token, job, "fake", active_charges=(str(uuid4()),))
    p.settle(first, actual_usd=None)
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        p.reserve(token, job, "fake", active_charges=(second,))
    # Already sent call can still settle after another call failed.
    assert p.settle(second, actual_usd="0.01") is None


def test_parallel_reservations_count_toward_limited_budget_and_calls(tmp_path):
    p, token, job = permit(tmp_path, budget_usd="1.00")
    first = p.reserve(token, job, "fake")
    second = p.reserve(token, job, "fake", active_charges=(first,))
    with pytest.raises(Denied, match="LOCAL_CALL_LIMIT"):
        p.reserve(token, job, "fake", active_charges=(first, second))
    p.settle(first, actual_usd="0.50")
    p.settle(second, actual_usd="0.50")
    with p.transaction() as db:
        assert p.committed(db) == Decimal("1.00")
