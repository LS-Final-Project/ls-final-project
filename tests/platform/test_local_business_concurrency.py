"""Offline fake SDK plus real SQLite/process races. Never opens provider clients or DBs."""

import multiprocessing
import os
import queue
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from backend.shared import ai
from scripts.local_business.permit import Busy, Denied, Permit, validate_concurrency
from scripts.local_business.worker import Configuration, Meter


def authorization(tmp_path, *, jobs=2, calls=2):
    permit = Permit(tmp_path / "permit.sqlite3")
    token = permit.authorize(
        max_jobs=20,
        max_calls=100,
        reserve_usd="0.01",
        unlimited=True,
        concurrent_jobs=jobs,
        concurrent_calls=calls,
    )
    return permit, token


def registered(permit, token):
    job = str(uuid4())
    permit.register_job(token, job)
    permit.grant(token, job)
    permit.register_worker(token, job, os.getpid())
    return job


def meter(permit, token, job, owner="tft"):
    prices = {
        "fake": ai.ModelPrice(
            input_per_million=Decimal("1"),
            output_per_million=Decimal("2"),
            cached_input_per_million=Decimal("0.5"),
            currency="USD",
            as_of=date(2026, 11, 3),
        )
    }
    config = Configuration(
        owner, UUID(job), token, permit.path.parent, Path("/no-key"), Path("/no-price")
    )
    return Meter(permit, config, prices)


def response():
    return SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=1,
            candidates_token_count=1,
            thoughts_token_count=0,
            total_token_count=2,
            cached_content_token_count=0,
        )
    )


def _process_calls(path, token, job, release, entered, active, peak, lock):
    permit = Permit(Path(path))
    current = meter(permit, token, job)

    def sdk(**_):
        with lock:
            active.value += 1
            peak.value = max(peak.value, active.value)
        entered.put(job)
        try:
            if not release.wait(10):
                raise TimeoutError("offline test gate")
            return response()
        finally:
            with lock:
                active.value -= 1

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(current.generate, sdk, model="fake") for _ in range(2)]
        for future in futures:
            future.result(timeout=12)


@pytest.mark.parametrize(("jobs", "cap"), [(2, 1), (6, 6)])
def test_multiple_process_workers_share_atomic_global_cap(tmp_path, jobs, cap):
    ctx = multiprocessing.get_context("fork")
    permit, token = authorization(tmp_path, jobs=jobs, calls=cap)
    release, entered, lock = ctx.Event(), ctx.Queue(), ctx.Lock()
    active, peak = ctx.Value("i", 0), ctx.Value("i", 0)
    processes = []
    try:
        for _ in range(jobs):
            job = str(uuid4())
            permit.register_job(token, job)
            permit.grant(token, job)
            process = ctx.Process(
                target=_process_calls,
                args=(str(permit.path), token, job, release, entered, active, peak, lock),
            )
            process.start()
            processes.append((job, process))
            permit.register_worker(token, job, process.pid)
        for _ in range(cap):
            entered.get(timeout=8)
        with pytest.raises(queue.Empty):
            entered.get(timeout=0.15)
        with permit.transaction() as db:
            assert (
                db.execute("SELECT count(*) FROM charges WHERE state='RESERVED'").fetchone()[0]
                == cap
            )
        permit.check_ready(token)  # Live reservations from several jobs are healthy.
        release.set()
        for job, process in processes:
            process.join(12)
            assert process.exitcode == 0
            permit.finish_job(token, job)
        assert peak.value <= cap
        with permit.transaction() as db:
            assert (
                db.execute("SELECT count(*) FROM charges WHERE state='SETTLED'").fetchone()[0]
                == jobs * 2
            )
            assert not db.execute("SELECT 1 FROM charges WHERE state!='SETTLED'").fetchone()
    finally:
        release.set()
        for _, process in processes:
            if process.is_alive():
                process.terminate()
                process.join(3)


