"""Validate real Compose resolution without starting containers or reading local secrets."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required for config validation")
class LocalComposeTests(unittest.TestCase):
    def test_each_service_build_contains_its_runtime_module(self):
        values = {
            key: "fixture-local-password"
            for key in (
                "LOCAL_DB_ADMIN_PASSWORD",
                "LOCAL_CS_PASSWORD",
                "LOCAL_TFT_PASSWORD",
                "LOCAL_PLATFORM_PASSWORD",
            )
        }
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "compose.env"
            env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
            output = subprocess.check_output(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(env_file),
                    "-f",
                    str(ROOT / "compose.yaml"),
                    "config",
                    "--format",
                    "json",
                ],
                cwd=ROOT,
                env=dict(os.environ, **values),
                text=True,
                stderr=subprocess.PIPE,
            )
        services = json.loads(output)["services"]
        for name in ("cs", "tft", "platform"):
            with self.subTest(service=name):
                self.assertEqual(services[name]["build"]["args"]["SERVICE_NAME"], name)
                self.assertEqual(services[name]["environment"]["SERVICE_NAME"], name)
                self.assertEqual(services[name]["build"]["target"], "runtime")
                self.assertEqual(services[name]["ports"][0]["host_ip"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
