#!/usr/bin/env python3
"""KAN-67: preview private deployments from a successful build; user runs --apply."""

import argparse
import json
import re
import shlex
import subprocess

PROJECT = "proj-skkuls0106-183255377938"
ACCOUNT = "skkuls0106@skkukdp.re.kr"
REGION = "asia-northeast3"
REGISTRY = f"{REGION}-docker.pkg.dev/{PROJECT}/team4"
SERVICES = ("cs", "tft", "platform")


def deployment_commands(build: dict) -> list[list[str]]:
    if build.get("status") != "SUCCESS":
        raise ValueError("Build must have status SUCCESS.")
    revision = build.get("substitutions", {}).get("COMMIT_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Build must record a full Git commit SHA.")
    images = {
        image["name"]: image.get("digest", "")
        for image in build.get("results", {}).get("images", [])
    }
    commands = []
    for service in SERVICES:
        image = f"{REGISTRY}/{service}"
        digest = images.get(f"{image}:{revision}", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"Build has no valid result digest for {service} at this revision.")
        commands.append(
            [
                "gcloud",
                "run",
                "deploy",
                f"team4-{service}",
                f"--image={image}@{digest}",
                f"--project={PROJECT}",
                f"--account={ACCOUNT}",
                f"--region={REGION}",
                f"--service-account={service}-runtime@{PROJECT}.iam.gserviceaccount.com",
                "--execution-environment=gen2",
                "--ingress=all",
                "--invoker-iam-check",
                "--no-allow-unauthenticated",
                "--min=1",
                "--max=2",
                "--cpu=1",
                "--memory=512Mi",
                "--port=8080",
                "--timeout=300s",
                "--concurrency=20",
                f"--set-cloudsql-instances={PROJECT}:{REGION}:team4-sql",
                "--set-env-vars="
                f"ENVIRONMENT=production,DEV_AUTH_ENABLED=false,SERVICE_NAME={service},"
                f"PGHOST=/cloudsql/{PROJECT}:{REGION}:team4-sql,PGDATABASE=team4,"
                f"PGUSER={service}_app",
                f"--set-secrets=PGPASSWORD={service}-db-password:1",
                f"--labels=commit-sha={revision}",
                "--quiet",
            ]
        )
    return commands


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_id", help="Successful Seoul Cloud Build UUID")
    parser.add_argument("--apply", action="store_true", help="Deploy; default only prints commands")
    arguments = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", arguments.build_id):
        parser.error("Expected a Cloud Build UUID.")
    result = subprocess.run(
        [
            "gcloud",
            "builds",
            "describe",
            arguments.build_id,
            f"--project={PROJECT}",
            f"--account={ACCOUNT}",
            f"--region={REGION}",
            "--format=json(status,substitutions.COMMIT_SHA,results.images)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        print("Unable to read build metadata. Check account, region and build ID.")
        return 1
    try:
        commands = deployment_commands(json.loads(result.stdout))
    except (ValueError, KeyError, TypeError) as error:
        print(f"Invalid build metadata: {error}")
        return 1
    # Validate all three images before deploying any service.
    for command in commands:
        print(shlex.join(command), flush=True)
        if arguments.apply:
            result = subprocess.run(command, check=False)
            if result.returncode:
                print("Deployment stopped. Earlier services may already be deployed.")
                return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