def test_dynamic_lower_cap_waits_without_cancelling_inflight_calls(tmp_path):
    permit, token = authorization(tmp_path, jobs=3, calls=2)
    jobs = [registered(permit, token) for _ in range(3)]
    meters = [meter(permit, token, job) for job in jobs]
    entered, releases = [Event() for _ in jobs], [Event() for _ in jobs]

    def sdk(index):
        def generate(**_):
            entered[index].set()
            assert releases[index].wait(5)
            return response()

        return generate

    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(meters[i].generate, sdk(i), model="fake") for i in range(2)]
        assert entered[0].wait(3) and entered[1].wait(3)
        permit.update_concurrency(token, concurrent_jobs=1, concurrent_calls=1)
        futures.append(pool.submit(meters[2].generate, sdk(2), model="fake"))
        assert not entered[2].wait(0.1)
        releases[0].set()
        futures[0].result(3)
        assert not entered[2].wait(0.1)  # One existing reservation still fills the lowered cap.
        releases[1].set()
        futures[1].result(3)
        assert entered[2].wait(3)
        releases[2].set()
        futures[2].result(3)
    with permit.transaction() as db:
        assert db.execute("SELECT count(*) FROM charges WHERE state='SETTLED'").fetchone()[0] == 3


def test_halt_and_unknown_stop_new_admission_but_allow_existing_settlement(tmp_path):
    permit, token = authorization(tmp_path)
    first, second = registered(permit, token), registered(permit, token)
    a = permit.reserve(token, first, "fake")
    b = permit.reserve(token, second, "fake")
    permit.settle(a, actual_usd=None)
    for action in (
        lambda: permit.reserve(token, second, "fake", active_charges=(b,)),
        lambda: permit.grant(token, str(uuid4())),
        lambda: permit.update_concurrency(token, concurrent_jobs=3, concurrent_calls=3),
    ):
        with pytest.raises(Denied):
            action()
    permit.halt(token)
    assert permit.settle(b, actual_usd="0.001") is None
    permit.close_driver(token)
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        permit.authorize(max_jobs=1, max_calls=1, reserve_usd="0.01")
    with permit.transaction() as db:
        assert [(r["state"], r["reserved"]) for r in db.execute("SELECT * FROM charges")] == [
            ("UNKNOWN", "0.01"),
            ("SETTLED", "0.01"),
        ]


def _crash_reservation(path, token, job, reserved):
    permit = Permit(Path(path))
    while True:
        try:
            permit.check_worker(token, job)
            break
        except Busy:
            time.sleep(0.01)
    reserved.put(permit.reserve(token, job, "fake"))
    time.sleep(30)


def test_dead_worker_reservation_survives_finish_restart_and_reenable(tmp_path):
    ctx = multiprocessing.get_context("fork")
    permit, token = authorization(tmp_path)
    job = str(uuid4())
    permit.register_job(token, job)
    permit.grant(token, job)
    reserved = ctx.Queue()
    process = ctx.Process(target=_crash_reservation, args=(str(permit.path), token, job, reserved))
    process.start()
    try:
        permit.register_worker(token, job, process.pid)
        charge = reserved.get(timeout=5)
    finally:
        process.terminate()
        process.join(3)
    with pytest.raises(Denied):
        permit.check_ready(token)
    permit.finish_job(token, job)
    permit.close_driver(token)
    reopened = Permit(permit.path)
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        reopened.authorize(max_jobs=1, max_calls=1, reserve_usd="0.01")
    with reopened.transaction() as db:
        assert (
            db.execute("SELECT state FROM charges WHERE id=?", (charge,)).fetchone()[0]
            == "RESERVED"
        )


def test_only_own_exact_active_charge_set_is_accepted(tmp_path):
    permit, token = authorization(tmp_path, calls=6)
    a, b = registered(permit, token), registered(permit, token)
    first = permit.reserve(token, a, "fake")
    second = permit.reserve(token, b, "fake")
    with pytest.raises(Denied):
        permit.reserve(token, a, "fake", active_charges=(first, second))
    with pytest.raises(Denied):
        permit.reserve(token, a, "fake")
    assert permit.reserve(token, a, "fake", active_charges=(first,))


def test_job_slots_pending_and_legacy_charge_boundary(tmp_path):
    permit, token = authorization(tmp_path, jobs=1)
    job = str(uuid4())
    permit.register_job(token, job)
    with pytest.raises(Denied, match="LOCAL_JOB_NOT_AUTHORIZED"):
        permit.check_worker(token, job)
    permit.grant(token, job)
    with pytest.raises(Busy, match="LOCAL_WORKER_STARTING"):
        permit.check_worker(token, job)
    with pytest.raises(Busy, match="LOCAL_JOB_SLOTS_BUSY"):
        permit.register_job(token, str(uuid4()))
    permit.finish_job(token, job)
    legacy = str(uuid4())
    permit.grant(token, legacy)
    permit.reserve(token, legacy, "fake")
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        permit.reserve(token, legacy, "fake")


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "2"])
def test_concurrency_rejects_non_positive_integer_without_writes(value, tmp_path):
    with pytest.raises(ValueError):
        validate_concurrency(value, 1)
    with pytest.raises(ValueError):
        validate_concurrency(1, value)
    permit = Permit(tmp_path / "permit.sqlite3")
    with pytest.raises(ValueError):
        permit.authorize(max_jobs=1, max_calls=1, reserve_usd=".01", concurrent_jobs=value)
    with permit.transaction() as db:
        assert not db.execute("SELECT 1 FROM driver").fetchone()


