#!/usr/bin/env python3
"""KAN-201 O1: preview only by default; an operator alone runs --apply.

Create an in-memory P-256 VAPID scalar in Secret Manager version 1. Never print,
write a key file, access an existing secret value or rotate a deployed key.
"""

import argparse
import base64
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PROJECT = "proj-skkuls0106-183255377938"
ACCOUNT = "skkuls0106@skkukdp.re.kr"
REGION = "asia-northeast3"
SECRET = "notification-vapid-key"
WORKER = f"notification-push-runtime@{PROJECT}.iam.gserviceaccount.com"


def cloud(args: list[str], *, payload: bytes | None = None) -> str:
    result = subprocess.run(
        ["gcloud", *args, f"--project={PROJECT}", f"--account={ACCOUNT}", "--quiet"],
        input=payload,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("VAPID setup failed; check GCP account/access. No secret logged.")
    return result.stdout.decode()


def register(*, apply: bool = False) -> None:
    if not apply:
        print(
            f"PLAN: create {SECRET}:1 in {REGION}; grant only {WORKER}. No cloud calls/key generation."
        )
        print(
            "Operator: run O2 --prepare-identities --apply to prepare worker identity, then O1 --apply, then configure public key."
        )
        return
    names = cloud(["secrets", "list", "--format=value(name)"]).splitlines()
    if any(name.rsplit("/", 1)[-1] == SECRET for name in names):
        state = cloud(
            ["secrets", "versions", "describe", "1", f"--secret={SECRET}", "--format=value(state)"]
        ).strip()
        if state != "ENABLED":
            raise RuntimeError("Pinned VAPID version 1 unavailable; no key rotation performed.")
        print(
            "KEPT: existing VAPID version 1. Retain the matching public-key config; no secret read."
        )
    else:
        private = ec.generate_private_key(ec.SECP256R1())
        raw = private.private_numbers().private_value.to_bytes(32, "big")
        cloud(
            [
                "secrets",
                "create",
                SECRET,
                "--replication-policy=user-managed",
                f"--locations={REGION}",
                "--data-file=-",
                "--format=none",
            ],
            payload=base64.urlsafe_b64encode(raw).rstrip(b"="),
        )
        public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        print(
            "NOTIFICATION_PUSH_PUBLIC_KEY=" + base64.urlsafe_b64encode(public).rstrip(b"=").decode()
        )
    cloud(
        [
            "secrets",
            "add-iam-policy-binding",
            SECRET,
            f"--member=serviceAccount:{WORKER}",
            "--role=roles/secretmanager.secretAccessor",
            "--condition=None",
            "--format=none",
        ]
    )
    print(
        "READY: private key accessible only to push Job identity. No deployment/notification performed."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    try:
        register(apply=parser.parse_args().apply)
    except (OSError, RuntimeError):
        print("VAPID setup incomplete; no secret logged.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
