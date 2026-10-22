"""Verify shared AI and engine sources participate in their consumers' lint and typing."""

import importlib.util
import subprocess
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "check_backend", Path(__file__).parents[1] / "check_backend.py"
)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class BackendSelectionTests(unittest.TestCase):
    def test_business_backends_check_existing_shared_engine_and_agent_sources(self):
        for service in ("cs", "tft", "platform"):
            with (
                self.subTest(service=service),
                patch("sys.argv", ["check_backend.py", service]),
                patch.object(
                    Path, "is_dir", lambda p: str(p) in {"backend/agent", "backend/engine"}
                ),
                patch.object(check.subprocess, "run") as run,
            ):
                check.main()
                commands = [call.args[0] for call in run.call_args_list]
                typed = next(args for args in commands if args[0] == "mypy")
                lint = next(args for args in commands if args[:2] == ["ruff", "check"])
                for path in ("backend/engine", "backend/agent"):
                    self.assertEqual(path in typed, service != "platform")
                    self.assertEqual(path in lint, service != "platform")

    def test_nested_shared_tests_and_owned_tests_are_collected_by_consumers(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            tests = Path("tests")
            tests.mkdir()
            (tests / "test_eval_smoke.py").touch()
            for name in (
                "cs",
                "tft",
                "platform",
                "eval",
                "common",
                "agent",
                "engine",
                "new_shared",
            ):
                (tests / name).mkdir()
                (tests / name / "test_regression.py").touch()
            for service in ("cs", "tft", "platform", "eval", "all"):
                with (
                    self.subTest(service=service),
                    patch("sys.argv", ["check_backend.py", service]),
                    patch.object(check.subprocess, "run") as run,
                ):
                    check.main()
                    commands = [c.args[0] for c in run.call_args_list]
                    selected = next(c[2:] for c in commands if c[0] == "pytest")
                    if service == "all":
                        self.assertEqual(selected, ["tests"])
                        continue
                    self.assertIn("tests/test_eval_smoke.py", selected)
                    for shared in ("common", "agent", "engine", "new_shared"):
                        self.assertIn(f"tests/{shared}", selected)
                    for owner in ("cs", "tft", "platform", "eval"):
                        self.assertEqual(f"tests/{owner}" in selected, owner == service)
                    if service == "eval":
                        self.assertIn(
                            [
                                "ruff",
                                "format",
                                "--check",
                                "eval",
                                "tests",
                                "scripts/check_backend.py",
                            ],
                            commands,
                        )
                        self.assertFalse(any(c[0] == "mypy" or c[0] == "python" for c in commands))

    def test_first_failure_does_not_claim_to_run_later_checks(self):
        with (
            patch("sys.argv", ["check_backend.py", "cs"]),
            patch.object(
                check.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "ruff")
            ) as run,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                check.main()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:2], ["ruff", "check"])


if __name__ == "__main__":
    unittest.main()