def test_additive_schema_preserves_legacy_driver_grants_and_costs(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    token, job = str(uuid4()), str(uuid4())
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE driver(singleton INTEGER PRIMARY KEY,token TEXT,job_limit INTEGER,call_limit INTEGER,reservation TEXT,budget TEXT); CREATE TABLE grants(job_id TEXT PRIMARY KEY,token TEXT); CREATE TABLE charges(id TEXT PRIMARY KEY,token TEXT,job_id TEXT,model TEXT,reserved TEXT,actual TEXT,state TEXT,usage TEXT,reason TEXT);"
        )
        db.execute("INSERT INTO driver VALUES(1,?,1,2,'0.1','1')", (token,))
        db.execute("INSERT INTO grants VALUES(?,?)", (job, token))
    permit = Permit(path)
    with permit.transaction() as db:
        row = permit.driver(db, token)
        assert (row["concurrent_jobs"], row["concurrent_calls"]) == (1, 6)
    charge = permit.reserve(token, job, "fake")
    assert permit.settle(charge, actual_usd="0.01") is None
    permit.update_concurrency(token, concurrent_jobs=2, concurrent_calls=2)
    with permit.transaction() as db:
        assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == 1
        assert db.execute("SELECT actual FROM charges").fetchone()[0] == "0.01"


def test_host_configuration_update_never_interprets_container_pid(tmp_path, monkeypatch):
    permit, token = authorization(tmp_path)
    registered(permit, token)
    monkeypatch.setattr(
        "scripts.local_business.permit.process_identity",
        lambda _: pytest.fail("host must not read worker PID namespace"),
    )
    permit.update_concurrency(token, concurrent_jobs=6, concurrent_calls=6)


def test_other_job_inflight_settles_after_provider_failure_and_new_calls_stop(tmp_path):
    from scripts.local_business.worker import LocalStop

    permit, token = authorization(tmp_path)
    failing = meter(permit, token, registered(permit, token))
    inflight = meter(permit, token, registered(permit, token))
    both_entered, failed = Event(), Event()

    def fail_sdk(**_):
        assert both_entered.wait(3)
        raise TimeoutError("offline timeout")

    def known_sdk(**_):
        both_entered.set()
        assert failed.wait(3)
        return response()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(failing.generate, fail_sdk, model="fake")
        second = pool.submit(inflight.generate, known_sdk, model="fake")
        with pytest.raises(LocalStop, match="LOCAL_PROVIDER_CALL_FAILED"):
            first.result(4)
        failed.set()
        second.result(4)
    with pytest.raises(LocalStop, match="LOCAL_RESERVATION_DENIED"):
        inflight.generate(lambda **_: pytest.fail("new SDK call forbidden"), model="fake")
    with permit.transaction() as db:
        assert sorted(r[0] for r in db.execute("SELECT state FROM charges")) == [
            "SETTLED",
            "UNKNOWN",
        ]


def test_raising_live_cap_admits_waiting_call_without_reauthorizing(tmp_path):
    permit, token = authorization(tmp_path, calls=1)
    first, second = [meter(permit, token, registered(permit, token)) for _ in range(2)]
    entered, release, second_entered = Event(), Event(), Event()

    def sdk(**_):
        entered.set()
        assert release.wait(4)
        return response()

    def second_sdk(**_):
        second_entered.set()
        return response()

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(first.generate, sdk, model="fake")
        assert entered.wait(3)
        b = pool.submit(second.generate, second_sdk, model="fake")
        assert not second_entered.wait(0.1)
        permit.update_concurrency(token, concurrent_jobs=2, concurrent_calls=2)
        assert second_entered.wait(3)
        b.result(3)
        release.set()
        a.result(3)
