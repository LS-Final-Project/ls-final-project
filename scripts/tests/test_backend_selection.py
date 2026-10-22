"""Verify shared AI and engine sources participate in their consumers' lint and typing."""

import importlib.util
import unittest
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


if __name__ == "__main__":
    unittest.main()
