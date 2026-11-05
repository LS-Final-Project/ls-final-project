"""Offline dispatcher children with the real SQLite execution/cost journal."""

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from scripts.local_business import dispatcher
from scripts.local_business.permit import Busy, Denied, Permit


class Harness:
    def __init__(self, tmp_path, monkeypatch, *, jobs=3, capacity=2):
        self.control = tmp_path / "control"
        self.control.mkdir()
        credentials = tmp_path / "credentials.json"
        credentials.write_text(json.dumps({"platform_app": "fake", "local_tft_worker": "fake"}))
        self.permit = Permit(self.control / "permit.sqlite3")
        self.token = self.permit.authorize(
            max_jobs=jobs,
            max_calls=jobs,
            reserve_usd="0.01",
            budget_usd="1.00",
            concurrent_jobs=capacity,
            concurrent_calls=6,
        )
        self.ids = [str(uuid4()) for _ in range(jobs)]
        self.queue = self.ids.copy()
        self.children = []
        self.events = []
        self.markers = []
        self.signals = {}
        self.capacity = capacity
        self.max_live = 0
        self.max_reserved = 0
        self.mode = "success"
        self.fail_job = None
        self.unknown_job = None
        self.tick = 0
        self.delayed_job = None
        self.on_settle = lambda job: None
        for key, value in {
            "LOCAL_BUSINESS_STATE": str(tmp_path),
            "LOCAL_BUSINESS_CONTROL": str(self.control),
            "LOCAL_JOB_OWNER": "tft",
            "LOCAL_DRIVER_TOKEN": self.token,
            "LOCAL_MODEL": "offline",
            "PATH": "/usr/bin:/bin",
        }.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(dispatcher, "local_only", lambda: None)
        monkeypatch.setattr(dispatcher, "Settings", lambda **kw: object())
        monkeypatch.setattr(
            dispatcher,
            "Path",
            lambda p: credentials if str(p) == "/local/secrets/worker-db.json" else Path(p),
        )
        monkeypatch.setattr(
            dispatcher.signal, "signal", lambda sig, fn: self.signals.setdefault(sig, fn)
        )

        @contextmanager
        def connect(*args, **kwargs):
            yield SimpleNamespace(
                execute=lambda sql: SimpleNamespace(
                    fetchone=lambda: (datetime(2026, 1, 1, tzinfo=UTC),)
                )
            )

        monkeypatch.setattr(dispatcher.psycopg, "connect", connect)
        monkeypatch.setattr(dispatcher, "candidate", self.candidate)

        async def claim(settings, job):
            assert self.queue.pop(0) == job
            return object()

        self.claim = AsyncMock(side_effect=claim)
        monkeypatch.setattr(dispatcher, "claim_send", self.claim)
        monkeypatch.setattr(dispatcher, "sent", AsyncMock())
        monkeypatch.setattr(dispatcher.subprocess, "Popen", self.spawn)
        original_save = dispatcher.save

        def save(path, value):
            if path.name == "enabled.json":
                self.markers.append(value)
            original_save(path, value)

        monkeypatch.setattr(dispatcher, "save", save)
        monkeypatch.setattr(
            dispatcher, "time", SimpleNamespace(monotonic=lambda: self.tick, sleep=self.sleep)
        )

    def candidate(self, settings, owner, after):
        assert owner == "tft" and after.tzinfo is not None
        return {"id": self.queue[0]} if self.queue else None

    def sleep(self, seconds):
        self.tick += 1
        if self.tick > 30:
            raise AssertionError("dispatcher did not terminate")

    def spawn(self, argv, **kwargs):
        harness = self
        job = kwargs["env"]["LOCAL_JOB_ID"]
        assert kwargs["env"]["TFT_JOB_ID"] == job
        assert argv[-1] == "scripts.local_business.worker"

        class Child:
            pid = os.getpid()  # Real live identity; only execution itself is fake.
            charge = None
            done = False
            terminated = False
            killed = False

            def poll(self):
                if self.done:
                    return 0
                if self.charge is None:
                    self.charge = harness.permit.reserve(harness.token, job, "offline")
                    with harness.permit.transaction() as db:
                        count = db.execute(
                            "SELECT count(*) FROM charges WHERE state='RESERVED'"
                        ).fetchone()[0]
                    harness.max_reserved = max(harness.max_reserved, count)
                    return None
                if len(harness.children) < harness.capacity:
                    return None
                with harness.permit.transaction() as db:
                    charged = db.execute("SELECT count(*) FROM charges").fetchone()[0]
                if charged < harness.capacity:
                    return None
                if harness.mode == "signal" and not self.terminated:
                    harness.signals[dispatcher.signal.SIGTERM](None, None)
                    return None
                if harness.mode == "timeout":
                    return None
                if job == harness.delayed_job and harness.tick < 2:
                    return None
                # Failure is observed while a different worker still has a live call.
                actual = None if job == harness.unknown_job else "0.001"
                harness.permit.settle(self.charge, actual_usd=actual)
                self.done = True
                harness.events.append(("settled", job))
                harness.on_settle(job)
                if job != harness.fail_job and harness.fail_job is not None:
                    with harness.permit.transaction() as db:
                        assert harness.permit.driver(db, harness.token)["halted"] == 1
                    assert not (harness.control / "enabled.json").exists()
                return 1 if job == harness.fail_job else 0

            def terminate(self):
                self.terminated = True
                harness.events.append(("terminated", job))

            def kill(self):
                self.killed = True
                harness.events.append(("killed", job))

            def wait(self, **kwargs):
                if self.terminated and harness.mode == "timeout" and not self.killed:
                    raise dispatcher.subprocess.TimeoutExpired("offline", 10)
                assert self.done or self.terminated
                harness.events.append(("wait", job))
                self.done = True
                return 1 if job == harness.fail_job else 0

        child = Child()
        child.log = kwargs["stdout"]
        self.children.append(child)
        self.events.append(("spawn", job))
        self.max_live = max(self.max_live, sum(not c.done for c in self.children))
        return child

    def assert_closed(self):
        assert all(child.done and child.log.closed for child in self.children)
        with self.permit.transaction() as db:
            assert db.execute("SELECT token FROM driver").fetchone()[0] is None
            assert (
                db.execute("SELECT count(*) FROM executions WHERE state!='FINISHED'").fetchone()[0]
                == 0
            )
        assert not (self.control / "enabled.json").exists()


