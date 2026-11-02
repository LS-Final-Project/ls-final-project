"""Start/stop owned APIs without init, migration or a paid dispatcher. Fixed Docker targets only."""

import os
import socket
import subprocess
import threading
from pathlib import Path

from scripts.local_business.setup import docker, inspect, owned


def running_drivers(config):
    ids = (
        docker("ps", "-q", "--filter", "label=team4.local.driver=" + config["namespace"])
        .decode()
        .split()
    )
    for identifier in ids:
        row = inspect(identifier)
        if row["Config"]["Labels"].get("team4.local.owner") != config["owner"]:
            raise ValueError("LOCAL_NAMESPACE_OWNERSHIP_MISMATCH")
    return ids


def no_active_driver(config):
    if running_drivers(config):
        raise ValueError("LOCAL_REAL_DRIVER_ACTIVE_STOP_IT_FIRST")


def start(state: Path):
    config = owned(state)
    ns = config["namespace"]
    no_active_driver(config)
    for port in config["ports"].values():
        # Only web is host-published; internal API ports are in the DB namespace.
        if port == config["ports"]["web"]:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
    (state / "control/enabled.json").unlink(missing_ok=True)
    pg = inspect(ns + "-pg")
    if not pg["State"]["Running"]:
        docker("start", ns + "-pg")
    pg = inspect(ns + "-pg")
    if list(pg["NetworkSettings"]["Networks"]) != [ns + "-internal"]:
        raise ValueError("LOCAL_DB_NETWORK_MISMATCH")
    names = set(docker("ps", "-a", "--format", "{{.Names}}").decode().splitlines())
    for service in ("cs", "tft", "platform", "web"):
        name = ns + "-" + service
        if name in names:
            c = inspect(name)
            if c["Config"]["Labels"].get("team4.local.owner") != config["owner"]:
                raise ValueError("LOCAL_NAMESPACE_OWNERSHIP_MISMATCH")
            if not c["State"]["Running"]:
                docker("start", name)
            continue
        cmd = [
            "run",
            "-d",
            "--pull=never",
            "--name",
            name,
            "--label",
            "team4.local.owner=" + config["owner"],
            "--network",
            "container:" + ns + "-pg",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--workdir",
            "/tmp",
            "--mount",
            f"type=bind,src={state}/source,dst=/workspace,readonly",
            "--mount",
            f"type=bind,src={state}/settings.json,dst=/local/state/settings.json,readonly",
            "--mount",
            f"type=bind,src={state}/auth,dst=/local/state/auth,readonly",
            "--mount",
            f"type=bind,src={state}/control,dst=/local/state/control,readonly",
            "--mount",
            f"type=bind,src={state}/files,dst=/local/files",
            "--entrypoint",
            "/usr/bin/env",
        ]
        env = [
            "-i",
            "PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME=/tmp",
            "PYTHONPATH=/workspace",
            "PYTHONDONTWRITEBYTECODE=1",
            "ENVIRONMENT=local",
            "TFT_DOCUMENT_ROOT=/local/files",
            "LOCAL_BUSINESS_STATE=/local/state",
        ]
        if service == "web":
            cmd += [
                config["images"]["web"],
                *env,
                "NODE_ENV=test",
                "LOCAL_BUSINESS_REPO=/workspace",
                "node",
                "/workspace/scripts/local_business/local_web.mjs",
            ]
        else:
            cmd += [
                config["images"]["runtime"],
                *env,
                "LOCAL_BUSINESS_SERVICE=" + service,
                "python",
                "-m",
                "scripts.local_business.runtime",
            ]
        docker(*cmd)
    return config


def stop(state: Path):
    config = owned(state)
    ns = config["namespace"]
    no_active_driver(config)
    (state / "control/enabled.json").unlink(missing_ok=True)
    docker("stop", *[ns + "-" + s for s in ("web", "platform", "tft", "cs")])
    # DB and volume remain running/preserved. No docker down -v, reset or deletion.


def tunnel(state: Path):
    config = owned(state)
    name = config["namespace"] + "-platform"
    port = config["ports"]["web"]
    import socketserver

    # No caller-supplied command/address. These source constants only forward opaque TLS bytes.
    peer = (
        "import socket,sys,threading; s=socket.create_connection(('127.0.0.1'," + str(port) + "));"
        "\ndef send():\n try:\n  while True:\n   b=sys.stdin.buffer.read1(65536)\n   if not b:break\n   s.sendall(b)\n finally:\n  s.shutdown(socket.SHUT_WR)\n"
        "threading.Thread(target=send,daemon=True).start()\n"
        "while True:\n b=s.recv(65536)\n if not b:break\n sys.stdout.buffer.write(b);sys.stdout.buffer.flush()\n"
    )
    command = ["docker", "exec", "-i", name, "python", "-I", "-c", peer]
    limit = threading.BoundedSemaphore(16)

    class Connection(socketserver.BaseRequestHandler):
        def handle(self):
            if not limit.acquire(blocking=False):
                return
            p = None
            try:
                p = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                self.request.settimeout(120)

                def send():
                    try:
                        while chunk := self.request.recv(65536):
                            p.stdin.write(chunk)
                            p.stdin.flush()
                    except (OSError, ValueError):
                        pass
                    finally:
                        p.stdin.close()

                threading.Thread(target=send, daemon=True).start()
                while chunk := p.stdout.read1(65536):
                    self.request.sendall(chunk)
            except OSError:
                pass
            finally:
                if p:
                    p.terminate()
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait()
                limit.release()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", port), Connection) as server:
        server.serve_forever()
