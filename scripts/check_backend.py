#!/usr/bin/env python3
"""Run shared regression checks plus the selected backend's own checks."""

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "service", choices=("all", "cs", "tft", "platform"), default="all", nargs="?"
    )
    service = parser.parse_args().service
    sources = (
        ["backend"]
        if service == "all"
        else [
            "backend/shared",
            "backend/api/common",
            f"backend/api/{service}",
            "backend/__main__.py",
            "backend/openapi.py",
            "backend/mock_examples.py",
        ]
    )
    tests = [
        str(p)
        for p in Path("tests").glob("test_*.py")
        if service == "all" or p.name != "test_eval_smoke.py"
    ]
    if service == "all":
        tests = ["tests"]
    for directory in () if service == "all" else (f"tests/{service}", "tests/common"):
        if Path(directory).is_dir():
            tests.append(directory)
    lint = sources + (["eval"] if service == "all" else []) + ["tests", "scripts/check_backend.py"]
    commands = [
        ["ruff", "check", *lint],
        ["ruff", "format", "--check", *lint],
        ["mypy", *sources],
        ["pytest", "-q", *sorted(set(tests))],
        ["python", "-m", "backend.openapi", "--check"]
        + ([] if service == "all" else ["--service", service]),
    ]
    commands.extend(
        ["python", "-m", "backend.mock_examples", "--check", "--service", selected]
        for selected in (("cs", "tft", "platform") if service == "all" else (service,))
    )
    for command in commands:
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
