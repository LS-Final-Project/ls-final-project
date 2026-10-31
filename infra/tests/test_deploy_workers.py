"""Worker Job 배포 계획 검사. 클라우드를 호출하지 않는다."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "deploy_workers", Path(__file__).parents[1] / "deploy_workers.py"
)
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
REVISION = "a" * 40


def build_fixture(worker="tft"):
    return {
        "status": "SUCCESS",
        "substitutions": {"COMMIT_SHA": REVISION},
        "results": {
            "images": [
                {
                    "name": f"{deployment.REGISTRY}/{worker}-worker:{REVISION}",
                    "digest": "sha256:" + "1" * 64,
                }
            ]
        },
    }


class WorkerDeploymentTests(unittest.TestCase):
    def test_job_uses_the_exact_digest_and_its_own_identity(self):
        [command] = deployment.job_commands(build_fixture(), ("tft",))
        text = " ".join(command)
        self.assertIn(f"{deployment.REGISTRY}/tft-worker@sha256:{'1' * 64}", text)
        self.assertIn("--service-account=tft-worker-runtime@", text)
        self.assertIn("--max-retries=0", command)
        self.assertIn("--container=worker", command)
        self.assertIn("TFT_WORKER_ENABLED=true", text)
        self.assertIn(f"TFT_DOCUMENT_BUCKET={deployment.BUCKET}", text)
        self.assertIn("PGPASSWORD=tft-worker-db-password:1", text)
        self.assertIn("user=tft_worker_login", text)

    def test_password_is_never_in_the_environment_values(self):
        for worker in deployment.WORKERS:
            [command] = deployment.job_commands(build_fixture(worker), (worker,))
            values = next(c for c in command if c.startswith("--set-env-vars="))
            self.assertNotIn("PASSWORD", values)
            self.assertNotIn("KEY", values)

    def test_cs_job_has_no_rfp_bucket(self):
        [command] = deployment.job_commands(build_fixture("cs"), ("cs",))
        text = " ".join(command)
        self.assertIn("CS_WORKER_ENABLED=true", text)
        self.assertIn("CS_POLICY_DOCUMENTS_FILE=/app/eval/policy/cs_policy_documents.json", text)
        self.assertNotIn("TFT_", text)

    def test_unfinished_or_mismatched_builds_are_refused(self):
        for change in (
            {"status": "FAILURE"},
            {"substitutions": {"COMMIT_SHA": "short"}},
            {"results": {"images": []}},
        ):
            with self.assertRaises(ValueError):
                deployment.job_commands({**build_fixture(), **change}, ("tft",))
        with self.assertRaises(ValueError):
            deployment.job_commands(build_fixture(), ("platform",))


if __name__ == "__main__":
    unittest.main()
