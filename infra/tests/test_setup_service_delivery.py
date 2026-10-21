"""Inspect exact cloud setup commands with a fake gcloud; never mutate GCP."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]


class DeliverySetupTests(unittest.TestCase):
    def test_oidc_is_limited_to_exact_repo_main_workflow_and_no_keys_are_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "calls.jsonl"
            fake = root / "gcloud"
            fake.write_text(
                "#!/usr/bin/env python3\nimport json,os,sys\nwith open(os.environ['TEST_CALL_LOG'],'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
            )
            fake.chmod(0o755)
            subprocess.run(
                ["bash", str(ROOT / "infra/setup_service_delivery.sh")],
                check=True,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "TEST_CALL_LOG": str(log),
                },
            )
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            for call in calls:
                self.assertIn("--project=proj-skkuls0106-183255377938", call)
                self.assertIn("--account=skkuls0106@skkukdp.re.kr", call)
                self.assertNotIn("keys", call)
                self.assertNotIn("--role=roles/owner", call)
                self.assertNotIn("--role=roles/editor", call)
                self.assertNotIn("--member=allUsers", call)
            provider = next(
                call
                for call in calls
                if call[:4] == ["iam", "workload-identity-pools", "providers", "update-oidc"]
            )
            condition = next(arg for arg in provider if arg.startswith("--attribute-condition="))
            for expected in (
                "1418330144",
                "338908553",
                "refs/heads/main",
                ".github/workflows/deploy-services.yml@refs/heads/main",
            ):
                self.assertIn(expected, condition)
            invoker = next(
                call
                for call in calls
                if call[:4] == ["run", "services", "add-iam-policy-binding", "team4-platform"]
            )
            self.assertIn(
                "--member=serviceAccount:web-runtime@proj-skkuls0106-183255377938.iam.gserviceaccount.com",
                invoker,
            )
            self.assertIn("--role=roles/run.invoker", invoker)


if __name__ == "__main__":
    unittest.main()
