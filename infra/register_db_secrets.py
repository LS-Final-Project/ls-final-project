#!/usr/bin/env python3
"""KAN-62: register exported DB passwords through gcloud stdin, never argv."""

import argparse
import os
import subprocess
import sys

PROJECT = "proj-skkuls0106-183255377938"
ACCOUNT = "skkuls0106@skkukdp.re.kr"
REGION = "asia-northeast3"
SECRETS = (
    ("cs-db-password", "CS_DB_PASSWORD", "cs-runtime"),
    ("tft-db-password", "TFT_DB_PASSWORD", "tft-runtime"),
    ("platform-db-password", "PLATFORM_DB_PASSWORD", "platform-runtime"),
)


class RegistrationError(Exception):
    """A message that contains only setting names or cloud resource names."""


def gcloud(arguments: list[str], payload: bytes | None = None) -> str:
    command = [
        "gcloud",
        *arguments,
        f"--project={PROJECT}",
        f"--account={ACCOUNT}",
        "--quiet",
    ]
    result = subprocess.run(command, input=payload, capture_output=True, check=False)
    if result.returncode:
        # Do not print subprocess output: secret data may be present in errors.
        raise RegistrationError(
            f"gcloud {' '.join(arguments[:3])} failed (exit {result.returncode}). "
            "Stopped; existing secrets and versions were not removed."
        )
    return result.stdout.decode("utf-8")


def register(dry_run: bool) -> None:
    for setting, expected in (
        ("LS_PROJECT_ID", PROJECT),
        ("LS_GCP_ACCOUNT", ACCOUNT),
        ("LS_REGION", REGION),
    ):
        if os.environ.get(setting) != expected:
            raise RegistrationError(f"Check {setting}: target configuration mismatch.")

    # Validate all inputs before making any cloud changes. Do not strip passwords.
    for _, variable, _ in SECRETS:
        password = os.environ.get(variable, "")
        if not password:
            raise RegistrationError(f"Missing exported setting: {variable}")
        if len(password.encode("utf-8")) > 65536:
            raise RegistrationError(f"Setting exceeds Secret Manager size limit: {variable}")

    if dry_run:
        for name, _, service in SECRETS:
            print(f"PLAN: {name} in {REGION}; accessor={service}. No cloud calls made.")
        return

    existing = {
        name.rsplit("/", 1)[-1]
        for name in gcloud(["secrets", "list", "--format=value(name)"]).splitlines()
    }
    for name, variable, service in SECRETS:
        if name not in existing:
            gcloud(
                [
                    "secrets",
                    "create",
                    name,
                    "--replication-policy=user-managed",
                    f"--locations={REGION}",
                    "--data-file=-",
                    "--format=none",
                ],
                payload=os.environ[variable].encode("utf-8"),
            )
            print(f"CREATED: {name}")
        else:
            enabled = gcloud(
                [
                    "secrets",
                    "versions",
                    "list",
                    name,
                    "--filter=state:ENABLED",
                    "--limit=1",
                    "--format=value(name)",
                ]
            )
            if not enabled.strip():
                raise RegistrationError(f"No enabled version in existing secret: {name}")
            print(f"KEPT: {name} (existing value; not compared with local password)")

        gcloud(
            [
                "secrets",
                "add-iam-policy-binding",
                name,
                f"--member=serviceAccount:{service}@{PROJECT}.iam.gserviceaccount.com",
                "--role=roles/secretmanager.secretAccessor",
                "--condition=None",
                "--format=none",
            ]
        )
        print(f"ACCESS: {name} -> {service}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate exported inputs without cloud calls"
    )
    arguments = parser.parse_args()
    try:
        register(arguments.dry_run)
    except (RegistrationError, OSError) as error:
        if isinstance(error, RegistrationError):
            print(f"ERROR: {error}", file=sys.stderr)
        else:
            print("ERROR: Unable to start gcloud. Check CLI installation.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
