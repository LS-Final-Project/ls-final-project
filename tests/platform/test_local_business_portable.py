"""Portable launcher regressions, fake OS/process/HTTPS only. No Docker, DB or models."""

import json
import os
import signal
import ssl
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.local_business import __main__ as cli
from scripts.local_business import config

NS = "team4-kan159-portable"
STARTED = "Tue Nov 2 06:00:00 2026"
TOKEN = "a" * 64


@pytest.fixture
def state(tmp_path):
    (tmp_path / "control").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {"namespace": NS, "ports": {"web": 39444}, "web_origin": "https://localhost:39444"}
        )
    )
    return tmp_path


def test_mac_clean_env_retains_only_docker_directory_and_home(monkeypatch):
    import shutil

    monkeypatch.setenv("HOME", "/Users/example")
    monkeypatch.setenv("PATH", "/private/unrelated:/Applications/Docker.app/Contents/Resources/bin")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    monkeypatch.setattr(
        shutil, "which", lambda name: "/Applications/Docker.app/Contents/Resources/bin/docker"
    )
    result = config.clean_env({})
    assert result["HOME"] == "/Users/example"
    assert "/Applications/Docker.app/Contents/Resources/bin" in result["PATH"].split(os.pathsep)
    assert "/private/unrelated" not in result["PATH"].split(os.pathsep)
    assert not {"GEMINI_API_KEY", "DOCKER_HOST", "HTTPS_PROXY"} & result.keys()


def command():
    return [
        sys.executable,
        "-m",
        "scripts.local_business",
        "tunnel",
        "--namespace",
        NS,
        "--tunnel-token",
        TOKEN,
    ]


def record(state):
    value = {
        "pid": 12345,
        "namespace": NS,
        "uid": os.getuid(),
        "argv": command(),
        "started": STARTED,
    }
    config.save(state / "control/tunnel.json", value)
    return value


def fake_ps(monkeypatch, *, uid=None, started=STARTED, argv=None, returncode=0, gone=lambda: False):
    def run(args, **kwargs):
        assert args[:4] == ["ps", "-ww", "-p", "12345"]
        assert "-E" not in args
        if gone():
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(
            returncode=returncode,
            stdout=f"{os.getuid() if uid is None else uid} {started} {' '.join(argv or command())}\n",
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", run)


def test_mac_stop_verifies_process_without_proc(state, monkeypatch):
    record(state)
    exists = Path.exists
    monkeypatch.setattr(
        Path, "exists", lambda p: False if str(p).startswith("/proc/") else exists(p)
    )
    killed = []
    fake_ps(monkeypatch, gone=lambda: bool(killed))

    def kill(pid, sig):
        if sig == 0:
            raise ProcessLookupError
        killed.append((pid, sig))

    monkeypatch.setattr(cli.os, "kill", kill)
    cli.stop_tunnel(state)
    assert killed == [(12345, signal.SIGTERM)]
    assert not (state / "control/tunnel.json").exists()


@pytest.mark.parametrize("change", ["uid", "started", "namespace", "token", "command", "saved_uid"])
def test_foreign_or_reused_pid_is_never_signalled(state, monkeypatch, change):
    value = record(state)
    argv = command()
    options = {}
    if change == "uid":
        options["uid"] = os.getuid() + 1
    elif change == "started":
        options["started"] = "Wed Nov 3 06:00:00 2026"
    elif change == "namespace":
        argv[5] = "team4-kan159-other"
    elif change == "token":
        argv[7] = "b" * 64
    elif change == "command":
        argv = [sys.executable, "-c", "unrelated"]
    else:
        value["uid"] += 1
        config.save(state / "control/tunnel.json", value)
    fake_ps(monkeypatch, argv=argv, **options)
    killed = []
    monkeypatch.setattr(cli.os, "kill", lambda *a: killed.append(a))
    with pytest.raises(ValueError, match="LOCAL_TUNNEL_PID_MISMATCH"):
        cli.stop_tunnel(state)
    assert killed == [] and (state / "control/tunnel.json").exists()


def test_mac_legacy_record_is_not_discarded_when_process_is_alive(state, monkeypatch):
    config.save(state / "control/tunnel.json", {"pid": 12345, "namespace": NS})
    exists = Path.exists
    monkeypatch.setattr(
        Path, "exists", lambda p: False if str(p).startswith("/proc/") else exists(p)
    )
    fake_ps(monkeypatch)
    killed = []
    monkeypatch.setattr(cli.os, "kill", lambda *a: killed.append(a))
    with pytest.raises(ValueError, match="LOCAL_TUNNEL_PROCESS_UNVERIFIED"):
        cli.stop_tunnel(state)
    assert killed == [] and (state / "control/tunnel.json").exists()


def test_process_identity_of_only_own_real_child():
    args = [sys.executable, "-I", "-c", "import time; time.sleep(10)"]
    child = subprocess.Popen(
        args, env=config.clean_env({}), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        value = cli.process_identity(child.pid)
        assert value["uid"] == os.getuid() and value["command"] == " ".join(args)
        assert value["started"]
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.parametrize(
    "data",
    [
        {"service": "platform", "database": False, "vector": True, "fts": True},
        {"service": "cs", "database": True, "vector": True, "fts": True},
        {"service": "platform", "database": 1, "vector": True, "fts": True},
        [],
    ],
)
def test_https_200_without_platform_readiness_never_succeeds(state, monkeypatch, data):
    monkeypatch.setattr(
        cli.ssl, "create_default_context", lambda **kw: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    )
    response = SimpleNamespace(status=200, read=lambda n: json.dumps({"data": data}).encode())

    class Response:
        def __enter__(self):
            return response

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(
        cli.urllib.request,
        "build_opener",
        lambda *a: SimpleNamespace(open=lambda *a, **kw: Response()),
    )
    ticks = iter([0, 0, 31])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="LOCAL_HTTPS_NOT_READY"):
        cli.wait_ready(
            state, {"web_origin": "https://localhost:39444"}, SimpleNamespace(poll=lambda: None)
        )


def test_https_requires_verified_certificate_no_proxy_no_redirect(state, monkeypatch):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    captured = {}

    def context(**kw):
        assert kw == {"cafile": str(state / "auth/tls-cert.pem")}
        return ctx

    def opener(*handlers):
        captured["handlers"] = handlers

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, limit):
                assert limit == 4096
                return b'{"data":{"service":"platform","database":true,"vector":true,"fts":true}}'

        def open(url, timeout):
            assert url == "https://localhost:39444/api/v1/platform/ready" and timeout == 2
            return Response()

        return SimpleNamespace(open=open)

    monkeypatch.setattr(cli.ssl, "create_default_context", context)
    monkeypatch.setattr(cli.urllib.request, "build_opener", opener)
    cli.wait_ready(
        state, {"web_origin": "https://localhost:39444"}, SimpleNamespace(poll=lambda: None)
    )
    proxy, https, redirect = captured["handlers"]
    assert proxy.proxies == {} and https._context is ctx
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    assert redirect.redirect_request(None, None, 302, "", {}, "https://external.invalid") is None


