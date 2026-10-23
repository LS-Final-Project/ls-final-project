#!/usr/bin/env python3
"""Select independent CI and release targets from committed paths."""

import argparse
import json
import subprocess
from pathlib import Path

BACKENDS = ("cs", "tft", "platform")
SERVICES = ("web", *BACKENDS)


def classify(paths: list[str]) -> dict:
    """변경 경로의 소유 서비스에 따라 검사와 배포 대상을 선택한다.

    Args:
        paths: 추가·수정·삭제된 저장소 상대 경로 목록.

    Returns:
        dict: 백엔드·프론트·인프라·평가 검사와 배포 서비스의 선택 결과.
    """
    release: set[str] = set()
    contracts: set[str] = set()
    infrastructure = evaluation = frontend = False
    for path in paths:
        if path.startswith(("data/", ".env")):
            continue
        if path.startswith("docs/openapi/"):
            service = Path(path).stem
            if service in BACKENDS:
                contracts.add(service)
                release.add(service)
                frontend = True
            continue
        if path.startswith(("backend/agent/", "backend/engine/")):
            release.update(("cs", "tft"))
            evaluation = True
            continue
        if path.endswith(".md") or path.startswith("contracts/"):
            continue
        if path.startswith("frontend/src/mocks/generated/"):
            frontend = True
            service = Path(path).stem
            if service in BACKENDS:
                contracts.add(service)
        elif path.startswith("frontend/"):
            release.add("web")
            frontend = True
        elif any(path.startswith(f"backend/api/{service}/") for service in BACKENDS):
            service = path.split("/")[2]
            release.add(service)
            contracts.add(service)
            frontend = True
        elif any(path.startswith(f"tests/{service}/") for service in BACKENDS):
            contracts.add(path.split("/")[1])
        elif path.startswith("backend/") or path in {"Dockerfile", "pyproject.toml", "uv.lock"}:
            release.update(BACKENDS)
            contracts.update(BACKENDS)
            frontend = True
            evaluation = True
        elif path.startswith(("eval/", "tests/eval/")) or path == "tests/test_eval_smoke.py":
            evaluation = True
        elif path.startswith("tests/"):
            contracts.update(BACKENDS)
            evaluation = True
        elif path == "compose.yaml":
            infrastructure = True
        elif path.startswith(("infra/", "scripts/")):
            infrastructure = True
            if path.startswith("infra/migrations/platform/"):
                contracts.add("platform")
            elif path in {"scripts/changes.py", "scripts/check_backend.py"}:
                release.update(SERVICES)
                contracts.update(BACKENDS)
                frontend = evaluation = True
            elif path.startswith("infra/cloudbuild."):
                if path.endswith("web.yaml"):
                    release.add("web")
                    frontend = True
                elif path.endswith("service.yaml"):
                    release.update(BACKENDS)
                    contracts.update(BACKENDS)
        elif path.startswith(".github/"):
            infrastructure = True
            if path.endswith("application-checks.yml"):
                contracts.update(BACKENDS)
                frontend = evaluation = True
        elif path in {".dockerignore", ".gcloudignore", "cloudbuild.yaml"}:
            release.update(SERVICES)
            contracts.update(BACKENDS)
            frontend = infrastructure = evaluation = True
        elif path not in {".gitignore", ".gitattributes"}:
            # An unknown executable/configuration path must not silently bypass CI.
            release.update(SERVICES)
            contracts.update(BACKENDS)
            frontend = infrastructure = evaluation = True
    checks = set(contracts) | (release & set(BACKENDS))
    return {
        "services": [service for service in SERVICES if service in release],
        "backends": [service for service in BACKENDS if service in checks],
        "contracts": [service for service in BACKENDS if service in contracts],
        "frontend": frontend,
        "infrastructure": infrastructure,
        "evaluation": evaluation,
    }


def changed_paths(base: str, head: str, merge_base: bool = False) -> list[str]:
    if not base or set(base) == {"0"}:
        return subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", head], text=True
        ).splitlines()
    if merge_base:
        base = subprocess.check_output(["git", "merge-base", base, head], text=True).strip()
    output = subprocess.check_output(
        ["git", "diff", "--no-renames", "--name-only", "-z", base, head, "--"]
    )
    return [path.decode("utf-8") for path in output.split(b"\0") if path]


def successful_branch_base(head: str, branch: str = "main") -> str:
    if branch not in {"main", "dev"}:
        raise ValueError("CI history must belong to main or dev")
    runs = json.loads(
        subprocess.check_output(
            [
                "gh",
                "run",
                "list",
                "--repo",
                "LS-Final-Project/ls-final-project",
                "--workflow",
                "application-checks.yml",
                "--branch",
                branch,
                "--status",
                "success",
                "--event",
                "push",
                "--limit",
                "50",
                "--json",
                "headSha",
            ],
            text=True,
        )
    )
    for run in runs:
        candidate = run["headSha"]
        if (
            candidate != head
            and subprocess.run(
                ["git", "merge-base", "--is-ancestor", candidate, head], capture_output=True
            ).returncode
            == 0
        ):
            return candidate
    return "0" * 40


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--merge-base", action="store_true")
    history = parser.add_mutually_exclusive_group()
    history.add_argument("--last-successful-main", action="store_true")
    history.add_argument("--last-successful-branch", choices=("main", "dev"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    branch = "main" if args.last_successful_main else args.last_successful_branch
    base = successful_branch_base(args.head, branch) if branch else args.base
    result = classify(changed_paths(base, args.head, args.merge_base))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.github_output:
        with args.github_output.open("a") as target:
            for key, value in result.items():
                target.write(f"{key}={json.dumps(value, separators=(',', ':'))}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
