#!/usr/bin/env python3
"""Run shared regression checks plus the selected backend or evaluation checks."""

import argparse
import subprocess
from pathlib import Path

BACKENDS = ("cs", "tft", "platform")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("all", *BACKENDS, "eval"), default="all", nargs="?")
    service = parser.parse_args().service
    if service in {"all", "eval"}:
        sources = ["backend" if service == "all" else "eval"]
    else:
        sources = [
            "backend/shared",
            "backend/api/common",
            f"backend/api/{service}",
            "backend/__main__.py",
            "backend/openapi.py",
            "backend/mock_examples.py",
        ]
    if service in {"cs", "tft"}:
        sources.extend(
            directory
            for directory in ("backend/engine", "backend/agent")
            if Path(directory).is_dir()
        )
    tests = [str(p) for p in Path("tests").glob("test_*.py")]
    if service == "all":
        tests = ["tests"]
    else:
        # Unknown test folders are shared until ownership is explicitly defined.
        for directory in sorted(Path("tests").iterdir()):
            if not directory.is_dir() or directory.name.startswith((".", "__")):
                continue
            if directory.name in (*BACKENDS, "eval") and directory.name != service:
                continue
            tests.append(str(directory))
    lint = sources + (["eval"] if service == "all" else []) + ["tests", "scripts/check_backend.py"]
    commands = [
        ["ruff", "check", *lint],
        ["ruff", "format", "--check", *lint],
    ]
    if service != "eval":
        commands.append(["mypy", *sources])
    commands.append(["pytest", "-q", *sorted(set(tests))])
    if service != "eval":
        commands.append(
            ["python", "-m", "backend.openapi", "--check"]
            + ([] if service == "all" else ["--service", service])
        )
        commands.extend(
            ["python", "-m", "backend.mock_examples", "--check", "--service", selected]
            for selected in (BACKENDS if service == "all" else (service,))
        )
    for command in commands:
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
