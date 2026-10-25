#!/usr/bin/env python3
"""Prepare the Gemini API secret; only the operator runs --apply against GCP."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT = "proj-skkuls0106-183255377938"
ACCOUNT = "skkuls0106@skkukdp.re.kr"
REGION = "asia-northeast3"
SECRET = "gemini-api-key"
SERVICES = ("cs", "tft")


class RegistrationError(Exception):
    """Contains only a safe diagnostic, never provider or subprocess output."""


def gcloud(arguments: list[str], payload: bytes | None = None) -> str:
    result = subprocess.run(
        ["gcloud", *arguments, f"--project={PROJECT}", f"--account={ACCOUNT}", "--quiet"],
        input=payload,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RegistrationError("gcloud operation failed; check account and Secret Manager access")
    return result.stdout.decode("utf-8")


def register(key: str, *, apply: bool = False) -> None:
    if not key or not key.strip() or len(key.encode("utf-8")) > 65536:
        raise RegistrationError("Set a nonempty GEMINI_API_KEY locally; do not paste it into chat")
    if not apply:
        print(
            f"PLAN: {SECRET}:1 in {PROJECT}/{REGION}; grant access to cs-runtime and tft-runtime; preserve existing bindings"
        )
        print("No cloud calls made. Run --apply in the operator terminal to register.")
        return

    existing = {
        name.rsplit("/", 1)[-1]
        for name in gcloud(["secrets", "list", "--format=value(name)"]).splitlines()
    }
    if SECRET not in existing:
        gcloud(
            [
                "secrets",
                "create",
                SECRET,
                "--replication-policy=user-managed",
                f"--locations={REGION}",
                "--data-file=-",
                "--format=none",
            ],
            payload=key.encode("utf-8"),
        )
        print(f"CREATED: {SECRET}:1")
    else:
        # Deployments pin version 1, so a different enabled version is not sufficient.
        state = gcloud(
            ["secrets", "versions", "describe", "1", f"--secret={SECRET}", "--format=value(state)"]
        ).strip()
        if state != "ENABLED":
            raise RegistrationError("Existing gemini-api-key:1 is not enabled; no versions changed")
        print(f"KEPT: {SECRET}:1; existing value was not compared or overwritten")
    for service in SERVICES:
        gcloud(
            [
                "secrets",
                "add-iam-policy-binding",
                SECRET,
                f"--member=serviceAccount:{service}-runtime@{PROJECT}.iam.gserviceaccount.com",
                "--role=roles/secretmanager.secretAccessor",
                "--condition=None",
                "--format=none",
            ]
        )
        print(f"ACCESS: {SECRET} -> {service}-runtime")
    print("Secret prepared. No Cloud Run deployment or Vertex IAM change was performed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from dotenv import dotenv_values

    try:
        values = dotenv_values(args.env_file, interpolate=False) if args.env_file.exists() else {}
        key = os.environ.get("GEMINI_API_KEY") or values.get("GEMINI_API_KEY") or ""
        register(key, apply=args.apply)
    except (RegistrationError, OSError):
        print(
            "FAIL: Secret registration incomplete; check local key, account and pinned version 1. "
            "No secret value was logged.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
