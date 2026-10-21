"""Build boundaries, shared impact and rename/deletion safety."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("changes", Path(__file__).parents[1] / "changes.py")
changes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(changes)


class ChangeTests(unittest.TestCase):
    def test_main_compares_against_successful_ci_not_a_failed_previous_commit(self):
        with (
            patch.object(
                changes.subprocess,
                "check_output",
                return_value='[{"headSha":"failed-branch"},{"headSha":"passed-ancestor"}]',
            ),
            patch.object(
                changes.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 1),
                    subprocess.CompletedProcess([], 0),
                ],
            ),
        ):
            self.assertEqual(changes.successful_main_base("current"), "passed-ancestor")

    def test_frontend_does_not_build_backends(self):
        result = changes.classify(["frontend/src/App.tsx"])
        self.assertEqual(result["services"], ["web"])
        self.assertEqual(result["backends"], [])

    def test_business_change_checks_frontend_contract_without_deploying_web(self):
        result = changes.classify(["backend/api/cs/tickets/schemas.py", "docs/openapi/cs.json"])
        self.assertEqual(result["services"], ["cs"])
        self.assertEqual(result["backends"], ["cs"])
        self.assertTrue(result["frontend"])

    def test_mock_fixture_is_not_a_production_web_release(self):
        result = changes.classify(["frontend/src/mocks/generated/cs.json"])
        self.assertTrue(result["frontend"])
        self.assertEqual(result["services"], [])

    def test_shared_dependencies_expand_to_all_backends(self):
        for path in ("backend/shared/models.py", "uv.lock", "backend/api/common/application.py"):
            with self.subTest(path=path):
                result = changes.classify([path])
                self.assertEqual(result["services"], list(changes.BACKENDS))
                self.assertTrue(result["frontend"])

    def test_docs_are_skipped_but_unknown_code_is_not(self):
        self.assertEqual(changes.classify(["docs/team-start.md"])["services"], [])
        self.assertEqual(
            changes.classify(["new-runtime/handler.py"])["services"], list(changes.SERVICES)
        )
        self.assertEqual(changes.classify(["infra/migrations/cs/new.sql"])["services"], [])
        self.assertTrue(changes.classify(["infra/migrations/cs/new.sql"])["infrastructure"])

    def test_moving_between_services_includes_old_and_new_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args], text=True).strip()

            git("init", "-q")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "user.name", "fixture")
            source = root / "backend/api/cs/feature.py"
            source.parent.mkdir(parents=True)
            source.write_text("fixture\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            destination = root / "backend/api/tft/feature.py"
            destination.parent.mkdir(parents=True)
            source.rename(destination)
            git("add", "-A")
            git("commit", "-qm", "move")
            paths = subprocess.check_output(
                ["git", "-C", directory, "diff", "--no-renames", "--name-only", base, "HEAD"],
                text=True,
            ).splitlines()
            self.assertEqual(changes.classify(paths)["services"], ["cs", "tft"])


if __name__ == "__main__":
    unittest.main()
