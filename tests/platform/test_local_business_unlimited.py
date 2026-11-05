"""Offline unlimited CLI/snapshot/driver contracts. No real key, Docker or business DB."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from scripts.local_business import __main__ as cli
from scripts.local_business import bootstrap, dispatcher
from scripts.local_business.config import save
from scripts.local_business.permit import Permit


def supported_snapshot(state):
    for name in ("permit.py", "dispatcher.py", "runtime.py", "worker.py"):
        path = state / "source/scripts/local_business" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("UNLIMITED_PROTOCOL = 1\nCONCURRENCY_PROTOCOL = 1\n")


@pytest.mark.parametrize("missing", ["permit.py", "dispatcher.py", "runtime.py"])
def test_old_snapshot_fails_before_key_or_permit_or_docker(tmp_path, monkeypatch, missing):
    supported_snapshot(tmp_path)
    (tmp_path / "source/scripts/local_business" / missing).write_text("# old limited code\n")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(cli, "owned", lambda _: {"namespace": "team4-kan159-offline"})
    forbidden = Mock(side_effect=AssertionError("No permit or Docker mutation"))
    monkeypatch.setattr(cli, "Permit", forbidden)
    monkeypatch.setattr(cli, "docker", forbidden)

    class NoKeyAccess:
        def __fspath__(self):
            pytest.fail("Key path accessed before snapshot validation")

    with pytest.raises(ValueError, match="LOCAL_UNLIMITED_SNAPSHOT_UNSUPPORTED"):
        cli.enable(tmp_path, SimpleNamespace(unlimited=True, key_file=NoKeyAccess()))
    forbidden.assert_not_called()
    assert not (tmp_path / "control").exists()


def test_completed_old_namespace_is_not_started_or_reset_by_unlimited_bootstrap(
    tmp_path, monkeypatch, capsys
):
    state = tmp_path / "old"
    state.mkdir()
    monkeypatch.setattr(bootstrap, "state_path", lambda _: state)
    monkeypatch.setattr(bootstrap, "owned", lambda _: {})
    forbidden = Mock(side_effect=AssertionError("Must preserve old namespace"))
    monkeypatch.setattr(bootstrap, "bootstrap", forbidden)
    monkeypatch.setattr(bootstrap, "enable_selected", forbidden)
    monkeypatch.setattr(
        bootstrap.os.sys,
        "argv",
        [
            "bootstrap",
            "--namespace",
            "team4-kan159-old",
            "--enable-model",
            "cs",
            "--key-file",
            "/not-read",
            "--unlimited",
        ],
    )
    assert bootstrap.main() == 1
    assert "LOCAL_UNLIMITED_SNAPSHOT_UNSUPPORTED" in capsys.readouterr().out
    forbidden.assert_not_called()


def test_supported_installed_snapshot_enables_unlimited_with_actual_journal(tmp_path, monkeypatch):
    supported_snapshot(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(
        cli,
        "owned",
        lambda _: {
            "namespace": "team4-kan159-offline",
            "owner": "fixture-owner",
            "images": {"runtime": "fixture-image"},
        },
    )
    key = tmp_path / "fake-only-key"
    key.write_text("authored fixture, not a credential")
    key.chmod(0o600)
    from backend.shared import ai

    monkeypatch.setattr(
        ai, "load_price_table", lambda _: {"fake-model": SimpleNamespace(currency="USD")}
    )
    docker = Mock(return_value=b"")
    monkeypatch.setattr(cli, "docker", docker)
    cli.enable(
        tmp_path,
        SimpleNamespace(
            unlimited=True,
            key_file=str(key),
            prices_file=str(tmp_path / "fake-prices"),
            model="fake-model",
            owner="tft",
            max_jobs=1,
            max_calls=8,
            reserve_usd="0.15",
            budget_usd="1.50",
        ),
    )
    p = Permit(tmp_path / "control/permit.sqlite3")
    with p.transaction() as db:
        assert db.execute("SELECT limit_mode FROM driver_modes").fetchone()[0] == "unlimited"
    command = docker.call_args_list[0].args
    assert command[0] == "create"
    assert f"type=bind,src={tmp_path / 'source'},dst=/workspace,readonly" in command
    assert "authored fixture, not a credential" not in " ".join(command)


@pytest.mark.parametrize("unlimited", [False, True])
def test_bootstrap_cli_forwards_limits(tmp_path, monkeypatch, unlimited):
    state = tmp_path / "new-namespace"
    monkeypatch.setattr(bootstrap, "state_path", lambda _: state)
    setup = Mock(return_value={"model_enabled": False})
    enable = Mock()
    monkeypatch.setattr(bootstrap, "bootstrap", setup)
    monkeypatch.setattr(bootstrap, "enable_selected", enable)
    argv = [
        "bootstrap",
        "--namespace",
        "team4-kan159-new",
        "--enable-model",
        "cs",
        "--key-file",
        "/not-read",
    ]
    if unlimited:
        argv += ["--unlimited"]
    monkeypatch.setattr(bootstrap.os.sys, "argv", argv)
    assert bootstrap.main() == 0
    assert enable.call_args.kwargs == {
        "max_calls": 20,
        "reserve_usd": "0.15",
        "budget_usd": "1.50",
        "unlimited": unlimited,
        "concurrent_jobs": 1,
        "concurrent_calls": 6,
    }
    assert not state.exists()


def test_direct_cli_defaults_still_select_limited_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "state_path", lambda _: tmp_path)
    enabled = Mock()
    monkeypatch.setattr(cli, "enable", enabled)
    monkeypatch.setattr(
        cli.os.sys,
        "argv",
        [
            "local",
            "enable-real",
            "--namespace",
            "team4-kan159-fixture",
            "--owner",
            "cs",
            "--key-file",
            "/not-read",
            "--prices-file",
            "/not-read",
            "--model",
            "fake",
        ],
    )
    cli.main()
    args = enabled.call_args.args[1]
    assert (args.unlimited, args.max_jobs, args.max_calls, args.reserve_usd, args.budget_usd) == (
        False,
        1,
        8,
        "0.15",
        "1.50",
    )


@pytest.mark.parametrize(
    "environment", [{"ENVIRONMENT": "production"}, {"ENVIRONMENT": "local", "K_SERVICE": ""}]
)
def test_enable_rejects_production_before_snapshot_keys_or_ledger(
    tmp_path, monkeypatch, environment
):
    monkeypatch.delenv("K_SERVICE", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    forbidden = Mock(side_effect=AssertionError("No state/key/ledger access"))
    monkeypatch.setattr(cli, "owned", forbidden)
    with pytest.raises(ValueError, match="LOCAL_RUNNER_REQUIRES_LOCAL_ENVIRONMENT"):
        cli.enable(tmp_path, SimpleNamespace(unlimited=True))
    forbidden.assert_not_called()


@pytest.mark.parametrize("entry", [cli, bootstrap])
@pytest.mark.parametrize(
    "flag,value", [("--max-jobs", "2"), ("--max-calls", "10"), ("--budget-usd", "2.00")]
)
def test_unlimited_cannot_silently_ignore_explicit_finite_flags(monkeypatch, entry, flag, value):
    argv = (
        ["local", "enable-real", "--owner", "cs", "--model", "fake", "--prices-file", "/not-read"]
        if entry is cli
        else ["bootstrap", "--enable-model", "cs"]
    )
    argv += [
        "--namespace",
        "team4-kan159-fixture",
        "--key-file",
        "/not-read",
        "--unlimited",
        flag,
        value,
    ]
    monkeypatch.setattr(entry.os.sys, "argv", argv)
    monkeypatch.setattr(entry, "state_path", Mock(side_effect=AssertionError("No state access")))
    with pytest.raises(SystemExit) as caught:
        entry.main()
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "marker",
    [
        {"mode": "real", "owner": "cs", "limit_mode": "unlimited", "remaining_jobs": None},
        {"mode": "real", "owner": "cs", "limit_mode": "unlimited"},
        {"mode": "real", "owner": "cs", "remaining_jobs": None},
        {"mode": "real", "owner": "cs", "remaining_jobs": True},
    ],
)
def test_bootstrap_unlimited_ready_needs_explicit_matching_marker(tmp_path, monkeypatch, marker):
    supported_snapshot(tmp_path)
    monkeypatch.setattr(bootstrap, "state_path", lambda _: tmp_path)
    monkeypatch.setattr(bootstrap, "owned", lambda _: {})
    monkeypatch.setattr(bootstrap, "no_active_driver", lambda _: None)
    monkeypatch.setattr(bootstrap, "running_drivers", lambda _: ["fake-driver"])
    clock = iter([0, 1, 20])
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    def command(args, *a):
        if "enable-real" in args:
            save(
                tmp_path / "control/enabled.json",
                {**marker, "concurrent_jobs": 1, "concurrent_calls": 6},
            )

    commands = Mock(side_effect=command)
    monkeypatch.setattr(bootstrap, "command", commands)
    if "limit_mode" in marker and "remaining_jobs" in marker:
        bootstrap.enable_selected(
            "team4-kan159-fixture", "cs", Path("/not-read"), 1, unlimited=True
        )
        args = commands.call_args.args[0]
        assert (
            "--unlimited" in args
            and "--max-jobs" not in args
            and "--max-calls" not in args
            and "--budget-usd" not in args
        )
    else:
        with pytest.raises(ValueError, match="MODEL_DRIVER_NOT_READY"):
            bootstrap.enable_selected(
                "team4-kan159-fixture", "cs", Path("/not-read"), 1, unlimited=True
            )
        assert "disable-real" in commands.call_args.args[0]


@pytest.mark.parametrize(
    "timeout_seconds,elapsed,interrupted",
    [(None, 1799, False), (None, 1801, True), ("7200", 1801, False), ("7200", 7201, True)],
)
@pytest.mark.parametrize("unlimited,expected_jobs", [(False, 1), (True, 3)])
def test_dispatcher_until_explicit_stop_uses_actual_permit(
    tmp_path, monkeypatch, unlimited, expected_jobs, timeout_seconds, elapsed, interrupted
):
    if timeout_seconds is None:
        monkeypatch.delenv("LOCAL_WORKER_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("LOCAL_WORKER_TIMEOUT_SECONDS", timeout_seconds)
    monotonic = iter([v for n in range(3) for v in (n * 10000, n * 10000 + elapsed)])
    monkeypatch.setattr(
        dispatcher, "time", SimpleNamespace(monotonic=lambda: next(monotonic), sleep=lambda _: None)
    )
    control = tmp_path / "control"
    control.mkdir()
    credentials = tmp_path / "fake-credentials.json"
    credentials.write_text(json.dumps({"platform_app": "fake-dsn", "local_tft_worker": "fake-dsn"}))
    p = Permit(control / "permit.sqlite3")
    token = p.authorize(max_jobs=1, max_calls=1, reserve_usd="0.15", unlimited=unlimited)
    for k, v in {
        "ENVIRONMENT": "local",
        "LOCAL_BUSINESS_STATE": str(tmp_path),
        "LOCAL_BUSINESS_CONTROL": str(control),
        "LOCAL_JOB_OWNER": "tft",
        "LOCAL_DRIVER_TOKEN": token,
        "LOCAL_MODEL": "fake-only",
        "PATH": "/usr/bin:/bin",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(
        dispatcher,
        "Path",
        lambda v: credentials if str(v) == "/local/secrets/worker-db.json" else Path(v),
    )
    monkeypatch.setattr(dispatcher, "Settings", lambda **kw: object())
    signals = {}
    monkeypatch.setattr(dispatcher.signal, "signal", lambda s, f: signals.setdefault(s, f))

    @contextmanager
    def connect(*a, **kw):
        yield SimpleNamespace(
            execute=lambda _: SimpleNamespace(fetchone=lambda: (datetime(2026, 1, 1, tzinfo=UTC),))
        )

    monkeypatch.setattr(dispatcher.psycopg, "connect", connect)
    jobs = [{"id": uuid4()} for _ in range(3)]

    def candidate(*args):
        if jobs:
            return jobs.pop(0)
        signals[dispatcher.signal.SIGTERM](None, None)
        return None

    monkeypatch.setattr(dispatcher, "candidate", candidate)
    monkeypatch.setattr(dispatcher, "claim_send", AsyncMock(return_value=object()))
    monkeypatch.setattr(dispatcher, "sent", AsyncMock())
    monkeypatch.setattr(dispatcher.time, "sleep", lambda _: None)
    heartbeat = []

    def marker(path, data):
        if path.name == "enabled.json":
            heartbeat.append(data.copy())
        save(path, data)

    monkeypatch.setattr(dispatcher, "save", marker)

    class Child:
        def __init__(self, *args, **kw):
            self.pid = dispatcher.os.getpid()
            self.job = kw["env"]["LOCAL_JOB_ID"]
            self.polls = iter([None, 0])
            self.charged = False

        def poll(self):
            # Parent registration precedes any worker SDK reservation.
            if not self.charged:
                charge = p.reserve(token, self.job, "fake-only")
                assert p.settle(charge, actual_usd="0.01") is None
                self.charged = True
            return next(self.polls)

        def terminate(self):
            assert interrupted

        def wait(self, **kw):
            return 0

    monkeypatch.setattr(dispatcher.subprocess, "Popen", Child)
    if interrupted:
        with pytest.raises(SystemExit, match="LOCAL_WORKER_INTERRUPTED_REVIEW_REQUIRED"):
            dispatcher.main()
        expected_jobs = 1
    else:
        dispatcher.main()
    with p.transaction() as db:
        assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == expected_jobs
        assert (
            db.execute("SELECT count(*) FROM charges WHERE state='SETTLED'").fetchone()[0]
            == expected_jobs
        )
        assert db.execute("SELECT token FROM driver").fetchone()[0] is None
    assert len(heartbeat) >= expected_jobs * 2
    assert all(v["limit_mode"] == ("unlimited" if unlimited else "limited") for v in heartbeat)
    if unlimited:
        assert all(v["remaining_jobs"] is None for v in heartbeat)
    else:
        assert heartbeat[0]["remaining_jobs"] == 1
    assert not (control / "enabled.json").exists()


@pytest.mark.parametrize("value", ["", "0", "-1", "1.5", "true", " 7200", "inf"])
def test_dispatcher_rejects_invalid_duration_before_credentials(monkeypatch, value):
    monkeypatch.setattr(dispatcher, "local_only", lambda: None)
    monkeypatch.setenv("LOCAL_WORKER_TIMEOUT_SECONDS", value)
    monkeypatch.setattr(dispatcher, "Path", lambda _: pytest.fail("No private file access"))
    with pytest.raises(ValueError, match="LOCAL_WORKER_TIMEOUT_INVALID"):
        dispatcher.main()
