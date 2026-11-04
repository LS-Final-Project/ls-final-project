"""Explicit local setup/start/stop/status and one bounded real UI dispatcher. No model defaults."""

import argparse
import json
import os
import re
import secrets
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from scripts.local_business.config import ROOT, clean_env, settings, state_path
from scripts.local_business.permit import (
    Permit,
    require_concurrency_snapshot,
    require_unlimited_snapshot,
    validate_concurrency,
    validate_limits,
)
from scripts.local_business.setup import docker, fresh, inspect, owned
from scripts.local_business.stack import running_drivers, start, stop, tunnel


def process_identity(pid):
    """Read only this PID's owner/start/command on Darwin and Linux; never print it."""
    if type(pid) is not int or pid <= 1:
        raise ValueError("LOCAL_TUNNEL_PID_MISMATCH")
    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "uid=", "-o", "lstart=", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            env=clean_env({"LC_ALL": "C"}),
        )
        if result.returncode == 1 and not result.stdout.strip():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return None
            raise ValueError("LOCAL_TUNNEL_PROCESS_UNVERIFIED")
        lines = result.stdout.strip().splitlines()
        if result.returncode or len(lines) != 1:
            raise ValueError("LOCAL_TUNNEL_PROCESS_UNVERIFIED")
        parts = lines[0].split(None, 6)
        if len(parts) != 7 or not parts[0].isdecimal():
            raise ValueError("LOCAL_TUNNEL_PROCESS_UNVERIFIED")
        return {"uid": int(parts[0]), "started": " ".join(parts[1:6]), "command": parts[6]}
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("LOCAL_TUNNEL_PROCESS_UNVERIFIED") from None


def stop_tunnel(state):
    pidfile = state / "control/tunnel.json"
    if not pidfile.exists():
        return
    if (
        pidfile.is_symlink()
        or pidfile.stat().st_uid != os.getuid()
        or pidfile.stat().st_mode & 0o077
    ):
        raise ValueError("LOCAL_TUNNEL_PID_MISMATCH")
    value = json.loads(pidfile.read_text())
    pid = value.get("pid")
    if type(pid) is not int or pid <= 1 or value.get("namespace") != settings(state)["namespace"]:
        raise ValueError("LOCAL_TUNNEL_PID_MISMATCH")
    if "argv" in value:
        argv = value["argv"]
        if (
            not isinstance(argv, list)
            or len(argv) != 8
            or not all(isinstance(a, str) for a in argv)
            or not Path(argv[0]).is_absolute()
            or argv[1:6]
            != ["-m", "scripts.local_business", "tunnel", "--namespace", value["namespace"]]
            or argv[6] != "--tunnel-token"
            or not re.fullmatch(r"[0-9a-f]{64}", argv[7])
            or value.get("uid") != os.getuid()
        ):
            raise ValueError("LOCAL_TUNNEL_PID_MISMATCH")
        proof = process_identity(pid)
        if proof is not None and proof != {
            "uid": value["uid"],
            "started": value.get("started"),
            "command": " ".join(argv),
        }:
            raise ValueError("LOCAL_TUNNEL_PID_MISMATCH")
    else:
        # Existing Linux records predate portable process proof. Preserve their exact argv guard.
        path = Path(f"/proc/{pid}/cmdline")
        if not path.exists():
            if process_identity(pid) is not None:
                raise ValueError("LOCAL_TUNNEL_PROCESS_UNVERIFIED")
            pidfile.unlink()
            return
        args = path.read_bytes().split(b"\0")
        namespace = settings(state)["namespace"].encode()
        positions = [i for i, arg in enumerate(args) if arg == b"--namespace"]
        if (
            b"scripts.local_business" not in args
            or b"tunnel" not in args
            or len(positions) != 1
            or positions[0] + 1 >= len(args)
            or args[positions[0] + 1] != namespace
            or value.get("namespace") != namespace.decode()
        ):
            raise ValueError("LOCAL_TUNNEL_PID_MISMATCH")
        if path.stat().st_uid != os.getuid():
            raise ValueError("LOCAL_TUNNEL_PID_MISMATCH")
        proof = process_identity(pid)
        if proof is not None and (
            proof["uid"] != os.getuid()
            or proof["command"] != " ".join(a.decode() for a in args if a)
        ):
            raise ValueError("LOCAL_TUNNEL_PID_MISMATCH")
    if proof is not None:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process_identity(pid) != proof:
                break  # Gone or reused; never signal the replacement process.
            time.sleep(0.05)
        else:
            raise ValueError("LOCAL_TUNNEL_STOP_TIMEOUT")
    pidfile.unlink()


