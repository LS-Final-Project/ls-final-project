#!/usr/bin/env python3
"""Reproducible local setup without loading or changing the existing .env/data."""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".env.local"


def run(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def local_settings() -> dict[str, str]:
    if not LOCAL.exists():
        raise ValueError("Run python3 scripts/workspace.py setup first.")
    result = {}
    for line in LOCAL.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def compose(*args: str) -> list[str]:
    local_settings()
    return ["docker", "compose", "--env-file", str(LOCAL), "-f", "compose.yaml", *args]


def setup() -> None:
    for tool in ("docker", "uv", "node", "npm"):
        if not shutil.which(tool):
            raise ValueError(f"Install {tool}; see docs/team-start.md prerequisites.")
    run(["docker", "compose", "version"])
    run(["docker", "info", "--format", "{{.ServerVersion}}"])
    version = subprocess.check_output(["node", "--version"], text=True).strip().lstrip("v")
    major, minor, *_ = map(int, version.split("."))
    if (major, minor) < (22, 12):
        raise ValueError("Use Node.js 22.12+ (Node 24 LTS recommended).")
    if not LOCAL.exists():
        body = (ROOT / ".env.local.example").read_text(encoding="utf-8")
        for _ in range(body.count("GENERATE_ON_SETUP")):
            body = body.replace("GENERATE_ON_SETUP", secrets.token_urlsafe(24), 1)
        with LOCAL.open("x", encoding="utf-8") as target:
            target.write(body)
        LOCAL.chmod(0o600)
        print("Created .env.local with separate local passwords. Existing .env was preserved.")
    else:
        print("Kept existing .env.local and passwords.")
    values = local_settings()
    if any(
        values.get(key, "") in {"", "GENERATE_ON_SETUP"}
        for key in (
            "LOCAL_DB_ADMIN_PASSWORD",
            "LOCAL_CS_PASSWORD",
            "LOCAL_TFT_PASSWORD",
            "LOCAL_PLATFORM_PASSWORD",
        )
    ):
        raise ValueError(
            "Local passwords are missing. Keep existing DB passwords; see troubleshooting."
        )
    run(["uv", "sync", "--frozen", "--python", "3.12"])
    front_env = ROOT / "frontend/.env.local"
    if not front_env.exists():
        body = "".join(
            f"VITE_{service.upper()}_API=http://127.0.0.1:{values[f'LOCAL_{service.upper()}_PORT']}\n"
            for service in ("cs", "tft", "platform")
        )
        with front_env.open("x", encoding="utf-8") as target:
            target.write(body)
    run(["npm", "ci", "--prefix", "frontend", "--no-audit", "--no-fund"])
    run(["npm", "run", "types", "--prefix", "frontend"])
    print("READY: run python3 scripts/workspace.py up, then npm run dev --prefix frontend.")


def check() -> None:
    values = local_settings()
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def call(
        service: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        origin: str | None = None,
    ) -> tuple[int, dict]:
        port = int(values[f"LOCAL_{service.upper()}_PORT"])
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers=headers,
        )
        try:
            response = opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        body = response.read()
        return response.status, json.loads(body) if body else {}

    for service in ("cs", "tft", "platform"):
        status, body = call(service, f"/api/v1/{service}/ready")
        if status != 200 or not all(
            body.get("data", {}).get(key) for key in ("database", "vector", "fts")
        ):
            raise ValueError(f"{service} readiness failed: HTTP {status}. Check workspace logs.")
        print(f"PASS: {service} DB account, schema, vector and FTS")
    base = "/api/v1/platform"
    status, body = call("platform", f"{base}/dev/accounts")
    if status != 200 or len(body["data"]["items"]) < 4:
        raise ValueError("Local demo accounts were not initialized.")
    user = body["data"]["items"][0]
    status, _ = call(
        "platform",
        f"{base}/dev/session",
        method="POST",
        payload={"user_id": user["id"]},
        origin="https://untrusted.invalid",
    )
    if status != 403:
        raise ValueError("Local session origin guard failed.")
    status, _ = call(
        "platform",
        f"{base}/dev/session",
        method="POST",
        payload={"user_id": "99999999-9999-4999-8999-999999999999"},
    )
    if status != 401:
        raise ValueError("Unregistered local account must be denied.")
    status, _ = call(
        "platform", f"{base}/dev/session", method="POST", payload={"user_id": user["id"]}
    )
    if status != 200:
        raise ValueError("Local session creation failed.")
    status, body = call("platform", f"{base}/me")
    if status != 200 or body["data"]["roles"] != user["roles"]:
        raise ValueError("Session identity did not match DB roles.")
    status, _ = call("platform", f"{base}/dev/session", method="DELETE")
    if status != 204 or call("platform", f"{base}/me")[0] != 401:
        raise ValueError("Local logout must invalidate the DB session.")
    print("PASS: account selection, DB roles, unknown-account denial, origin guard and logout")
    run(
        compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-X",
            "-U",
            "postgres",
            "-d",
            "team4",
            "-f",
            "/setup/sql/002_verify_service_roles.sql",
        )
    )


def main() -> int:
    os.chdir(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "up", "down", "check", "logs"):
        commands.add_parser(name)
    contracts = commands.add_parser("contracts")
    contracts.add_argument("service", choices=("cs", "tft", "platform", "all"))
    contracts.add_argument("--check", action="store_true")
    migration = commands.add_parser("migrate", help="Explicitly apply one local service migration")
    migration.add_argument("service", choices=("cs", "tft", "platform"))
    migration.add_argument("filename", help="File directly under infra/migrations/<service>/")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup()
        elif args.command == "up":
            run(compose("up", "-d", "--build", "--wait", "--wait-timeout", "180"))
            check()
        elif args.command == "down":
            run(compose("down"))  # Never removes volumes.
        elif args.command == "logs":
            run(compose("logs", "--tail", "80"))
        elif args.command == "check":
            check()
        elif args.command == "migrate":
            filename = Path(args.filename)
            source = ROOT / "infra/migrations" / args.service / filename
            if filename.name != args.filename or not source.is_file() or source.suffix != ".sql":
                raise ValueError(
                    "Use one existing .sql filename under your service migrations directory."
                )
            run(
                compose(
                    "exec",
                    "-T",
                    "db",
                    "psql",
                    "-X",
                    "-U",
                    "postgres",
                    "-d",
                    "team4",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-f",
                    f"/setup/migrations/{args.service}/{filename.name}",
                )
            )
        else:
            for service in ("cs", "tft", "platform") if args.service == "all" else (args.service,):
                for module in ("backend.openapi", "backend.mock_examples"):
                    run(
                        ["uv", "run", "--frozen", "python", "-m", module, "--service", service]
                        + (["--check"] if args.check else [])
                    )
                run(["npm", "run", f"types:{service}", "--prefix", "frontend"])
            run(["npm", "run", "check", "--prefix", "frontend"])
    except (ValueError, OSError, URLError, subprocess.CalledProcessError) as error:
        if isinstance(error, ValueError):
            print(f"ERROR: {error}", file=sys.stderr)
        else:
            print(
                "ERROR: command failed. See docs/team-start.md troubleshooting; do not share secret-bearing logs.",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
