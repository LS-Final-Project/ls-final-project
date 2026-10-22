"""Verify inference IAM scope and cleanup using fake commands, without touching GCP."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
PROJECT = "proj-skkuls0106-183255377938"
ACCOUNT = "skkuls0106@skkukdp.re.kr"


class VertexAccessTests(unittest.TestCase):
    def run_setup(self, verify=False, fail=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "calls.jsonl"
            executable = root / "gcloud"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import json,os,sys\n"
                "args=sys.argv[1:]\n"
                "with open(os.environ['TEST_CALL_LOG'],'a') as f:\n"
                " f.write(json.dumps([os.path.basename(sys.argv[0]),*args])+'\\n')\n"
                "if args[:2]==['services','list']: print('aiplatform.googleapis.com')\n"
                "if os.path.basename(sys.argv[0])=='uv' and os.environ['TEST_FAIL']=='1':\n"
                " sys.exit(9)\n"
            )
            executable.chmod(0o755)
            (root / "uv").symlink_to(executable)
            result = subprocess.run(
                ["bash", str(ROOT / "infra/setup_vertex_access.sh")]
                + (["--verify-runtime"] if verify else []),
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "TEST_CALL_LOG": str(log),
                    "TEST_FAIL": str(int(fail)),
                },
            )
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return result, calls

    def test_default_grants_only_two_inference_permissions_to_cs_and_tft(self):
        result, calls = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        role = next(c for c in calls if c[1:4] == ["iam", "roles", "create"])
        self.assertIn("--permissions=aiplatform.endpoints.predict,serviceusage.services.use", role)
        bindings = [c for c in calls if c[1:3] == ["projects", "add-iam-policy-binding"]]
        self.assertEqual(len(bindings), 2)
        self.assertEqual(
            {arg for c in bindings for arg in c if arg.startswith("--member=")},
            {
                f"--member=serviceAccount:{service}-runtime@{PROJECT}.iam.gserviceaccount.com"
                for service in ("cs", "tft")
            },
        )
        for call in calls:
            self.assertIn(f"--project={PROJECT}", call)
            self.assertIn(f"--account={ACCOUNT}", call)
            self.assertNotIn("keys", call)
            self.assertNotIn("service-accounts", call)
            self.assertNotIn("enable", call)

    def test_verification_removes_only_the_exact_temporary_conditional_bindings(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                result, calls = self.run_setup(verify=True, fail=fail)
                self.assertEqual(result.returncode, 9 if fail else 0, result.stderr)
                additions = [
                    c
                    for c in calls
                    if c[1:4] == ["iam", "service-accounts", "add-iam-policy-binding"]
                ]
                removals = [
                    c
                    for c in calls
                    if c[1:4] == ["iam", "service-accounts", "remove-iam-policy-binding"]
                ]
                self.assertEqual(len(additions), 2)
                self.assertEqual(len(removals), len(additions))
                for added, removed in zip(additions, removals, strict=True):
                    self.assertEqual(added[4:], removed[4:])
                    self.assertIn(f"--member=user:{ACCOUNT}", added)
                    self.assertIn("--role=roles/iam.serviceAccountTokenCreator", added)
                    condition = next(a for a in added if a.startswith("--condition="))
                    self.assertIn("request.time<timestamp(", condition)
                    self.assertIn("title=team4-vertex-setup-verification", condition)
                probes = [c for c in calls if c[0] == "uv"]
                self.assertEqual(len(probes), 1 if fail else 2)
                for probe in probes:
                    self.assertIn(f"--gcloud-account={ACCOUNT}", probe)
                    self.assertIn("--model=gemini-3.6-flash", probe)


if __name__ == "__main__":
    unittest.main()
