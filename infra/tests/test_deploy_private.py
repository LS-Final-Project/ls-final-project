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
            self.assertTrue(
                any(
                    arg.startswith(f"--update-secrets=PGPASSWORD={service}-db-password:1")
                    for arg in command
                )
            )
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

    def test_only_cs_and_tft_receive_gemini_provider_and_pinned_secret_reference(self):
        for service, command in zip(
            deployment.SERVICES, deployment.deployment_commands(build_fixture()), strict=True
        ):
            env = next(arg for arg in command if arg.startswith("--update-env-vars="))
            secrets = next(arg for arg in command if arg.startswith("--update-secrets="))
            self.assertEqual("AI_PROVIDER=gemini" in env, service in ("cs", "tft"))
            self.assertEqual("GEMINI_API_KEY=gemini-api-key:1" in secrets, service in ("cs", "tft"))
            self.assertNotIn("GEMINI_API_KEY", env)
            self.assertNotIn("GOOGLE_CLOUD_LOCATION", env)
            self.assertNotIn("AI_PROVIDER=vertex", env)

    def test_one_service_build_does_not_require_or_deploy_other_images(self):
        build = build_fixture()
        build["results"]["images"] = build["results"]["images"][:1]
        commands = deployment.deployment_commands(build, ("cs",))
        self.assertEqual(len(commands), 1)
        self.assertIn("team4-cs", commands[0])
        with self.assertRaises(ValueError):
            deployment.deployment_commands(build, ("platform",))

    def test_web_has_no_database_secret_or_cloudsql_access_and_automation_keeps_iam(self):
        build = build_fixture()
        build["results"]["images"] = [
            {"name": f"{deployment.REGISTRY}/web:{'a' * 40}", "digest": "sha256:" + "d" * 64}
        ]
        command = deployment.deployment_commands(build, ("web",))[0]
        self.assertIn("--allow-unauthenticated", command)
        self.assertFalse(
            any("--update-secrets" in arg or "--set-cloudsql" in arg for arg in command)
        )
        private = deployment.deployment_commands(
            build_fixture(), ("cs",), preserve_access_policy=True
        )[0]
        self.assertFalse(
            any("unauthenticated" in arg or "invoker-iam-check" in arg for arg in private)
        )

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
