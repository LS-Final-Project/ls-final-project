"""KAN-74 메일 발송 큐·Scheduler 준비 명령을 가짜 gcloud로 확인한다. GCP는 바꾸지 않는다."""

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
    for name in os.environ.get("TEST_ACCOUNTS", "platform-runtime cs-runtime mail-sync-invoker").split():
        print(f"{name}@proj-skkuls0106-183255377938.iam.gserviceaccount.com")
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
            ["bash", str(ROOT / "infra/setup_mail_send.sh")],
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


class MailSendSetupTests(unittest.TestCase):
    def test_new_setup_grants_least_privilege_and_creates_a_paused_sweep(self):
        result, calls = run()
        self.assertEqual(result.returncode, 0, result.stderr)
        for call in calls:
            self.assertIn("--project=proj-skkuls0106-183255377938", call)
            self.assertIn("--account=skkuls0106@skkukdp.re.kr", call)
            self.assertNotIn("keys", call)
        heads = [call[:3] for call in calls]
        self.assertIn(["tasks", "queues", "create"], heads)
        queue = next(c for c in calls if c[:3] == ["tasks", "queues", "create"])
        self.assertIn("mail-send", queue)
        job = next(c for c in calls if c[:3] == ["scheduler", "jobs", "create"])
        self.assertIn(
            "--uri=https://team4-platform-abc.a.run.app/internal/v1/platform/mail/send-sweep", job
        )
        self.assertIn("--max-retry-attempts=3", job)
        self.assertEqual(
            heads.index(["scheduler", "jobs", "pause"]),
            heads.index(["scheduler", "jobs", "create"]) + 1,
        )
        grants = {
            (
                next(a for a in c if a.startswith("--member=")),
                next(a for a in c if a.startswith("--role=")),
            )
            for c in calls
            if "add-iam-policy-binding" in c
        }
        sa = "@proj-skkuls0106-183255377938.iam.gserviceaccount.com"
        self.assertEqual(
            grants,
            {
                (f"--member=serviceAccount:cs-runtime{sa}", "--role=roles/run.invoker"),
                (
                    f"--member=serviceAccount:platform-runtime{sa}",
                    "--role=roles/cloudtasks.enqueuer",
                ),
                (
                    f"--member=serviceAccount:platform-runtime{sa}",
                    "--role=roles/iam.serviceAccountUser",
                ),
            },
        )
        self.assertIn("MAIL_SEND_CS_CALLER_EMAIL=cs-runtime" + sa, result.stdout)

    def test_rerun_keeps_existing_queue_and_job(self):
        result, calls = run(TEST_EXISTING_QUEUE="mail-send", TEST_EXISTING_JOB="mail-send-sweep")
        self.assertEqual(result.returncode, 0, result.stderr)
        heads = [call[:3] for call in calls]
        for head in (
            ["tasks", "queues", "create"],
            ["scheduler", "jobs", "create"],
            ["scheduler", "jobs", "pause"],
            ["scheduler", "jobs", "resume"],
        ):
            self.assertNotIn(head, heads)

    def test_missing_invoker_account_stops_before_any_change(self):
        result, calls = run(TEST_ACCOUNTS="platform-runtime cs-runtime")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([c[:3] for c in calls], [["iam", "service-accounts", "list"]])


if __name__ == "__main__":
    unittest.main()
