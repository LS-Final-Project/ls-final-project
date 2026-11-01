"""Release planning prevents missed commits, unrelated deployments and stale rollback."""

import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_web_build_passes_ui_flag_only_after_tests_succeed(self):
        dockerfile = (release.ROOT / "frontend/Dockerfile").read_text()
        default = re.search(r"^ARG BUSINESS_UI_ENABLED=(\w+)$", dockerfile, re.M).group(1)
        cloudbuild = (release.ROOT / "infra/cloudbuild.web.yaml").read_text()
        args = [
            part.strip().strip("'")
            for part in re.search(r"args: \[(.+)\]", cloudbuild)[1].split(",")
        ]
        flag = args[args.index("--build-arg") + 1]
        self.assertEqual(flag, "BUSINESS_UI_ENABLED=true")
        command = next(
            line[4:] for line in dockerfile.splitlines() if line.startswith("RUN npm test ")
        )
        self.assertEqual(default, "false")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls.jsonl"
            npm = root / "npm"
            npm.write_text(
                "#!/usr/bin/env python3\nimport json,os,sys\n"
                "with open(os.environ['TEST_CALL_LOG'],'a') as f:\n"
                " f.write(json.dumps([sys.argv[1:],os.environ.get('VITE_BUSINESS_UI_ENABLED')])+'\\n')\n"
                "sys.exit(1 if sys.argv[1]=='test' and os.environ.get('TEST_NPM_FAIL') else 0)\n"
            )
            npm.chmod(0o755)
            env = {
                key: value for key, value in os.environ.items() if key != "VITE_BUSINESS_UI_ENABLED"
            }
            env.update(PATH=directory + os.pathsep + env["PATH"], TEST_CALL_LOG=str(calls))
            for enabled in (default, flag.split("=", 1)[1]):
                subprocess.run(
                    ["sh", "-ec", command], env=env | {"BUSINESS_UI_ENABLED": enabled}, check=True
                )
                self.assertEqual(
                    [json.loads(line) for line in calls.read_text().splitlines()],
                    [
                        [["test", "--prefix", "frontend"], None],
                        [["run", "build", "--prefix", "frontend"], enabled],
                    ],
                )
                calls.unlink()
            failed = subprocess.run(
                ["sh", "-ec", command],
                env=env | {"BUSINESS_UI_ENABLED": "true", "TEST_NPM_FAIL": "1"},
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(len(calls.read_text().splitlines()), 1)

    def test_ci_wait_accepts_only_the_exact_main_commit(self):
        sha = "a" * 40
        attempts = iter(
            [
                [],
                [
                    {
                        "headSha": sha,
                        "headBranch": "dev",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
                [
                    {
                        "headSha": "b" * 40,
                        "headBranch": "main",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
                [
                    {
                        "headSha": sha,
                        "headBranch": "main",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            ]
        )
        with (
            patch.object(release, "is_current_main", return_value=True),
            patch.object(
                release, "output", side_effect=lambda _: json.dumps(next(attempts))
            ) as calls,
            patch.object(release.time, "sleep"),
        ):
            release.wait_for_main_ci(sha)
        self.assertEqual(calls.call_count, 4)
        self.assertTrue(all(c.args[0][0] == "gh" for c in calls.call_args_list))

    def test_failed_or_stale_main_ci_stops_without_building(self):
        sha = "a" * 40
        for conclusion in ("failure", "cancelled", "skipped"):
            with (
                patch.object(release, "is_current_main", return_value=True),
                patch.object(
                    release,
                    "output",
                    return_value=json.dumps(
                        [
                            {
                                "headSha": sha,
                                "headBranch": "main",
                                "status": "completed",
                                "conclusion": conclusion,
                            }
                        ]
                    ),
                ),
                patch.object(release, "build") as build,
            ):
                with self.assertRaisesRegex(ValueError, "did not succeed"):
                    release.wait_for_main_ci(sha)
                build.assert_not_called()
        with (
            patch.object(release, "is_current_main", return_value=False),
            patch.object(release, "output") as calls,
        ):
            with self.assertRaisesRegex(ValueError, "Main advanced"):
                release.wait_for_main_ci(sha)
            calls.assert_not_called()

    def test_ci_wait_timeout_does_not_start_any_network_or_build(self):
        with patch.object(release, "output") as calls:
            with self.assertRaisesRegex(ValueError, "Timed out"):
                release.wait_for_main_ci("a" * 40, timeout=0)
            calls.assert_not_called()

    def test_failed_sdk_command_explains_failure_without_logging_credentials(self):
        token = "eyJhbGciOiJSUzI1NiJ9.payload.signature"
        result = subprocess.CompletedProcess(
            [],
            1,
            stdout="",
            stderr=(
                "auth diagnostics must stay private\n"
                "ERROR: (gcloud.builds.submit) INVALID_ARGUMENT: unused COMMIT_SHA; "
                f"Bearer {token} refresh_token=private-refresh-value"
            ),
        )
        with patch.object(release.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError) as failure:
                release.output(["gcloud", "builds", "submit"])
        message = str(failure.exception)
        self.assertIn("INVALID_ARGUMENT: unused COMMIT_SHA", message)
        for private in (token, "private-refresh-value", "auth diagnostics"):
            self.assertNotIn(private, message)

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
