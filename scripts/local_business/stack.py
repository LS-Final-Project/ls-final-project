"""Start/stop owned APIs without init, migration or a paid dispatcher. Fixed Docker targets only."""

import json
import os
import re
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


def check_pg_resume(config, pg):
    """Read an existing cluster without the image entrypoint or any initialization.

    Never create/repair a volume, stop another writer, or run initdb as recovery.
    Docker inspect/check/start are separate operations, not a daemon-wide lock.
    """
    volume = config["namespace"] + "-pgdata"
    image = config["images"]["pg"]
    pgdata = "/var/lib/postgresql/data"
    env = pg["Config"].get("Env", [])
    mounts = [m for m in pg["Mounts"] if m.get("Name") == volume]
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", image)
        or [e for e in env if e.startswith("PGDATA=")] != ["PGDATA=" + pgdata]
        or len(mounts) != 1
        or mounts[0].get("Type") != "volume"
        or mounts[0].get("Destination") != pgdata
        or mounts[0].get("RW") is not True
    ):
        raise ValueError("LOCAL_PG_TARGET_INVALID")
    try:
        metadata = json.loads(docker("volume", "inspect", volume))[0]
    except (RuntimeError, ValueError, IndexError, TypeError):
        raise ValueError("LOCAL_PG_VOLUME_UNAVAILABLE") from None
    labels = metadata.get("Labels") or {}
    if (
        metadata.get("Name") != volume
        or labels.get("team4.local.owner") != config["owner"]
        or labels.get("team4.local.namespace") != config["namespace"]
    ):
        raise ValueError("LOCAL_PG_VOLUME_OWNERSHIP_MISMATCH")
    # The owned PG may already be running. Every other live RW consumer is refused,
    # including a writer from another namespace; do not stop or modify that container.
    for identifier in docker("ps", "-q", "--filter", "volume=" + volume).decode().split():
        peer = inspect(identifier)
        if (
            peer["Id"] != pg["Id"]
            and peer["State"]["Running"]
            and any(
                m.get("Type") == "volume" and m.get("Name") == volume and m.get("RW") is True
                for m in peer["Mounts"]
            )
        ):
            raise ValueError("LOCAL_PG_VOLUME_IN_USE")
    try:
        docker(
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            "--user",
            "postgres",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=volume,src={volume},dst={pgdata},readonly,volume-nocopy",
            "--entrypoint",
            "/bin/sh",
            image,
            "-eu",
            "-c",
            'test ! -L "$1/PG_VERSION" && test -f "$1/PG_VERSION" && '
            'test "$(cat "$1/PG_VERSION")" = 17 && '
            'test ! -L "$1/base" && test -d "$1/base" && '
            'test ! -L "$1/global" && test -d "$1/global"',
            "local-pg-resume-check",
            pgdata,
        )
    except RuntimeError:
        raise ValueError("LOCAL_PG_CLUSTER_INVALID") from None


def check_web_port(port):
    # Match ThreadingTCPServer.allow_reuse_address: TIME_WAIT is not a live listener.
    # SO_REUSEPORT is deliberately absent; an existing server must still be refused.
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            raise ValueError("LOCAL_WEB_PORT_UNAVAILABLE") from None


def start(state: Path):
    config = owned(state)
    ns = config["namespace"]
    no_active_driver(config)
    pg = inspect(ns + "-pg")
    check_pg_resume(config, pg)
    for port in config["ports"].values():
        # Only web is host-published; internal API ports are in the DB namespace.
        if port == config["ports"]["web"]:
            check_web_port(port)
    (state / "control/enabled.json").unlink(missing_ok=True)
    if not pg["State"]["Running"]:
        docker("start", ns + "-pg")
    pg = inspect(ns + "-pg")
    networks = list(pg["NetworkSettings"]["Networks"])
    # The APIs share the DB network namespace. Real mail mode alone adds outbound internet
    # (Microsoft sign-in and Graph); mail-off removes it on the next start.
    egress = ns + "-worker-egress"
    want = (state / "auth" / "mail.json").exists()
    if want and egress not in networks:
        docker("network", "connect", egress, ns + "-pg")
    elif not want and egress in networks:
        docker("network", "disconnect", egress, ns + "-pg")
    if sorted(list(inspect(ns + "-pg")["NetworkSettings"]["Networks"])) != sorted(
        [ns + "-internal"] + ([egress] if want else [])
    ):
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


DEMO_MANAGER = "00000000-0000-4000-8000-000000000004"  # seed_demo.sql local-4
AUTO_MANAGE_SECONDS = 5


def grant_new_projects(state: Path) -> list[str]:
    """로컬 시연 전용: 아직 시연 관리자(local-4)에게 등록되지 않은 RFP 프로젝트를 등록한다.

    운영에서는 관리 대상을 서버 밖 등록 스크립트로만 준다(contracts/platform/permissions.md 1.2, manage-project와 같은 SQL).
    로컬 하네스만 새 프로젝트를 만들 때마다 손으로 등록하지 않게 tunnel이 주기적으로 부른다(사용자 결정 2026-11-05, KAN-236은 운영 방식).
    """
    sql = (
        "INSERT INTO identity.manager_projects(user_id, project_id) "
        f"SELECT '{DEMO_MANAGER}', p.id FROM tft.projects p WHERE NOT EXISTS ("
        f"SELECT 1 FROM identity.manager_projects m WHERE m.user_id = '{DEMO_MANAGER}' AND m.project_id = p.id) "
        "ON CONFLICT DO NOTHING RETURNING project_id"
    )
    out = docker(
        "exec",
        state.name + "-pg",
        "psql",
        "-X",
        "-U",
        "postgres",
        "-d",
        "team4",
        "-At",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    ).decode()
    return [line for line in out.splitlines() if re.fullmatch(r"[0-9a-f-]{36}", line)]


def auto_manage(state: Path, stop: threading.Event) -> None:
    """tunnel이 떠 있는 동안 grant_new_projects를 주기적으로 부른다. 실패는 기록만 하고 다음 주기에 다시 한다."""
    while not stop.wait(AUTO_MANAGE_SECONDS):
        try:
            granted = grant_new_projects(state)
        except Exception as error:  # noqa: BLE001 — 로컬 보조 작업이라 tunnel을 멈추지 않는다
            print(json.dumps({"auto_manage": "failed", "error": type(error).__name__}), flush=True)
            continue
        if granted:
            print(
                json.dumps(
                    {"auto_manage": "granted", "manager": DEMO_MANAGER, "projects": granted}
                ),
                flush=True,
            )


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

    if config.get("auto_manage_projects", True):
        threading.Thread(target=auto_manage, args=(state, threading.Event()), daemon=True).start()
    with Server(("127.0.0.1", port), Connection) as server:
        server.serve_forever()
