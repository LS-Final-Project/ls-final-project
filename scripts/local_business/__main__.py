"""Explicit local setup/start/stop/status and one bounded real UI dispatcher. No model defaults."""

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from scripts.local_business.config import ROOT, clean_env, state_path
from scripts.local_business.permit import Permit
from scripts.local_business.setup import docker, fresh, inspect, owned
from scripts.local_business.stack import running_drivers, start, stop, tunnel


def stop_tunnel(state):
    pidfile = state / "control/tunnel.json"
    if not pidfile.exists():
        return
    value = json.loads(pidfile.read_text())
    path = Path(f"/proc/{value['pid']}/cmdline")
    if path.exists():
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
        os.kill(value["pid"], signal.SIGTERM)
    pidfile.unlink()


def up(state):
    config = start(state)
    log = (state / "logs/tunnel.log").open("ab")
    p = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.local_business",
            "tunnel",
            "--namespace",
            config["namespace"],
        ],
        env=clean_env({}),
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    from scripts.local_business.config import save

    save(state / "control/tunnel.json", {"pid": p.pid, "namespace": config["namespace"]})
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
    config = owned(state)
    ns = config["namespace"]
    key = Path(args.key_file).expanduser().resolve()
    if (
        not key.is_file()
        or key.stat().st_mode & 0o077
        or key.stat().st_size > 4096
        or key.is_symlink()
    ):
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
    print("Started explicit bounded driver:", name)
    print("Reservations are admission controls, not a guaranteed provider USD hard cap.")


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
    ):
        p = sub.add_parser(command)
        p.add_argument("--namespace", required=True)
        if command == "setup":
            p.add_argument("--runtime-image", required=True)
            p.add_argument("--web-image", required=True)
            p.add_argument("--web-port", type=int, default=38443)
        if command == "enable-real":
            p.add_argument("--owner", choices=["cs", "tft"], required=True)
            p.add_argument("--key-file", required=True)
            p.add_argument("--prices-file", required=True)
            p.add_argument("--model", required=True)
            p.add_argument("--max-jobs", type=int, default=1)
            p.add_argument("--max-calls", type=int, default=8)
            p.add_argument("--reserve-usd", default="0.15")
            p.add_argument("--budget-usd", default="1.50")
    args = parser.parse_args()
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
            str(error)
            if isinstance(error, ValueError) and str(error).startswith("LOCAL_")
            else "LOCAL_COMMAND_FAILED"
        )
        raise SystemExit(safe) from None
