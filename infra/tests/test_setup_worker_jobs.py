"""Worker 권한·설정 계획 검사. 클라우드를 호출하지 않는다."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "setup_worker_jobs", Path(__file__).parents[1] / "setup_worker_jobs.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
URL = "https://team4-platform-abc.a.run.app"


class WorkerSetupTests(unittest.TestCase):
    def test_prepare_creates_only_what_is_missing_and_pauses_the_schedule(self):
        commands = setup.prepare(set(), set(), URL)
        created = [c[3] for c in commands if c[:3] == ["iam", "service-accounts", "create"]]
        self.assertEqual(
            created, ["cs-worker-runtime", "tft-worker-runtime", "jobs-dispatch-invoker"]
        )
        verbs = [c[:3] for c in commands]
        self.assertLess(
            verbs.index(["scheduler", "jobs", "create"]),
            verbs.index(["scheduler", "jobs", "pause"]),
        )
        existing = {
            setup.email(n)
            for n in ("cs-worker-runtime", "tft-worker-runtime", "jobs-dispatch-invoker")
        }
        again = setup.prepare(existing, {setup.SCHEDULER}, URL)
        self.assertFalse([c for c in again if c[0] == "scheduler" or c[1] == "service-accounts"])

    def test_only_the_tft_worker_reads_the_document_bucket_and_cannot_write(self):
        commands = [c for c in setup.prepare(set(), set(), URL) if c[0] == "storage"]
        self.assertEqual(len(commands), 1)
        self.assertIn(setup.member("tft-worker-runtime"), commands[0])
        self.assertIn("--role=roles/storage.objectViewer", commands[0])

    def test_platform_may_run_only_the_two_jobs(self):
        jobs = [c for c in setup.grant() if c[:2] == ["run", "jobs"]]
        self.assertEqual([c[3] for c in jobs], ["cs-worker", "tft-worker"])
        for command in jobs:
            self.assertIn(setup.member("platform-runtime"), command)

    def test_enable_turns_on_both_owners_and_never_evaluation_admission(self):
        commands = setup.enable(URL, "123456789012")
        text = " ".join(" ".join(c) for c in commands)
        for name in (
            "TFT_JOBS_PROCESSOR_CONNECTED=true",
            "CS_JOBS_PROCESSOR_CONNECTED=true",
            "TFT_WORKER_JOBS_ENABLED=true",
            "CS_WORKER_JOBS_ENABLED=true",
            "TFT_PROCESSING_ENABLED=true",
            "CS_ANALYSIS_ENABLED=true",
            "JOBS_SWEEP_ENABLED=true",
        ):
            self.assertIn(name, text)
        self.assertNotIn("EVALUATION_LOCAL_ADMISSION", text)
        self.assertEqual(commands[-1][:3], ["scheduler", "jobs", "resume"])


if __name__ == "__main__":
    unittest.main()