def test_six_jobs_share_six_global_call_slots_and_targeted_claims(tmp_path, monkeypatch):
    harness = Harness(tmp_path, monkeypatch, jobs=6, capacity=6)
    dispatcher.main()
    assert harness.max_live == 6
    assert harness.max_reserved == 6
    assert [call.args[1] for call in harness.claim.call_args_list] == harness.ids
    assert max(value["active_jobs"] for value in harness.markers) == 6
    assert harness.markers[0]["remaining_jobs"] == 6
    assert harness.markers[-1]["remaining_jobs"] == 0
    harness.assert_closed()


@pytest.mark.parametrize("unknown", [False, True])
def test_failure_halts_new_jobs_but_other_inflight_call_settles(tmp_path, monkeypatch, unknown):
    harness = Harness(tmp_path, monkeypatch)
    if unknown:
        harness.unknown_job = harness.ids[0]
    else:
        harness.fail_job = harness.ids[0]
    with pytest.raises(SystemExit):
        dispatcher.main()
    assert len(harness.children) == 2
    assert not any(child.terminated for child in harness.children)
    with harness.permit.transaction() as db:
        assert db.execute("SELECT count(*) FROM charges WHERE state='SETTLED'").fetchone()[0] == (
            1 if unknown else 2
        )
        assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == 2
    harness.assert_closed()


@pytest.mark.parametrize("mode", ["signal", "timeout"])
def test_stop_reaps_every_child_closes_logs_and_keeps_unfinished_reservations(
    tmp_path, monkeypatch, mode
):
    harness = Harness(tmp_path, monkeypatch)
    harness.mode = mode
    monkeypatch.setenv("LOCAL_WORKER_TIMEOUT_SECONDS", "2")
    with pytest.raises(SystemExit, match="LOCAL_WORKER_INTERRUPTED_REVIEW_REQUIRED"):
        dispatcher.main()
    assert len(harness.children) == 2
    assert all(child.terminated for child in harness.children)
    if mode == "timeout":
        assert all(child.killed for child in harness.children)
    with harness.permit.transaction() as db:
        assert db.execute("SELECT count(*) FROM charges WHERE state='RESERVED'").fetchone()[0] == 2
    harness.assert_closed()


