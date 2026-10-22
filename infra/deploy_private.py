#!/usr/bin/env python3
"""KAN-67: preview private deployments from a successful build; user runs --apply."""

import argparse
import json
import os
import re
import shlex
import subprocess

PROJECT = "proj-skkuls0106-183255377938"
ACCOUNT = "skkuls0106@skkukdp.re.kr"
REGION = "asia-northeast3"
REGISTRY = f"{REGION}-docker.pkg.dev/{PROJECT}/team4"
SERVICES = ("cs", "tft", "platform")
PLATFORM_URL = "https://team4-platform-gew6ci4pgq-du.a.run.app"


def deployment_commands(
    build: dict,
    selected: tuple[str, ...] = SERVICES,
    *,
    account: str = ACCOUNT,
    preserve_access_policy: bool = False,
) -> list[list[str]]:
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
    if not selected or any(service not in (*SERVICES, "web") for service in selected):
        raise ValueError("Select an existing service name.")
    for service in selected:
        image = f"{REGISTRY}/{service}"
        digest = images.get(f"{image}:{revision}", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"Build has no valid result digest for {service} at this revision.")
        command = [
            "gcloud",
            "run",
            "deploy",
            f"team4-{service}",
            f"--image={image}@{digest}",
            f"--project={PROJECT}",
            f"--region={REGION}",
            f"--service-account={service}-runtime@{PROJECT}.iam.gserviceaccount.com",
            "--execution-environment=gen2",
            "--ingress=all",
            "--min=1",
            "--max=2",
            "--cpu=1",
            "--memory=512Mi",
            "--port=8080",
            "--timeout=300s",
            "--concurrency=20",
            f"--labels=commit-sha={revision}",
            "--quiet",
        ]
        if account:
            command.append(f"--account={account}")
        if not preserve_access_policy:
            command.extend(
                [
                    "--invoker-iam-check",
                    "--allow-unauthenticated" if service == "web" else "--no-allow-unauthenticated",
                ]
            )
        if service == "web":
            command.append(f"--update-env-vars=NODE_ENV=production,PLATFORM_URL={PLATFORM_URL}")
        else:
            ai_environment = (
                ",AI_PROVIDER=gemini,GEMINI_MODEL=gemini-3.6-flash,GEMINI_MAX_OUTPUT_TOKENS=1024"
                if service in ("cs", "tft")
                else ""
            )
            command.extend(
                [
                    f"--set-cloudsql-instances={PROJECT}:{REGION}:team4-sql",
                    "--update-env-vars="
                    f"ENVIRONMENT=production,DEV_AUTH_ENABLED=false,SERVICE_NAME={service},"
                    f"PGHOST=/cloudsql/{PROJECT}:{REGION}:team4-sql,PGDATABASE=team4,"
                    f"PGUSER={service}_app{ai_environment}",
                    f"--update-secrets=PGPASSWORD={service}-db-password:1"
                    + (",GEMINI_API_KEY=gemini-api-key:1" if service in ("cs", "tft") else ""),
                ]
            )
        if service in ("cs", "tft"):
            command.append(
                "--remove-env-vars=GOOGLE_CLOUD_LOCATION,GOOGLE_CLOUD_EMBEDDING_LOCATION"
            )
        commands.append(command)
    return commands


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_id", help="Successful Seoul Cloud Build UUID")
    parser.add_argument("--apply", action="store_true", help="Deploy; default only prints commands")
    parser.add_argument("--service", choices=(*SERVICES, "web"), action="append")
    parser.add_argument("--account", default=os.environ.get("TEAM4_GCLOUD_ACCOUNT", ACCOUNT))
    parser.add_argument(
        "--preserve-access-policy",
        action="store_true",
        help="Keep existing IAM; required for automated least-privilege deployment",
    )
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
            f"--region={REGION}",
            "--format=json(status,substitutions.COMMIT_SHA,results.images)",
        ]
        + ([f"--account={arguments.account}"] if arguments.account else []),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        print("Unable to read build metadata. Check account, region and build ID.")
        return 1
    try:
        selected = tuple(dict.fromkeys(arguments.service or SERVICES))
        commands = deployment_commands(
            json.loads(result.stdout),
            selected,
            account=arguments.account,
            preserve_access_policy=arguments.preserve_access_policy,
        )
    except (ValueError, KeyError, TypeError) as error:
        print(f"Invalid build metadata: {error}")
        return 1
    if arguments.preserve_access_policy:
        # Automation may update revisions, never turn a private service into public access.
        for service in selected:
            common = [
                f"--project={PROJECT}",
                f"--region={REGION}",
                "--format=json",
            ]
            if arguments.account:
                common.append(f"--account={arguments.account}")
            try:
                policy = json.loads(
                    subprocess.check_output(
                        [
                            "gcloud",
                            "run",
                            "services",
                            "get-iam-policy",
                            f"team4-{service}",
                            *common,
                        ],
                        text=True,
                    )
                )
                description = json.loads(
                    subprocess.check_output(
                        ["gcloud", "run", "services", "describe", f"team4-{service}", *common],
                        text=True,
                    )
                )
            except (subprocess.CalledProcessError, ValueError):
                print("Existing service and access policy must be provisioned before automated CD.")
                return 1
            members = {
                member
                for binding in policy.get("bindings", [])
                if binding.get("role") == "roles/run.invoker"
                for member in binding.get("members", [])
            }
            disabled = (
                description.get("metadata", {})
                .get("annotations", {})
                .get("run.googleapis.com/invoker-iam-disabled")
                == "true"
            )
            if (
                disabled
                or (service != "web" and members & {"allUsers", "allAuthenticatedUsers"})
                or (service == "web" and "allUsers" not in members)
            ):
                print("Existing access policy differs from the expected web/private boundary.")
                return 1
    # Validate every requested image and policy before deploying any service.
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
