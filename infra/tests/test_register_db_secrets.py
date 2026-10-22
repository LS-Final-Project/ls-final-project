"""Safety checks for uploading credentials; never calls a real gcloud."""

import contextlib
import importlib.util
import io
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "register_db_secrets", Path(__file__).parents[1] / "register_db_secrets.py"
)
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "LS_PROJECT_ID": registration.PROJECT,
            "LS_GCP_ACCOUNT": registration.ACCOUNT,
            "LS_REGION": registration.REGION,
            "CS_DB_PASSWORD": "test-only cs $' password",
            "TFT_DB_PASSWORD": "test-only tft 한글 password",
            "PLATFORM_DB_PASSWORD": "test-only platform password\n",
        }

    def test_passwords_use_stdin_and_access_is_scoped_to_matching_service(self):
        output = io.StringIO()
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch.object(
                registration.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, b"", b""),
            ) as run,
            contextlib.redirect_stdout(output),
        ):
            registration.register(False)
        for secret, variable, service in registration.SECRETS:
            creates = [
                call
                for call in run.call_args_list
                if call.args[0][1:4] == ["secrets", "create", secret]
            ]
            self.assertEqual(len(creates), 1)
            self.assertEqual(creates[0].kwargs["input"], self.environment[variable].encode("utf-8"))
            grants = [
                call.args[0]
                for call in run.call_args_list
                if call.args[0][1:4] == ["secrets", "add-iam-policy-binding", secret]
            ]
            self.assertEqual(len(grants), 1)
            self.assertIn(
                f"--member=serviceAccount:{service}@{registration.PROJECT}.iam.gserviceaccount.com",
                grants[0],
            )
            self.assertNotIn(self.environment[variable], output.getvalue())
            self.assertFalse(
                any(
                    self.environment[variable] in argument
                    for call in run.call_args_list
                    for argument in call.args[0]
                )
            )

    def test_invalid_inputs_stop_before_any_cloud_call(self):
        for name, invalid in (("LS_PROJECT_ID", "wrong-project"), ("TFT_DB_PASSWORD", "")):
            with (
                self.subTest(name=name),
                patch.dict(os.environ, self.environment | {name: invalid}, clear=True),
                patch.object(registration.subprocess, "run") as run,
                self.assertRaises(registration.RegistrationError),
            ):
                registration.register(False)
            run.assert_not_called()

    def test_rerun_keeps_existing_values_without_adding_versions(self):
        names = "\n".join(secret for secret, _, _ in registration.SECRETS).encode()

        def fake_run(command, **kwargs):
            response = names if command[1:3] == ["secrets", "list"] else b"versions/1"
            return subprocess.CompletedProcess(command, 0, response, b"")

        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch.object(registration.subprocess, "run", side_effect=fake_run) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            registration.register(False)
        self.assertFalse(any(call.kwargs.get("input") is not None for call in run.call_args_list))
        self.assertFalse(
            any(call.args[0][1:4] == ["secrets", "versions", "add"] for call in run.call_args_list)
        )

    def test_cli_failure_does_not_echo_sensitive_subprocess_output(self):
        secret = self.environment["CS_DB_PASSWORD"]
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch.object(
                registration.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1, secret.encode(), secret.encode()),
            ),
            patch("sys.argv", ["register_db_secrets.py"]),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            self.assertEqual(registration.main(), 1)
        self.assertNotIn(secret, output.getvalue() + errors.getvalue())


if __name__ == "__main__":
    unittest.main()
