"""Secret setup checks with fake gcloud; no real credentials or cloud calls."""

import contextlib
import importlib.util
import io
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "register_gemini_secret", Path(__file__).parents[1] / "register_gemini_secret.py"
)
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)


class GeminiSecretTests(unittest.TestCase):
    def test_apply_uploads_only_through_stdin_and_grants_only_two_runtime_accounts(self):
        key = "fixture-sensitive-key"
        output = io.StringIO()
        with (
            patch.object(
                registration.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, b"", b""),
            ) as run,
            contextlib.redirect_stdout(output),
        ):
            registration.register(key, apply=True)
        calls = run.call_args_list
        uploads = [c for c in calls if c.kwargs.get("input") is not None]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].kwargs["input"], key.encode())
        grants = [
            c.args[0] for c in calls if c.args[0][1:3] == ["secrets", "add-iam-policy-binding"]
        ]
        self.assertEqual(len(grants), 2)
        for service, command in zip(("cs", "tft"), grants, strict=True):
            self.assertIn(
                f"--member=serviceAccount:{service}-runtime@{registration.PROJECT}.iam.gserviceaccount.com",
                command,
            )
        self.assertNotIn(key, output.getvalue())
        self.assertNotIn(key, " ".join(arg for c in calls for arg in c.args[0]))

    def test_preview_and_missing_key_never_call_cloud(self):
        with (
            patch.object(registration.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            registration.register("fixture-key")
            with self.assertRaises(registration.RegistrationError):
                registration.register("", apply=True)
        run.assert_not_called()

    def test_existing_secret_requires_pinned_version_one_and_never_overwrites(self):
        for state in ("ENABLED", "DISABLED"):
            responses = [registration.SECRET, state, "", ""]
            with (
                self.subTest(state=state),
                patch.object(registration, "gcloud", side_effect=responses) as run,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                if state == "ENABLED":
                    registration.register("fixture-key", apply=True)
                else:
                    with self.assertRaises(registration.RegistrationError):
                        registration.register("fixture-key", apply=True)
                self.assertEqual(
                    run.call_args_list[1].args[0][0:4], ["secrets", "versions", "describe", "1"]
                )
                self.assertFalse(any(c.kwargs.get("payload") for c in run.call_args_list))
                if state != "ENABLED":
                    self.assertEqual(run.call_count, 2)

    def test_failed_gcloud_does_not_expose_output_containing_key(self):
        key = "fixture-sensitive-key"
        with patch.object(
            registration.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, key.encode(), key.encode()),
        ):
            with self.assertRaises(registration.RegistrationError) as error:
                registration.register(key, apply=True)
        self.assertNotIn(key, str(error.exception))
