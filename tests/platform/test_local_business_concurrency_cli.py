"""Host CLI scheduling updates use a verified driver and never open credentials."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.local_business import __main__ as cli
from scripts.local_business import bootstrap
from scripts.local_business.permit import Permit


def snapshot(state):
    for name in ("permit", "dispatcher", "worker", "runtime"):
        path = state / "source/scripts/local_business" / (name + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("CONCURRENCY_PROTOCOL = 1\nUNLIMITED_PROTOCOL = 1\n")


@pytest.mark.parametrize("missing", ["permit", "dispatcher", "worker", "runtime"])
def test_missing_concurrency_boundary_rejects_before_key_or_ledger(tmp_path, monkeypatch, missing):
    snapshot(tmp_path)
    (tmp_path / "source/scripts/local_business" / (missing + ".py")).write_text("# legacy\n")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(cli, "owned", lambda _: {"namespace": "fixture"})
    forbidden = Mock(side_effect=AssertionError("No key/ledger/Docker access"))
    monkeypatch.setattr(cli, "Permit", forbidden)
    monkeypatch.setattr(cli, "docker", forbidden)
    with pytest.raises(ValueError, match="LOCAL_CONCURRENCY_SNAPSHOT_UNSUPPORTED"):
        cli.enable(tmp_path, SimpleNamespace(owner="tft", concurrent_jobs=6, concurrent_calls=6))
    forbidden.assert_not_called()


def fixture_driver(state, monkeypatch):
    snapshot(state)
    p = Permit(state / "control/permit.sqlite3")
    token = p.authorize(max_jobs=1, max_calls=8, reserve_usd="0.15", unlimited=True)
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setattr(cli, "owned", lambda _: {"images": {"runtime": "fixture-image"}})
    monkeypatch.setattr(cli, "running_drivers", lambda _: ["fixture-driver"])
    row = {
        "State": {"Running": True},
        "Image": "fixture-image",
        "Config": {
            "Cmd": [
                "LOCAL_DRIVER_TOKEN=" + token,
                "LOCAL_JOB_OWNER=tft",
                "python",
                "-m",
                "scripts.local_business.dispatcher",
            ]
        },
        "Mounts": [
            {"Source": str(state / "control"), "Destination": "/local/control", "RW": True},
            {"Source": str(state / "source"), "Destination": "/workspace", "RW": False},
            {
                "Source": str(state / "settings.json"),
                "Destination": "/local/state/settings.json",
                "RW": False,
            },
        ],
    }
    monkeypatch.setattr(cli, "inspect", lambda _: row)
    return p, token, row


def test_live_cli_changes_only_caps_without_key_restart_or_new_token(tmp_path, monkeypatch, capsys):
    p, token, _ = fixture_driver(tmp_path, monkeypatch)
    with p.transaction() as db:
        before = [tuple(r) for r in db.execute("SELECT * FROM driver")]
    monkeypatch.setattr(cli, "docker", Mock(side_effect=AssertionError("No restart/create")))
    cli.set_concurrency(tmp_path, SimpleNamespace(concurrent_jobs=6, concurrent_calls=6))
    cli.set_concurrency(tmp_path, SimpleNamespace(concurrent_jobs=2, concurrent_calls=1))
    with p.transaction() as db:
        row = p.driver(db, token)
        assert (row["concurrent_jobs"], row["concurrent_calls"]) == (2, 1)
        assert [tuple(r) for r in db.execute("SELECT * FROM driver")] == before
        assert db.execute("SELECT count(*) FROM charges").fetchone()[0] == 0
    assert token not in capsys.readouterr().out


@pytest.mark.parametrize(
    "bad", ["image", "mount", "source", "source_rw", "settings", "command", "cs"]
)
def test_cli_rejects_wrong_driver_or_parallel_cs(tmp_path, monkeypatch, bad):
    p, token, row = fixture_driver(tmp_path, monkeypatch)
    if bad == "image":
        row["Image"] = "other"
    elif bad == "mount":
        row["Mounts"][0]["Source"] = "/other/control"
    elif bad == "source":
        row["Mounts"][1]["Source"] = "/old-snapshot/source"
    elif bad == "source_rw":
        row["Mounts"][1]["RW"] = True
    elif bad == "settings":
        row["Mounts"][2]["Source"] = "/other/settings.json"
    elif bad == "command":
        row["Config"]["Cmd"][-1] = "another.module"
    else:
        row["Config"]["Cmd"][1] = "LOCAL_JOB_OWNER=cs"
    with pytest.raises(ValueError, match="LOCAL_"):
        cli.set_concurrency(tmp_path, SimpleNamespace(concurrent_jobs=6, concurrent_calls=6))
    with p.transaction() as db:
        assert p.driver(db, token)["concurrent_jobs"] == 1


@pytest.mark.parametrize("module", [cli, bootstrap])
def test_cli_forwards_explicit_six_six(monkeypatch, tmp_path, module):
    monkeypatch.setattr(module, "state_path", lambda _: tmp_path / "not-created")
    if module is cli:
        target = Mock()
        monkeypatch.setattr(cli, "set_concurrency", target)
        argv = ["local", "set-concurrency"]
    else:
        monkeypatch.setattr(bootstrap, "bootstrap", Mock(return_value={}))
        target = Mock()
        monkeypatch.setattr(bootstrap, "enable_selected", target)
        argv = ["bootstrap", "--enable-model", "tft", "--key-file", "/not-read", "--unlimited"]
    argv += [
        "--namespace",
        "team4-kan159-fixture",
        "--concurrent-jobs",
        "6",
        "--concurrent-calls",
        "6",
    ]
    monkeypatch.setattr(module.os.sys, "argv", argv)
    module.main()
    if module is cli:
        args = target.call_args.args[1]
        assert (args.concurrent_jobs, args.concurrent_calls) == (6, 6)
    else:
        assert target.call_args.kwargs["concurrent_jobs"] == 6
        assert target.call_args.kwargs["concurrent_calls"] == 6
