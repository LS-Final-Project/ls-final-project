"""Offline Docker and synthetic PG directory fakes; never start a real DB or container."""

import copy
import json
import subprocess
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from scripts.local_business import stack

NS = "team4-kan159-resume-offline"
VOLUME = NS + "-pgdata"
IMAGE = "sha256:" + "a" * 64
PGDATA = "/var/lib/postgresql/data"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    config = {
        "namespace": NS,
        "owner": "offline-owner",
        "images": {"pg": IMAGE},
        "ports": {"web": 38443},
    }
    pg = {
        "Id": "our-pg-id",
        "State": {"Running": False},
        "Image": IMAGE,
        "Config": {"Labels": {"team4.local.owner": config["owner"]}, "Env": ["PGDATA=" + PGDATA]},
        "Mounts": [{"Type": "volume", "Name": VOLUME, "Destination": PGDATA, "RW": True}],
        "NetworkSettings": {"Networks": {NS + "-internal": {}}},
    }
    volume = {
        "Name": VOLUME,
        "Labels": {
            "team4.local.owner": config["owner"],
            "team4.local.namespace": NS,
        },
    }
    data = tmp_path / "synthetic-pgdata"
    data.mkdir()
    (data / "PG_VERSION").write_text("17\n")
    (data / "base").mkdir()
    (data / "global").mkdir()
    marker = tmp_path / "control/enabled.json"
    marker.parent.mkdir()
    marker.write_text("preserve until resume is safe")
    state = SimpleNamespace(
        config=config,
        pg=pg,
        volume=volume,
        data=data,
        marker=marker,
        calls=[],
        peers={},
        probe=None,
        missing_volume=False,
    )

    def inspect(name):
        if name == NS + "-pg":
            return copy.deepcopy(state.pg)
        if name in state.peers:
            return copy.deepcopy(state.peers[name])
        if name in {NS + "-" + s for s in ("cs", "tft", "platform", "web")}:
            return {
                "State": {"Running": False},
                "Config": {"Labels": {"team4.local.owner": config["owner"]}},
            }
        pytest.fail("Unexpected container inspection")

    def docker(*args):
        state.calls.append(args)
        if args[:2] == ("ps", "-q"):
            if args[-1] == "label=team4.local.driver=" + NS:
                return b""
            assert args == ("ps", "-q", "--filter", "volume=" + VOLUME)
            return ("\n".join(state.peers) + "\n").encode()
        if args[:2] == ("volume", "inspect"):
            assert args == ("volume", "inspect", VOLUME)
            if state.missing_volume:
                raise RuntimeError("private docker detail")
            return json.dumps([state.volume]).encode()
        if args[0] == "run":
            state.probe = args
            # Run only the fixed probe's shell on a synthetic directory, never Docker/Postgres.
            assert args[-2] == "local-pg-resume-check" and args[-1] == PGDATA
            result = subprocess.run(
                ["/bin/sh", "-eu", "-c", args[-3], args[-2], str(data)], capture_output=True
            )
            if result.returncode:
                raise RuntimeError("private probe detail")
            return result.stdout
        if args == ("ps", "-a", "--format", "{{.Names}}"):
            return "\n".join(NS + "-" + s for s in ("cs", "tft", "platform", "web")).encode()
        if args[0] == "start":
            if args[1] == NS + "-pg":
                state.pg["State"]["Running"] = True
            return b""
        pytest.fail("Unexpected Docker command")

    state.peers["own-pg"] = state.pg
    monkeypatch.setattr(stack, "owned", lambda _: config)
    monkeypatch.setattr(stack, "inspect", inspect)
    monkeypatch.setattr(stack, "docker", docker)
    monkeypatch.setattr(
        stack.socket, "socket", lambda: nullcontext(SimpleNamespace(bind=lambda _: None))
    )
    return state, tmp_path


def assert_no_mutation(state):
    assert not any(c[0] in {"start", "stop", "create", "rm"} for c in state.calls)
    assert state.marker.read_text() == "preserve until resume is safe"