def test_temporary_job_slot_busy_does_not_consume_a_grant_or_skip_the_candidate(
    tmp_path, monkeypatch
):
    harness = Harness(tmp_path, monkeypatch, jobs=2)
    original = Permit.register_job
    attempts = []

    def register(permit, token, job):
        attempts.append(job)
        if len(attempts) == 1:
            raise Busy("LOCAL_JOB_SLOTS_BUSY")
        return original(permit, token, job)

    monkeypatch.setattr(Permit, "register_job", register)
    dispatcher.main()
    assert attempts[:2] == [harness.ids[0], harness.ids[0]]
    with harness.permit.transaction() as db:
        assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == 2
    harness.assert_closed()


def test_lowered_job_cap_drains_existing_jobs_without_cancelling_them(tmp_path, monkeypatch):
    harness = Harness(tmp_path, monkeypatch)
    harness.delayed_job = harness.ids[1]

    def lower(job):
        if job == harness.ids[0]:
            harness.permit.update_concurrency(harness.token, concurrent_jobs=1, concurrent_calls=6)

    harness.on_settle = lower
    dispatcher.main()
    assert harness.events.index(("spawn", harness.ids[2])) > harness.events.index(
        ("wait", harness.ids[1])
    )
    assert not any(child.terminated for child in harness.children)
    assert any(value["concurrent_jobs"] == 1 for value in harness.markers)
    harness.assert_closed()


def test_raised_job_cap_is_observed_without_restarting_the_driver(tmp_path, monkeypatch):
    harness = Harness(tmp_path, monkeypatch, jobs=2)
    harness.permit.update_concurrency(harness.token, concurrent_jobs=1, concurrent_calls=6)
    original_sleep = harness.sleep

    def raise_cap(seconds):
        original_sleep(seconds)
        harness.permit.update_concurrency(harness.token, concurrent_jobs=2, concurrent_calls=6)

    monkeypatch.setattr(dispatcher.time, "sleep", raise_cap)
    dispatcher.main()
    assert harness.max_live == 2
    assert harness.markers[0]["concurrent_jobs"] == 1
    assert any(value["concurrent_jobs"] == 2 for value in harness.markers)
    harness.assert_closed()


def test_claim_send_always_passes_the_selected_uuid_to_runtime_claim(monkeypatch):
    job = str(uuid4())
    claim = AsyncMock(return_value=None)
    monkeypatch.setattr(dispatcher.dispatch, "claim_next", claim)
    monkeypatch.setattr(dispatcher, "job_settings", lambda: (object(), object()))
    with pytest.raises(Denied, match="LOCAL_DISPATCH_NOT_CLAIMED"):
        dispatcher.asyncio.run(dispatcher.claim_send(object(), job))
    assert str(claim.call_args.kwargs["job_id"]) == job


def test_spawn_failure_records_unknown_dispatch_and_drains_other_calls(tmp_path, monkeypatch):
    harness = Harness(tmp_path, monkeypatch)
    original_spawn = harness.spawn

    def spawn(argv, **kwargs):
        if kwargs["env"]["LOCAL_JOB_ID"] == harness.ids[1]:
            harness.capacity = 1
            raise OSError("private launch detail")
        return original_spawn(argv, **kwargs)

    monkeypatch.setattr(dispatcher.subprocess, "Popen", spawn)
    with pytest.raises(SystemExit, match="LOCAL_DRIVER_FAILED"):
        dispatcher.main()
    assert len(harness.children) == 1
    assert not harness.children[0].terminated
    assert dispatcher.sent.call_args_list[-1].args[-1] is False
    assert "private launch detail" not in (harness.control / "driver-result.json").read_text()
    with harness.permit.transaction() as db:
        assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM charges WHERE state='SETTLED'").fetchone()[0] == 1
    harness.assert_closed()
