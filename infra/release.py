#!/usr/bin/env python3
"""Service-aware build and CD orchestration. plan is read-only; run mutates GCP."""

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.changes import SERVICES, changed_paths, classify  # noqa: E402

PROJECT = "proj-skkuls0106-183255377938"
REGION = "asia-northeast3"
ACCOUNT = os.environ.get("TEAM4_GCLOUD_ACCOUNT", "skkuls0106@skkukdp.re.kr")
BUCKET = f"gs://{PROJECT}-team4-build-source/source"
COMMON = [f"--project={PROJECT}", f"--account={ACCOUNT}", f"--region={REGION}"]


def output(command: list[str]) -> str:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        # Keep the SDK's error line, not auth diagnostics or uploaded source output.
        detail = next(
            (line for line in result.stderr.splitlines() if line.startswith("ERROR:")), ""
        )
        detail = re.sub(r"(?i)Bearer\s+\S+|ya29\.[\w.-]+", "[redacted]", detail)
        detail = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "[redacted]", detail)
        detail = re.sub(
            r"(?i)(access_token|refresh_token|id_token|client_secret)([\s\"']*[:=][\s\"']*)[^\s\"'&,}]+",
            r"\1\2[redacted]",
            detail,
        )
        raise RuntimeError(
            f"Command failed: {' '.join(command[:3])}; "
            + (detail[:1200] or "inspect account, IAM and build logs.")
        )
    return result.stdout.strip()


def revision(value: str = "HEAD") -> str:
    value = output(["git", "rev-parse", "--verify", f"{value}^{{commit}}"])
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected a full Git revision.")
    return value


def deployed_revision(service: str) -> str | None:
    result = subprocess.run(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            f"team4-{service}",
            *COMMON,
            "--format=value(metadata.labels.commit-sha)",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        # Only an actual missing service can be bootstrapped; access errors must fail.
        if "NOT_FOUND" in result.stderr or "Cannot find service" in result.stderr:
            return None
        raise RuntimeError(f"Cannot inspect deployment of {service}. Check IAM.")
    value = result.stdout.strip()
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


def needs_release(service: str, base: str | None, head: str) -> bool:
    if base == head:
        return False
    if base is None:
        return True
    # A service may already be ahead of a stale CI completion event.
    if (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", head, base], cwd=ROOT, capture_output=True
        ).returncode
        == 0
    ):
        return False
    try:
        paths = changed_paths(base, head)
    except subprocess.CalledProcessError:
        return True  # Unknown old history: rebuild instead of silently skipping.
    return service in classify(paths)["services"]


def is_current_main(head: str) -> bool:
    remote = output(["git", "ls-remote", "origin", "refs/heads/main"]).split()
    return bool(remote) and remote[0] == head