@pytest.mark.parametrize(
    "broken",
    [
        "empty",
        "missing-version",
        "wrong-version",
        "no-base",
        "no-global",
        "version-symlink",
        "base-symlink",
        "global-symlink",
    ],
)
def test_missing_or_invalid_cluster_never_reaches_standard_pg_start(fake, broken):
    state, path = fake
    if broken in {"empty", "missing-version"}:
        (state.data / "PG_VERSION").unlink()
        if broken == "empty":
            (state.data / "base").rmdir()
            (state.data / "global").rmdir()
    elif broken == "wrong-version":
        (state.data / "PG_VERSION").write_text("16\n")
    elif broken.startswith("no-"):
        (state.data / broken.removeprefix("no-")).rmdir()
    else:
        name = {
            "version-symlink": "PG_VERSION",
            "base-symlink": "base",
            "global-symlink": "global",
        }[broken]
        target = state.data / name
        moved = state.data / (name + "-original")
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(ValueError, match="LOCAL_PG_CLUSTER_INVALID"):
        stack.start(path)
    assert_no_mutation(state)


def test_other_running_rw_mount_is_refused_without_stopping_either_namespace(fake):
    state, path = fake
    state.peers["other-namespace"] = {
        "Id": "other-id",
        "State": {"Running": True},
        "Mounts": [{"Type": "volume", "Name": VOLUME, "Destination": "/elsewhere", "RW": True}],
    }
    with pytest.raises(ValueError, match="LOCAL_PG_VOLUME_IN_USE"):
        stack.start(path)
    assert state.probe is None
    assert_no_mutation(state)


@pytest.mark.parametrize("running", [False, True])
def test_valid_cluster_checked_readonly_before_stopped_pg_can_start(fake, running):
    state, path = fake
    state.pg["State"]["Running"] = running
    assert stack.start(path) is state.config
    p = state.probe
    assert p is not None
    assert "--rm" in p and "--pull=never" in p and "--read-only" in p
    assert p[p.index("--network") + 1] == "none"
    assert p[p.index("--entrypoint") + 1] == "/bin/sh"
    assert p[p.index("--user") + 1] == "postgres"
    assert p[p.index("--cap-drop") + 1] == "ALL"
    assert p[p.index("--mount") + 1] == (
        f"type=volume,src={VOLUME},dst={PGDATA},readonly,volume-nocopy"
    )
    assert p[p.index("--entrypoint") + 2] == IMAGE
    assert "--env-file" not in p and "-p" not in p
    starts = [c for c in state.calls if c == ("start", NS + "-pg")]
    assert len(starts) == (0 if running else 1)
    if starts:
        assert state.calls.index(p) < state.calls.index(starts[0])
    assert not state.marker.exists()


def test_running_readonly_reader_does_not_block_resume(fake):
    state, path = fake
    state.peers["reader"] = {
        "Id": "reader-id",
        "State": {"Running": True},
        "Mounts": [{"Type": "volume", "Name": VOLUME, "Destination": "/read", "RW": False}],
    }
    stack.start(path)
    assert ("start", NS + "-pg") in state.calls


@pytest.mark.parametrize(
    "broken", ["missing-volume", "foreign-volume", "tag-image", "wrong-pgdata", "wrong-mount"]
)
def test_unsafe_target_is_refused_before_probe_or_start(fake, broken):
    state, path = fake
    if broken == "missing-volume":
        state.missing_volume = True
    elif broken == "foreign-volume":
        state.volume["Labels"]["team4.local.owner"] = "foreign"
    elif broken == "tag-image":
        state.config["images"]["pg"] = "postgres:latest"
    elif broken == "wrong-pgdata":
        state.pg["Config"]["Env"] = ["PGDATA=/another/empty/location"]
    else:
        state.pg["Mounts"][0]["Destination"] = "/another/location"
    with pytest.raises(ValueError, match="LOCAL_PG_"):
        stack.start(path)
    assert state.probe is None
    assert_no_mutation(state)