def wait_ready(state, config, child):
    """Require authenticated localhost HTTPS and real platform DB readiness, not spawn success."""
    try:
        context = ssl.create_default_context(cafile=str(state / "auth/tls-cert.pem"))
    except (OSError, ssl.SSLError):
        raise ValueError("LOCAL_HTTPS_CERTIFICATE_INVALID") from None

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect()
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise ValueError("LOCAL_TUNNEL_EXITED_BEFORE_READY")
        try:
            with opener.open(
                config["web_origin"] + "/api/v1/platform/ready", timeout=2
            ) as response:
                document = json.loads(response.read(4096))
                data = document.get("data") if isinstance(document, dict) else None
                if (
                    isinstance(data, dict)
                    and response.status == 200
                    and data.get("service") == "platform"
                    and all(data.get(k) is True for k in ("database", "vector", "fts"))
                ):
                    return
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise ValueError("LOCAL_HTTPS_CERTIFICATE_INVALID") from None
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise ValueError("LOCAL_HTTPS_NOT_READY")


def up(state):
    config = start(state)
    command = [
        sys.executable,
        "-m",
        "scripts.local_business",
        "tunnel",
        "--namespace",
        config["namespace"],
        "--tunnel-token",
        secrets.token_hex(32),
    ]
    with (state / "logs/tunnel.log").open("ab") as log:
        p = subprocess.Popen(
            command,
            env=clean_env({}),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    from scripts.local_business.config import save

    try:
        proof = process_identity(p.pid)
        if proof is None or proof["uid"] != os.getuid() or proof["command"] != " ".join(command):
            raise ValueError("LOCAL_TUNNEL_PROCESS_UNVERIFIED")
        save(
            state / "control/tunnel.json",
            {
                "pid": p.pid,
                "namespace": config["namespace"],
                "uid": proof["uid"],
                "started": proof["started"],
                "argv": command,
            },
        )
        wait_ready(state, config, p)
    except BaseException:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
        pidfile = state / "control/tunnel.json"
        if pidfile.exists() and json.loads(pidfile.read_text()).get("argv") == command:
            pidfile.unlink()
        raise
    print(config["web_origin"])


def status(state):
    config = owned(state)
    ns = config["namespace"]
    result = {
        "namespace": ns,
        "web_origin": config["web_origin"],
        "real_enabled": bool(running_drivers(config)),
        "source_sha": config["source_sha"],
    }
    names = set(docker("ps", "-a", "--format", "{{.Names}}").decode().splitlines())
    result["containers"] = {
        s: inspect(ns + "-" + s)["State"]["Status"] if ns + "-" + s in names else "not-created"
        for s in ("pg", "cs", "tft", "platform", "web")
    }
    if result["containers"]["pg"] != "running":
        result["database_reason"] = "LOCAL_DB_STOPPED_START_EXPLICITLY"
        print(json.dumps(result, indent=2))
        return
    # Existing DB is the only job-state source. Never print prompts, responses, identities or secrets.
    sql = "SELECT json_build_object('id',j.id,'kind',j.kind,'status',j.status,'failure_code',c.failure_code,'lease_live',coalesce(c.lease_expires_at>clock_timestamp(),false)) FROM runtime.jobs j JOIN runtime.job_control c ON c.job_id=j.id WHERE j.status IN ('QUEUED','RUNNING','FAILED') ORDER BY j.created_at"
    raw = docker(
        "exec",
        "-e",
        "PGOPTIONS=-c default_transaction_read_only=on",
        ns + "-pg",
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
    )
    result["unfinished_or_failed_jobs"] = [json.loads(line) for line in raw.decode().splitlines()]
    result["recovery"] = (
        "Old QUEUED/RUNNING jobs are never automatically resubmitted. See existing reap_stale recovery; do not delete a live lease."
    )
    if (state / "control/driver-result.json").exists():
        result["driver_result"] = json.loads((state / "control/driver-result.json").read_text())
    print(json.dumps(result, indent=2))


def enable(state, args):
    if os.environ.get("ENVIRONMENT", "local") != "local" or "K_SERVICE" in os.environ:
        raise ValueError("LOCAL_RUNNER_REQUIRES_LOCAL_ENVIRONMENT")
    config = owned(state)
    ns = config["namespace"]
    unlimited = getattr(args, "unlimited", False)
    if unlimited:
        require_unlimited_snapshot(state)
    concurrent_jobs = getattr(args, "concurrent_jobs", 1)
    concurrent_calls = getattr(args, "concurrent_calls", 6)
    validate_concurrency(concurrent_jobs, concurrent_calls)
    if args.owner == "cs" and concurrent_jobs != 1:
        raise ValueError("LOCAL_CS_CONCURRENCY_REQUIRES_ONE_JOB")
    require_concurrency_snapshot(state)
    key = Path(args.key_file).expanduser()
    if key.is_symlink():
        raise ValueError("LOCAL_PRIVATE_KEY_FILE_REQUIRED")
    key = key.resolve()
    if not key.is_file() or key.stat().st_mode & 0o077 or key.stat().st_size > 4096:
        raise ValueError("LOCAL_PRIVATE_KEY_FILE_REQUIRED")
    prices = Path(args.prices_file).resolve()
    from backend.shared.ai import load_price_table

    table = load_price_table(prices)
    if args.model not in table or table[args.model].currency != "USD":
        raise ValueError("LOCAL_MODEL_PRICE_REQUIRED")
    permit = Permit(state / "control/permit.sqlite3")
    token = permit.authorize(
        max_jobs=args.max_jobs,
        max_calls=args.max_calls,
        reserve_usd=args.reserve_usd,
        budget_usd=args.budget_usd,
        unlimited=unlimited,
        concurrent_jobs=concurrent_jobs,
        concurrent_calls=concurrent_calls,
    )
    name = ns + "-driver-" + token[:8]
    try:
        # Only this worker/driver container sees the individual model key. APIs get neither file nor env.
        cmd = [
            "create",
            "--pull=never",
            "--name",
            name,
            "--label",
            "team4.local.owner=" + config["owner"],
            "--label",
            "team4.local.driver=" + ns,
            "--network",
            ns + "-internal",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--tmpfs",
            "/local/run:rw,nosuid,nodev,mode=777,size=16m",
            "--workdir",
            "/local/run",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
        ]
        for source, target, rw in [
            (state / "source", "/workspace", False),
            (state / "settings.json", "/local/state/settings.json", False),
            (state / "control", "/local/control", True),
            (state / "corpus", "/local/corpus", False),
            (state / "files", "/local/files", True),
            (state / "private/worker-db.json", "/local/secrets/worker-db.json", False),
            (key, "/run/secrets/gemini-api-key", False),
            (prices, "/local/prices.json", False),
        ]:
            cmd += ["--mount", f"type=bind,src={source},dst={target}" + ("" if rw else ",readonly")]
        cmd += [
            "--entrypoint",
            "/usr/bin/env",
            config["images"]["runtime"],
            "-i",
            "PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME=/tmp",
            "PYTHONPATH=/workspace",
            "PYTHONDONTWRITEBYTECODE=1",
            "ENVIRONMENT=local",
            "LOCAL_BUSINESS_STATE=/local/state",
            "LOCAL_BUSINESS_CONTROL=/local/control",
            "LOCAL_DRIVER_TOKEN=" + token,
            "LOCAL_JOB_OWNER=" + args.owner,
            "LOCAL_MODEL=" + args.model,
            "python",
            "-m",
            "scripts.local_business.dispatcher",
        ]
        docker(*cmd)
        docker("network", "connect", ns + "-worker-egress", name)
        docker("start", name)
    except BaseException:
        permit.close_driver(token)
        raise
    print("Started explicit", "unlimited" if unlimited else "limited", "driver:", name)
    print("UNKNOWN/interrupted reservations still block work; provider quotas remain in effect.")


def set_concurrency(state, args):
    """Change only the current authorization's scheduling caps, without a key or restart."""
    if os.environ.get("ENVIRONMENT", "local") != "local" or "K_SERVICE" in os.environ:
        raise ValueError("LOCAL_RUNNER_REQUIRES_LOCAL_ENVIRONMENT")
    validate_concurrency(args.concurrent_jobs, args.concurrent_calls)
    config = owned(state)
    require_concurrency_snapshot(state)
    drivers = running_drivers(config)
    if len(drivers) != 1:
        raise ValueError("LOCAL_SINGLE_DRIVER_REQUIRED")
    row = inspect(drivers[0])
    command = row["Config"]["Cmd"]
    if (
        not row["State"]["Running"]
        or row["Image"] != config["images"]["runtime"]
        or command[-3:] != ["python", "-m", "scripts.local_business.dispatcher"]
        or not all(
            any(
                m.get("Source") == str(state / source)
                and m["Destination"] == destination
                and m["RW"] is False
                for m in row["Mounts"]
            )
            for source, destination in (
                ("source", "/workspace"),
                ("settings.json", "/local/state/settings.json"),
            )
        )
        or not any(
            m.get("Source") == str(state / "control")
            and m["Destination"] == "/local/control"
            and m["RW"] is True
            for m in row["Mounts"]
        )
    ):
        raise ValueError("LOCAL_DRIVER_IDENTITY_MISMATCH")
    tokens = [v.split("=", 1)[1] for v in command if v.startswith("LOCAL_DRIVER_TOKEN=")]
    owners = [v.split("=", 1)[1] for v in command if v.startswith("LOCAL_JOB_OWNER=")]
    if len(tokens) != 1 or owners not in (["cs"], ["tft"]):
        raise ValueError("LOCAL_DRIVER_IDENTITY_MISMATCH")
    if owners == ["cs"] and args.concurrent_jobs != 1:
        raise ValueError("LOCAL_CS_CONCURRENCY_REQUIRES_ONE_JOB")
    Permit(state / "control/permit.sqlite3").update_concurrency(
        tokens[0], concurrent_jobs=args.concurrent_jobs, concurrent_calls=args.concurrent_calls
    )
    print(
        json.dumps(
            {
                "concurrent_jobs": args.concurrent_jobs,
                "concurrent_calls": args.concurrent_calls,
                "running_work_cancelled": False,
            }
        )
    )


DEMO_MANAGER = "00000000-0000-4000-8000-000000000004"  # seed_demo.sql local-4


def manage_project(state, number):
    """Grant the demo manager (local-4) one RFP project, like the server-external registration script.

    A manager only sees projects listed in identity.manager_projects (contracts/platform/permissions.md 1.2);
    no API grants it, so a project created in the local demo needs this before 책임자 지정.
    """
    if not re.fullmatch(r"P\d{2}-\d{4}", number):
        raise ValueError("LOCAL_PROJECT_NUMBER_INVALID")
    owned(state)
    ns = state.name
    sql = (
        "INSERT INTO identity.manager_projects(user_id, project_id) "
        f"SELECT '{DEMO_MANAGER}', id FROM tft.projects WHERE external_id = '{number}' "
        "ON CONFLICT DO NOTHING RETURNING project_id"
    )
    out = docker(
        "exec",
        ns + "-pg",
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
    rows = [line for line in out.splitlines() if re.fullmatch(r"[0-9a-f-]{36}", line)]
    print(json.dumps({"project": number, "granted": len(rows)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in (
        "setup",
        "start",
        "stop",
        "restart",
        "status",
        "tunnel",
        "enable-real",
        "disable-real",
        "set-concurrency",
        "manage-project",
    ):
        p = sub.add_parser(command)
        p.add_argument("--namespace", required=True)
        if command == "setup":
            p.add_argument("--runtime-image", required=True)
            p.add_argument("--web-image", required=True)
            p.add_argument("--web-port", type=int, default=38443)
        if command == "manage-project":
            p.add_argument("--project", required=True, help="RFP project number, e.g. P26-0002")
        if command == "tunnel":
            p.add_argument("--tunnel-token", help=argparse.SUPPRESS)
        if command in {"enable-real", "set-concurrency"}:
            p.add_argument(
                "--concurrent-jobs",
                type=int,
                default=1,
                required=command == "set-concurrency",
                help="Simultaneous RFP jobs",
            )
            p.add_argument(
                "--concurrent-calls",
                type=int,
                default=6,
                required=command == "set-concurrency",
                help="Simultaneous SDK calls across all jobs",
            )
        if command == "enable-real":
            p.add_argument("--owner", choices=["cs", "tft"], required=True)
            p.add_argument("--key-file", required=True)
            p.add_argument("--prices-file", required=True)
            p.add_argument("--model", required=True)
            p.add_argument(
                "--unlimited",
                action="store_true",
                help="Remove tool job/call/USD caps; preserve UNKNOWN blocking and usage journal",
            )
            p.add_argument("--max-jobs", type=int, help="Limited mode default: 1")
            p.add_argument("--max-calls", type=int, help="Limited mode default: 8")
            p.add_argument("--reserve-usd", default="0.15")
            p.add_argument("--budget-usd", help="Limited mode default: 1.50")
    args = parser.parse_args()
    if args.command in {"enable-real", "set-concurrency"}:
        try:
            validate_concurrency(args.concurrent_jobs, args.concurrent_calls)
        except ValueError:
            parser.error("Concurrency must be positive integers")
    if args.command == "enable-real":
        if args.unlimited and any(
            v is not None for v in (args.max_jobs, args.max_calls, args.budget_usd)
        ):
            parser.error(
                "--unlimited cannot be combined with --max-jobs, --max-calls or --budget-usd"
            )
        args.max_jobs = 1 if args.max_jobs is None else args.max_jobs
        args.max_calls = 8 if args.max_calls is None else args.max_calls
        args.budget_usd = "1.50" if args.budget_usd is None else args.budget_usd
        try:
            validate_limits(
                args.max_jobs, args.max_calls, args.reserve_usd, args.budget_usd, args.unlimited
            )
        except ValueError:
            parser.error("Invalid local limits or amounts")
    state = state_path(args.namespace)
    if args.command == "setup":
        print(
            fresh(
                args.namespace,
                runtime_image=args.runtime_image,
                web_image=args.web_image,
                web_port=args.web_port,
            )
        )
        return
    if args.command == "start":
        up(state)
    elif args.command == "stop":
        stop(state)
        stop_tunnel(state)
    elif args.command == "restart":
        stop(state)
        stop_tunnel(state)
        up(state)
    elif args.command == "status":
        status(state)
    elif args.command == "tunnel":
        tunnel(state)
    elif args.command == "enable-real":
        enable(state, args)
    elif args.command == "set-concurrency":
        set_concurrency(state, args)
    elif args.command == "manage-project":
        manage_project(state, args.project)
    elif args.command == "disable-real":
        config = owned(state)
        rows = running_drivers(config)
        if rows:
            docker("stop", "--time", "15", *rows)
        (state / "control/enabled.json").unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Fixed diagnostics only; subprocess/DB/provider exceptions may contain secrets.
        safe = (
            str(error) if re.fullmatch(r"LOCAL_[A-Z0-9_]+", str(error)) else "LOCAL_COMMAND_FAILED"
        )
        raise SystemExit(safe) from None
