"""Release planning prevents missed commits, unrelated deployments and stale rollback."""

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_last_deployment_comparison_retains_changes_skipped_by_docs_commit(self):
        with (
            patch.object(
                release.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)
            ),
            patch.object(
                release,
                "changed_paths",
                return_value=["backend/api/cs/routes.py", "docs/team-start.md"],
            ),
        ):
            self.assertTrue(release.needs_release("cs", "a" * 40, "c" * 40))
            self.assertFalse(release.needs_release("tft", "a" * 40, "c" * 40))
            self.assertFalse(release.needs_release("web", "a" * 40, "c" * 40))

    def test_stale_event_does_not_roll_back_already_newer_service(self):
        with (
            patch.object(
                release.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
            ),
            patch.object(release, "changed_paths") as changes,
        ):
            self.assertFalse(release.needs_release("cs", "b" * 40, "a" * 40))
            changes.assert_not_called()
        self.assertFalse(release.needs_release("cs", "a" * 40, "a" * 40))
        self.assertTrue(release.needs_release("web", None, "a" * 40))

    def test_unknown_old_history_builds_conservatively(self):
        with (
            patch.object(
                release.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)
            ),
            patch.object(
                release, "changed_paths", side_effect=subprocess.CalledProcessError(1, "git")
            ),
        ):
            self.assertTrue(release.needs_release("cs", "a" * 40, "b" * 40))

    def test_snapshot_inputs_exclude_environment_data_and_unrelated_infra(self):
        for service in release.SERVICES:
            paths = release.source_inputs(service)
            self.assertNotIn(".env", paths)
            self.assertNotIn("data", paths)
            self.assertNotIn(".", paths)
            if service == "web":
                self.assertNotIn("backend", paths)
            self.assertEqual(
                release.source_inputs(service, True),
                [".gcloudignore", "infra/cloudbuild.deploy.yaml", "infra/deploy_private.py"],
            )


if __name__ == "__main__":
    unittest.main()
