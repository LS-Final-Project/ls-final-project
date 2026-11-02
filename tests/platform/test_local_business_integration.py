"""Offline integration of actual local policies/factories. No provider or DB connection."""

import json
import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.shared.ai import ModelPrice
from scripts.local_business import runtime, setup
from scripts.local_business.permit import Denied, Permit
from scripts.local_business.worker import Configuration, LocalStop, Meter


@pytest.fixture
def local_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("K_SERVICE", raising=False)
    (tmp_path / "auth").mkdir()
    (tmp_path / "control").mkdir()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for name, data in {
        "caller-key.pem": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        "caller-public.pem": key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    }.items():
        f = tmp_path / "auth" / name
        f.write_bytes(data)
        f.chmod(0o600)
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "namespace": "team4-kan159-offline",
                "ports": {"web": 38443, "cs": 38081, "tft": 38082, "platform": 38083},
                "web_origin": "https://localhost:38443",
            }
        )
    )
    return tmp_path


@pytest.mark.parametrize("enabled,requested", [("cs", "tft"), ("tft", "cs")])
def test_wrong_owner_is_rejected_before_business_admission(
    local_state, enabled, requested, monkeypatch
):
    (local_state / "control/enabled.json").write_text(
        json.dumps({"mode": "real", "owner": enabled, "remaining_jobs": 1})
    )
    app = runtime.app_for("platform", local_state)
    forwarded = []
    app.state.business_forwarder = lambda *a: forwarded.append(a)
    path = (
        "/api/v1/cs/tickets/" + str(uuid4()) + "/analysis"
        if requested == "cs"
        else "/api/v1/tft/projects/" + str(uuid4()) + "/process"
    )
    with TestClient(app) as client:
        response = client.post(path, json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LOCAL_REAL_DRIVER_DISABLED"
    assert not forwarded


def test_disabled_exhausted_stale_or_wrong_owner_never_opens_gate(local_state):
    assert not runtime.gate(local_state, "cs")
    marker = local_state / "control/enabled.json"
    for value in [
        {"mode": "real", "owner": "cs", "remaining_jobs": 0},
        {"mode": "offline", "owner": "cs", "remaining_jobs": 1},
    ]:
        marker.write_text(json.dumps(value))
        assert not runtime.gate(local_state, "cs")
    marker.write_text(json.dumps({"mode": "real", "owner": "cs", "remaining_jobs": 1}))
    assert runtime.gate(local_state, "cs") and not runtime.gate(local_state, "tft")
    os.utime(marker, (1, 1))
    assert not runtime.gate(local_state, "cs")


def test_dirty_source_stops_before_any_namespace_or_db_creation(monkeypatch):
    monkeypatch.setattr(setup.subprocess, "check_output", lambda *a, **k: b" M infra/local/init.sh")
    monkeypatch.setattr(setup, "docker", lambda *a, **k: pytest.fail("Docker must not be called"))
    with pytest.raises(ValueError, match="LOCAL_SOURCE_DIRTY_COMMIT_BEFORE_SETUP"):
        setup.fresh("team4-kan159-offline", runtime_image="unused", web_image="unused")


def test_missing_frontend_provenance_stops_before_db_access(local_state, monkeypatch):
    config = json.loads((local_state / "settings.json").read_text())
    config.update(setup_complete=True, source_sha="recorded-source")
    (local_state / "settings.json").write_text(json.dumps(config))
    monkeypatch.setattr(
        setup, "inspect", lambda *a: pytest.fail("DB inspection must not be called")
    )
    with pytest.raises(ValueError, match="LOCAL_FRONTEND_PROVENANCE_MISSING"):
        setup.owned(local_state)


@pytest.mark.parametrize("outcome", ["known", "unknown", "overrun"])
def test_actual_sqlite_permit_is_used_at_sdk_boundary(tmp_path, outcome):
    p = Permit(tmp_path / "permit.sqlite3")
    token = p.authorize(max_jobs=1, max_calls=2, reserve_usd="0.10")
    job = uuid4()
    p.grant(token, str(job))
    config = Configuration(
        "cs", job, token, tmp_path, tmp_path / "not-read", tmp_path / "not-read-price"
    )
    price = ModelPrice(
        input_per_million=Decimal("1"),
        output_per_million=Decimal("2"),
        cached_input_per_million=Decimal("0.1"),
        currency="USD",
        as_of=date(2026, 11, 2),
    )
    meter = Meter(p, config, {"fake-sdk-model": price})
    tokens = 1000000 if outcome == "overrun" else 1
    usage = SimpleNamespace(
        prompt_token_count=tokens,
        candidates_token_count=1,
        thoughts_token_count=None if outcome == "unknown" else 0,
        total_token_count=tokens + 1,
        cached_content_token_count=None,
    )
    response = SimpleNamespace(usage_metadata=usage)

    def fake_sdk(**kwargs):
        with p.transaction() as db:
            assert db.execute("SELECT state FROM charges").fetchone()[0] == "RESERVED"
        return response

    if outcome == "known":
        assert meter.generate(fake_sdk, model="fake-sdk-model") is response
    else:
        with pytest.raises(LocalStop):
            meter.generate(fake_sdk, model="fake-sdk-model")
    with p.transaction() as db:
        row = db.execute("SELECT reserved,actual,state FROM charges").fetchone()
        assert row["reserved"] == "0.10"
        assert row["state"] == ("SETTLED" if outcome == "known" else "UNKNOWN")
        if outcome == "overrun":
            assert Decimal(row["actual"]) > Decimal("0.10")
    if outcome != "known":
        p.close_driver(token)
        with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
            Permit(p.path).authorize(max_jobs=1, max_calls=1, reserve_usd="0.10")


@pytest.mark.parametrize("operation", ["start", "stop"])
def test_token_suffixed_live_driver_prevents_stack_changes(local_state, monkeypatch, operation):
    from scripts.local_business import stack

    config = {"namespace": "team4-kan159-offline", "owner": "owned-marker", "ports": {"web": 38443}}
    marker = local_state / "control/enabled.json"
    marker.write_text("preserve live heartbeat")
    mutations = []

    def fake_docker(*args, **kwargs):
        if args[:2] == ("ps", "-q"):
            assert "label=team4.local.driver=team4-kan159-offline" in args
            return b"team4-kan159-offline-driver-abcd1234\n"
        mutations.append(args)
        pytest.fail("Live driver must block all stack mutation")

    monkeypatch.setattr(stack, "owned", lambda _: config)
    monkeypatch.setattr(stack, "docker", fake_docker)
    monkeypatch.setattr(
        stack,
        "inspect",
        lambda name: {
            "State": {"Running": True},
            "Config": {"Labels": {"team4.local.owner": "owned-marker"}},
        },
    )
    with pytest.raises(ValueError, match="LOCAL_REAL_DRIVER_ACTIVE_STOP_IT_FIRST"):
        getattr(stack, operation)(local_state)
    assert not mutations and marker.read_text() == "preserve live heartbeat"


def test_foreign_driver_owner_is_not_stopped(local_state, monkeypatch):
    from scripts.local_business import stack

    monkeypatch.setattr(stack, "docker", lambda *args: b"foreign-driver\n")
    monkeypatch.setattr(
        stack,
        "inspect",
        lambda name: {"Config": {"Labels": {"team4.local.owner": "somebody-else"}}},
    )
    with pytest.raises(ValueError, match="LOCAL_NAMESPACE_OWNERSHIP_MISMATCH"):
        stack.running_drivers({"namespace": "team4-kan159-offline", "owner": "ours"})


def test_old_pid_reused_by_other_namespace_is_never_killed(local_state, monkeypatch):
    from pathlib import Path

    from scripts.local_business import __main__ as cli

    pidfile = local_state / "control/tunnel.json"
    pidfile.write_text(json.dumps({"pid": 999999, "namespace": "team4-kan159-offline"}))
    exists, read = Path.exists, Path.read_bytes
    monkeypatch.setattr(
        Path, "exists", lambda p: True if str(p) == "/proc/999999/cmdline" else exists(p)
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda p: (
            b"python\0-m\0scripts.local_business\0tunnel\0--namespace\0team4-kan159-other\0"
            if str(p) == "/proc/999999/cmdline"
            else read(p)
        ),
    )
    killed = []
    monkeypatch.setattr(cli.os, "kill", lambda *args: killed.append(args))
    with pytest.raises(ValueError, match="LOCAL_TUNNEL_PID_MISMATCH"):
        cli.stop_tunnel(local_state)
    assert killed == [] and pidfile.exists()


@pytest.mark.parametrize("change", ["modified", "missing", "extra", "symlink", "extra-dist"])
def test_changed_snapshot_is_refused_before_container_access(local_state, monkeypatch, change):
    import hashlib
    import io
    import tarfile

    source = local_state / "source"
    source.mkdir()
    dist = source / "frontend/dist"
    dist.mkdir(parents=True)
    (local_state / "private").mkdir()
    original = b"# fixed worker\n"
    (source / "worker.py").write_bytes(original)
    (dist / "index.html").write_bytes(b"fixed frontend")
    digest = hashlib.sha256(original).hexdigest()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tree:
        member = tarfile.TarInfo("worker.py")
        member.size = len(original)
        tree.addfile(member, io.BytesIO(original))
    blob = archive.getvalue()
    (local_state / "private/source.tar").write_bytes(blob)
    (local_state / "source-manifest.json").write_text(
        json.dumps({"tracked": {"worker.py": digest}, "files": {"worker.py": digest}})
    )
    config = json.loads((local_state / "settings.json").read_text())
    config.update(
        setup_complete=True,
        source_sha="fixed-source",
        source_archive_sha256=hashlib.sha256(blob).hexdigest(),
        frontend_build={
            "source_sha": "fixed-source",
            "business_ui": True,
            "files": {"index.html": hashlib.sha256(b"fixed frontend").hexdigest()},
        },
    )
    (local_state / "settings.json").write_text(json.dumps(config))
    if change == "modified":
        (source / "worker.py").write_bytes(b"# modified worker")
    if change == "missing":
        (source / "worker.py").unlink()
    if change == "extra":
        (source / "unexpected.py").write_text("raise RuntimeError")
    if change == "symlink":
        (source / "unexpected.py").symlink_to(source / "worker.py")
    if change == "extra-dist":
        (dist / "unexpected.js").write_text("unexpected()")
    monkeypatch.setattr(
        setup, "inspect", lambda *a: pytest.fail("Changed source must not reach Docker")
    )
    with pytest.raises(ValueError, match="LOCAL_(SOURCE|FRONTEND)_PROVENANCE_MISMATCH"):
        setup.owned(local_state)