def wait_for_main_ci(head: str, timeout: int = 1200) -> None:
    """Read GitHub only, before acquiring GCP credentials or starting any builds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_current_main(head):
            raise ValueError("Main advanced; this release will not start.")
        runs = json.loads(
            output(
                [
                    "gh",
                    "run",
                    "list",
                    "--repo",
                    "LS-Final-Project/ls-final-project",
                    "--workflow",
                    "application-checks.yml",
                    "--branch",
                    "main",
                    "--commit",
                    head,
                    "--event",
                    "push",
                    "--limit",
                    "10",
                    "--json",
                    "status,conclusion,headSha,headBranch",
                ]
            )
        )
        matching = [r for r in runs if r.get("headSha") == head and r.get("headBranch") == "main"]
        if matching and matching[0]["status"] == "completed":
            if matching[0]["conclusion"] != "success":
                raise ValueError("The exact main CI did not succeed; release stopped.")
            print("PASS: exact main commit passed Application checks.", flush=True)
            return
        print("Waiting for Application checks on this main commit; no build started.", flush=True)
        time.sleep(min(15, max(0, deadline - time.monotonic())))
    raise ValueError("Timed out waiting for main CI; release stopped without building.")


def source_inputs(service: str, deploy: bool = False) -> list[str]:
    if deploy:
        return [".gcloudignore", "infra/cloudbuild.deploy.yaml", "infra/deploy_private.py"]
    if service == "web":
        return [".gcloudignore", "frontend", "docs/openapi", "infra/cloudbuild.web.yaml"]
    return [
        ".gcloudignore",
        ".dockerignore",
        "Dockerfile",
        "pyproject.toml",
        "uv.lock",
        "backend",
        "tests",
        "eval",
        "docs/openapi",
        "frontend/src/mocks/generated",
        "scripts/check_backend.py",
        "infra/cloudbuild.service.yaml",
    ]


def build(service: str, head: str, *, deploy_build_id: str | None = None) -> dict:
    deploy = deploy_build_id is not None
    inputs = source_inputs(service, deploy)
    if subprocess.run(["git", "diff", "--quiet", head, "--", *inputs], cwd=ROOT).returncode:
        raise ValueError(
            "Commit build inputs before submitting. Only the selected commit is uploaded."
        )
    config = (
        "infra/cloudbuild.deploy.yaml"
        if deploy
        else f"infra/cloudbuild.{'web' if service == 'web' else 'service'}.yaml"
    )
    substitutions = f"COMMIT_SHA={head},_SERVICE={service}"
    if service == "web" and not deploy:
        substitutions = f"COMMIT_SHA={head}"
    if deploy:
        if not re.fullmatch(r"[0-9a-f-]{36}", deploy_build_id or ""):
            raise ValueError("Expected a source build UUID.")
        substitutions += f",_BUILD_ID={deploy_build_id}"
    runner = "clouddeploy-runner" if deploy else "cloudbuild-runner"
    with tempfile.TemporaryDirectory(prefix="team4-release-") as directory:
        root = Path(directory)
        archive = root / "source.tar"
        with archive.open("wb") as target:
            subprocess.run(
                ["git", "archive", head, "--", *inputs], cwd=ROOT, stdout=target, check=True
            )
        with tarfile.open(archive) as source:
            source.extractall(root, filter="data")
        archive.unlink()
        payload = json.loads(
            output(
                [
                    "gcloud",
                    "builds",
                    "submit",
                    directory,
                    *COMMON,
                    f"--config={root / config}",
                    f"--ignore-file={root / '.gcloudignore'}",
                    f"--gcs-source-staging-dir={BUCKET}",
                    f"--service-account=projects/{PROJECT}/serviceAccounts/{runner}@{PROJECT}.iam.gserviceaccount.com",
                    f"--substitutions={substitutions}",
                    "--async",
                    "--quiet",
                    "--format=json(id,status)",
                ]
            )
        )
    build_id = payload["id"]
    print(f"{'CD' if deploy else 'Build'} {service}: {build_id}", flush=True)
    deadline = time.monotonic() + 1300
    previous = None
    while time.monotonic() < deadline:
        metadata = json.loads(
            output(
                [
                    "gcloud",
                    "builds",
                    "describe",
                    build_id,
                    *COMMON,
                    "--format=json(id,status,substitutions.COMMIT_SHA,results.images)",
                ]
            )
        )
        status = metadata["status"]
        if status != previous:
            print(f"{service} {build_id}: {status}", flush=True)
            previous = status
        if status == "SUCCESS":
            if metadata.get("substitutions", {}).get("COMMIT_SHA") != head:
                raise ValueError("Build source revision did not match.")
            return metadata
        if status not in {"PENDING", "QUEUED", "WORKING"}:
            raise RuntimeError(f"Build {build_id} finished with {status}. See Cloud Build logs.")
        time.sleep(10)
    raise RuntimeError(f"Timed out waiting for {build_id}; inspect it before retrying.")


def write_plan(result: dict, path: Path | None) -> None:
    print(json.dumps(result, indent=2))
    if path:
        with path.open("a") as target:
            for key, value in result.items():
                target.write(
                    f"{key}={json.dumps(value, separators=(',', ':')) if not isinstance(value, str) else value}\n"
                )


def main() -> int:
    os.chdir(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser(
        "plan", help="Read deployed SHA per service and print pending releases"
    )
    plan.add_argument("--github-output", type=Path)
    wait = commands.add_parser("wait-ci", help="Wait for successful push CI on the exact main SHA")
    wait.add_argument("--revision", default="HEAD")
    for name in ("build", "run"):
        command = commands.add_parser(name)
        command.add_argument("--service", choices=SERVICES, required=True)
        command.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    try:
        head = revision(getattr(args, "revision", "HEAD"))
        if args.command == "wait-ci":
            wait_for_main_ci(head)
        elif args.command == "plan":
            if not is_current_main(head):
                write_plan({"services": [], "revision": head}, args.github_output)
                return 0
            if os.environ.get("GITHUB_ACTIONS") == "true":
                runs = json.loads(
                    output(
                        [
                            "gh",
                            "run",
                            "list",
                            "--repo",
                            "LS-Final-Project/ls-final-project",
                            "--workflow",
                            "application-checks.yml",
                            "--commit",
                            head,
                            "--event",
                            "push",
                            "--json",
                            "conclusion,status,headBranch",
                        ]
                    )
                )
                if not any(
                    run["status"] == "completed"
                    and run["conclusion"] == "success"
                    and run["headBranch"] == "main"
                    for run in runs
                ):
                    raise ValueError(
                        "The exact main revision must pass Application checks before release."
                    )
            services = [
                service
                for service in SERVICES
                if needs_release(service, deployed_revision(service), head)
            ]
            write_plan({"services": services, "revision": head}, args.github_output)
        elif args.command == "build":
            build(args.service, head)
        else:
            if not is_current_main(head) or not needs_release(
                args.service, deployed_revision(args.service), head
            ):
                print("Skipped stale or already deployed release.")
                return 0
            metadata = build(args.service, head)
            if not is_current_main(head) or not needs_release(
                args.service, deployed_revision(args.service), head
            ):
                print("Source became stale while building; existing deployment preserved.")
                return 0
            build(args.service, head, deploy_build_id=metadata["id"])
        return 0
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Release stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
