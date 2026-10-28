"""KAN-72 메일 수집 큐·Scheduler 준비 명령을 가짜 gcloud로 확인한다. GCP는 바꾸지 않는다."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
FAKE = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["TEST_CALL_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if args[:3] == ["iam", "service-accounts", "list"]:
    print("platform-runtime@proj-skkuls0106-183255377938.iam.gserviceaccount.com")
    print(os.environ.get("TEST_EXISTING_INVOKER", ""))
elif args[:3] == ["run", "services", "describe"]:
    print(os.environ.get("TEST_SERVICE_URL", "https://team4-platform-abc.a.run.app"))
elif args[:3] == ["tasks", "queues", "list"]:
    print(os.environ.get("TEST_EXISTING_QUEUE", ""))
elif args[:3] == ["scheduler", "jobs", "list"]:
    print(os.environ.get("TEST_EXISTING_JOB", ""))
"""


def run(**env):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        log = root / "calls.jsonl"
        fake = root / "gcloud"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        result = subprocess.run(
            ["bash", str(ROOT / "infra/setup_mail_sync.sh")],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "TEST_CALL_LOG": str(log),
                **env,
            },
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls


class MailSyncSetupTests(unittest.TestCase):
    def test_new_setup_creates_least_privilege_resources_and_a_paused_job(self):
        result, calls = run()
        self.assertEqual(result.returncode, 0, result.stderr)
        for call in calls:
            self.assertIn("--project=proj-skkuls0106-183255377938", call)
            self.assertIn("--account=skkuls0106@skkukdp.re.kr", call)
            self.assertNotIn("keys", call)
            for broad in ("roles/owner", "roles/editor", "roles/run.admin"):
                self.assertNotIn(f"--role={broad}", call)
        heads = [call[:3] for call in calls]
        self.assertIn(["iam", "service-accounts", "create"], heads)
        self.assertIn(["tasks", "queues", "create"], heads)
        create = next(c for c in calls if c[:3] == ["scheduler", "jobs", "create"])
        self.assertIn(
            "--uri=https://team4-platform-abc.a.run.app/internal/v1/platform/mail/dispatch", create
        )
        self.assertIn("--oidc-token-audience=https://team4-platform-abc.a.run.app", create)
        self.assertIn(
            "--oidc-service-account-email=mail-sync-invoker@proj-skkuls0106-183255377938.iam.gserviceaccount.com",
            create,
        )
        self.assertEqual(
            heads.index(["scheduler", "jobs", "pause"]),
            heads.index(["scheduler", "jobs", "create"]) + 1,
        )
        roles = {
            next(a for a in c if a.startswith("--role="))
            for c in calls
            if any(a.startswith("--role=") for a in c)
        }
        self.assertEqual(
            roles,
            {
                "--role=roles/run.invoker",
                "--role=roles/cloudtasks.enqueuer",
                "--role=roles/iam.serviceAccountUser",
            },
        )
        self.assertIn(
            "MAIL_SYNC_QUEUE=projects/proj-skkuls0106-183255377938/locations/asia-northeast3/queues/mail-sync",
            result.stdout,
        )

    def test_rerun_keeps_existing_resources_and_does_not_resume_the_job(self):
        result, calls = run(
            TEST_EXISTING_INVOKER="mail-sync-invoker@proj-skkuls0106-183255377938.iam.gserviceaccount.com",
            TEST_EXISTING_QUEUE="mail-sync",
            TEST_EXISTING_JOB="mail-sync-dispatch",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        heads = [call[:3] for call in calls]
        for head in (
            ["iam", "service-accounts", "create"],
            ["tasks", "queues", "create"],
            ["scheduler", "jobs", "create"],
            ["scheduler", "jobs", "pause"],
            ["scheduler", "jobs", "resume"],
        ):
            self.assertNotIn(head, heads)

    def test_unusable_service_url_stops_before_any_grant(self):
        result, calls = run(TEST_SERVICE_URL="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse([c for c in calls if "add-iam-policy-binding" in c])


if __name__ == "__main__":
    unittest.main()