def test_tunnel_exit_does_not_claim_https_ready(state, monkeypatch):
    monkeypatch.setattr(
        cli.ssl, "create_default_context", lambda **kw: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    )
    with pytest.raises(ValueError, match="LOCAL_TUNNEL_EXITED_BEFORE_READY"):
        cli.wait_ready(
            state, {"web_origin": "https://localhost:39444"}, SimpleNamespace(poll=lambda: 1)
        )


def test_unreadable_process_identity_never_signals(state, monkeypatch):
    record(state)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=2, stdout="", stderr="private OS error"),
    )
    killed = []
    monkeypatch.setattr(cli.os, "kill", lambda *a: killed.append(a))
    with pytest.raises(ValueError, match="LOCAL_TUNNEL_PROCESS_UNVERIFIED"):
        cli.stop_tunnel(state)
    assert killed == [] and (state / "control/tunnel.json").exists()


def test_invalid_certificate_never_attempts_network(state, monkeypatch):
    def invalid(**kw):
        raise ssl.SSLError("fixture certificate failure")

    monkeypatch.setattr(cli.ssl, "create_default_context", invalid)
    monkeypatch.setattr(cli.urllib.request, "build_opener", lambda *a: pytest.fail("No network"))
    with pytest.raises(ValueError, match="LOCAL_HTTPS_CERTIFICATE_INVALID"):
        cli.wait_ready(
            state, {"web_origin": "https://localhost:39444"}, SimpleNamespace(poll=lambda: None)
        )


def test_start_readiness_failure_never_prints_ready_and_stops_only_own_child(
    state, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli, "start", lambda _: {"namespace": NS, "web_origin": "https://localhost:39444"}
    )
    captured = {}

    class Child:
        pid = 12345

        def __init__(self, args, **kwargs):
            captured["argv"] = args

        def poll(self):
            return None

        def terminate(self):
            captured["terminated"] = True

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", Child)
    monkeypatch.setattr(
        cli,
        "process_identity",
        lambda pid: {"uid": os.getuid(), "started": STARTED, "command": " ".join(captured["argv"])},
        raising=False,
    )

    def not_ready(*a):
        raise ValueError("LOCAL_HTTPS_NOT_READY")

    monkeypatch.setattr(cli, "wait_ready", not_ready, raising=False)
    with pytest.raises(ValueError, match="LOCAL_HTTPS_NOT_READY"):
        cli.up(state)
    assert captured["terminated"] is True
    assert "https://" not in capsys.readouterr().out
    assert not (state / "control/tunnel.json").exists()
