"""Deployment planning checks; no cloud requests or real secrets."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "deploy_private", Path(__file__).parents[1] / "deploy_private.py"
)
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


def build_fixture():
    return {
        "status": "SUCCESS",
        "substitutions": {"COMMIT_SHA": "a" * 40},
        "results": {
            "images": [
                {
                    "name": f"{deployment.REGISTRY}/{service}:{'a' * 40}",
                    "digest": f"sha256:{str(index) * 64}",
                }
                for index, service in enumerate(deployment.SERVICES, 1)
            ]
        },
    }


class DeploymentTests(unittest.TestCase):
    def test_all_services_use_build_result_digests_and_private_production_settings(self):
        for service, command in zip(
            deployment.SERVICES, deployment.deployment_commands(build_fixture())
        ):
            self.assertIn("--no-allow-unauthenticated", command)
            self.assertIn("--invoker-iam-check", command)
            self.assertIn(
                f"--service-account={service}-runtime@{deployment.PROJECT}.iam.gserviceaccount.com",
                command,
            )
            self.assertIn(f"--set-secrets=PGPASSWORD={service}-db-password:1", command)
            self.assertIn("--min=1", command)
            self.assertTrue(
                any(
                    arg.startswith(f"--image={deployment.REGISTRY}/{service}@sha256:")
                    for arg in command
                )
            )
            self.assertTrue(
                any("ENVIRONMENT=production,DEV_AUTH_ENABLED=false" in arg for arg in command)
            )

    def test_failed_or_unidentified_builds_are_rejected(self):
        for field, value in (("status", "FAILURE"), ("substitutions", {})):
            with self.subTest(field=field):
                build = build_fixture()
                build[field] = value
                with self.assertRaises(ValueError):
                    deployment.deployment_commands(build)

    def test_missing_mismatched_or_invalid_image_digest_rejects_entire_release(self):
        for change in ("missing", "revision", "digest"):
            with self.subTest(change=change):
                build = build_fixture()
                images = build["results"]["images"]
                if change == "missing":
                    images.pop()
                elif change == "revision":
                    images[-1]["name"] = images[-1]["name"].replace("a" * 40, "b" * 40)
                else:
                    images[-1]["digest"] = "latest"
                with self.assertRaises(ValueError):
                    deployment.deployment_commands(build)


if __name__ == "__main__":
    unittest.main()
